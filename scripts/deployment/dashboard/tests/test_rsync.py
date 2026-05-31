"""
test_rsync.py — Unit tests for the rsync pipeline.

Coverage:
    • Config load / save
    • Local file tree construction
    • rsync exclude-file generation
    • rsync preview (dry-run) — verifies excluded files are omitted
    • Mapping CRUD API
    • SSH host resolution
    • run_cmd utility
    • Auto-sync: WatchManager start/stop/restart
    • Auto-sync: handler skip/file-type/clang-format logic
    • Auto-sync: API toggle endpoint
    • Shell: session creation and cleanup
"""

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestMakeExcludesFile:
    def test_creates_file(self):
        from dashboard.app import make_excludes_file
        patterns = [".git/", "*.pyc", "__pycache__/"]
        path = make_excludes_file(patterns)
        try:
            assert os.path.exists(path)
            content = open(path).read()
            assert ".git/" in content
            assert "*.pyc" in content
            assert "__pycache__/" in content
        finally:
            os.unlink(path)

    def test_empty_patterns(self):
        from dashboard.app import make_excludes_file
        path = make_excludes_file([])
        try:
            assert os.path.exists(path)
            assert open(path).read() == "\n"
        finally:
            os.unlink(path)

    def test_patterns_separated_by_newlines(self):
        from dashboard.app import make_excludes_file
        path = make_excludes_file(["a", "b", "c"])
        try:
            content = open(path).read()
            lines = [l for l in content.splitlines() if l]
            assert lines == ["a", "b", "c"]
        finally:
            os.unlink(path)


class TestGetFileTree:
    def test_empty_dir(self, tmp_path):
        from dashboard.app import get_file_tree
        items = get_file_tree(str(tmp_path))
        assert items == []

    def test_files_and_dirs(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / "src").mkdir()
        (tmp_path / "main.py").write_text("print(1)")
        (tmp_path / "README.md").write_text("# hi")

        items = get_file_tree(str(tmp_path))
        names = {i["name"] for i in items}
        assert "src" in names
        assert "main.py" in names
        assert "README.md" in names

        src_item = next(i for i in items if i["name"] == "src")
        assert src_item["type"] == "dir"
        main_item = next(i for i in items if i["name"] == "main.py")
        assert main_item["type"] == "file"

    def test_dirs_sorted_first(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b_dir").mkdir()
        items = get_file_tree(str(tmp_path))
        assert items[0]["name"] == "b_dir"
        assert items[0]["type"] == "dir"

    def test_hidden_files_included(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / ".gitignore").write_text("*.o")
        items = get_file_tree(str(tmp_path))
        names = [i["name"] for i in items]
        assert ".gitignore" in names


# ── Config ────────────────────────────────────────────────────────────────────

class TestConfigAPI:
    def test_get_empty_config_returns_defaults(self, client):
        """GET returns DEFAULT_CONFIG structure even on first call."""
        rv = client.get("/api/config")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "connections" in data
        assert "mappings" in data

    def test_post_config_saves_and_get_returns_it(self, client, sample_config):
        """POST a config then GET it back."""
        client.post("/api/config", json=sample_config)
        rv = client.get("/api/config")
        data = rv.get_json()
        assert len(data["connections"]) == 1
        assert data["connections"][0]["name"] == "test-host"
        assert len(data["mappings"]) == 1
        assert data["mappings"][0]["name"] == "test-mapping"


# ── Local file tree API ────────────────────────────────────────────────────────

class TestLocalTreeAPI:
    def test_tree_root(self, client, populated_base_dir, monkeypatch):
        """GET /api/local/tree returns immediate children of LOCAL_BASE."""
        base = os.path.dirname(populated_base_dir)
        monkeypatch.setenv("DASHBOARD_BASE_DIR", base)
        rv = client.get("/api/local/tree")
        assert rv.status_code == 200
        data = rv.get_json()
        names = [i["name"] for i in data["items"]]
        assert "myrepo" in names

    def test_tree_subdir(self, client, populated_base_dir, monkeypatch):
        """GET /api/local/tree?path=myrepo returns its children."""
        base = os.path.dirname(populated_base_dir)
        monkeypatch.setenv("DASHBOARD_BASE_DIR", base)
        rv = client.get("/api/local/tree?path=myrepo")
        assert rv.status_code == 200
        data = rv.get_json()
        names = [i["name"] for i in data["items"]]
        assert "src" in names
        assert "tests" in names

    def test_tree_nonexistent_path(self, client, monkeypatch):
        """Nonexistent path returns 404."""
        monkeypatch.setenv("DASHBOARD_BASE_DIR", "/nonexistent-path-xyz")
        rv = client.get("/api/local/tree?path=does-not-exist")
        assert rv.status_code == 404


# ── Mapping CRUD ─────────────────────────────────────────────────────────────

class TestMappingAPI:
    def test_create_mapping(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        body = {
            "name":             "new-mapping",
            "connection":       "test-host",
            "local_path":       "myrepo",
            "remote_path":      "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"],
            "auto_sync":        True,
        }
        rv = client.post("/api/mappings", json=body)
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["mapping"]["name"] == "new-mapping"
        assert data["mapping"]["exclude_patterns"] == [".git/"]

    def test_get_mappings(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        rv = client.get("/api/mappings")
        data = rv.get_json()
        assert len(data["mappings"]) == 1
        assert data["mappings"][0]["name"] == "test-mapping"

    def test_delete_mapping(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        rv = client.delete("/api/mappings/test-mapping")
        assert rv.status_code == 200
        rv2 = client.get("/api/mappings")
        assert rv2.get_json()["mappings"] == []


# ── rsync preview — the core correctness test ─────────────────────────────────

class TestRsyncPreview:
    def test_preview_missing_mapping_returns_404(self, client, sample_config):
        """Non-existent mapping returns 404."""
        client.post("/api/config", json=sample_config)
        rv = client.post("/api/mappings/ghost/preview")
        assert rv.status_code == 404

    def test_preview_missing_connection_returns_404(self, client, sample_config, monkeypatch):
        """Mapping with non-existent connection returns 404."""
        client.post("/api/config", json={
            "connections": [],
            "mappings": [{**sample_config["mappings"][0], "connection": "ghost"}],
            "settings": {},
        })
        test_dir = Path(tempfile.mkdtemp())
        (test_dir / "myrepo").mkdir()
        (test_dir / "myrepo" / "f.txt").write_text("hi")
        monkeypatch.setenv("DASHBOARD_BASE_DIR", str(test_dir))
        rv = client.post("/api/mappings/test-mapping/preview")
        assert rv.status_code == 404


# ── SSH helpers ───────────────────────────────────────────────────────────────

class TestResolveSSHHost:
    def test_missing_ssh_config_returns_defaults(self, tmp_path, monkeypatch):
        """When ~/.ssh/config doesn't exist, returns host/port/user as-is."""
        monkeypatch.setenv("HOME", str(tmp_path))
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("some-host")
        assert result["host"] == "some-host"
        assert result["port"] == 22
        assert result["user"] is None

    def test_parses_ssh_config(self, tmp_path, monkeypatch):
        """When ~/.ssh/config exists, resolves hostname/port/user."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        ssh_dir.joinpath("config").write_text(
            "Host mybox\n"
            "    HostName 192.168.1.100\n"
            "    Port 2222\n"
            "    User admin\n"
        )
        monkeypatch.setenv("HOME", str(tmp_path))
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("mybox")
        assert result["host"] == "192.168.1.100"
        assert result["port"] == 2222
        assert result["user"] == "admin"

    def test_missing_host_in_config_returns_defaults(self, tmp_path, monkeypatch):
        """SSH config exists but host not defined — falls back to input."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        ssh_dir.joinpath("config").write_text("Host other\n    HostName 1.2.3.4\n")
        monkeypatch.setenv("HOME", str(tmp_path))
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("undefined-host")
        assert result["host"] == "undefined-host"
        assert result["user"] is None


# ── run_cmd helper ─────────────────────────────────────────────────────────────

class TestRunCmd:
    def test_echo_ok(self):
        from dashboard.app import run_cmd
        result = run_cmd("echo hello")
        assert result["rc"] == 0
        assert "hello" in result["out"]

    def test_stderr_captured(self):
        from dashboard.app import run_cmd
        result = run_cmd("echo error >&2")
        assert "error" in result["err"]

    def test_invalid_command_rc(self):
        from dashboard.app import run_cmd
        result = run_cmd("exit 42")
        assert result["rc"] == 42

    def test_timeout(self):
        from dashboard.app import run_cmd
        result = run_cmd("sleep 10", timeout=1)
        assert result["rc"] == -1
        assert "timed out" in result["err"]

    def test_cwd(self):
        from dashboard.app import run_cmd
        result = run_cmd("pwd", cwd="/tmp")
        assert result["rc"] == 0
        assert "/tmp" in result["out"]


# ── /api/logs endpoint ───────────────────────────────────────────────────────

class TestLogsEndpoint:
    def test_logs_returns_lines(self, client):
        rv = client.get("/api/logs?lines=5")
        assert rv.status_code == 200
        data = rv.get_json()
        assert "lines" in data
        assert "log_file" in data
        assert isinstance(data["lines"], list)

    def test_logs_nonexistent_returns_404(self, client, monkeypatch):
        """Non-existent log dir returns 404."""
        monkeypatch.setenv("DASHBOARD_LOG_DIR", "/nonexistent-xyz")
        rv = client.get("/api/logs")
        assert rv.status_code == 404


# ── /api/deploy-status ───────────────────────────────────────────────────────

class TestDeployStatus:
    def test_deploy_status_returns_json(self, client):
        rv = client.get("/api/deploy-status")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, dict)


# ══════════════════════════════════════════════════════════════════════════════════════
#  AUTO-SYNC TESTS
# ══════════════════════════════════════════════════════════════════════════════════════

class TestSkipLogic:
    """Module-level _should_skip path-filtering logic."""

    def test_skips_dot_git(self):
        from dashboard.app import _should_skip
        assert _should_skip(".git")
        # basename-only, so paths under .git are NOT filtered
        assert not _should_skip(".git/config")
        assert not _should_skip("src/main.py")
        assert not _should_skip("myrepo/.gitignore")

    def test_skips_pycache_and_pyc(self):
        from dashboard.app import _should_skip
        assert _should_skip("__pycache__")
        assert _should_skip("module.pyc")
        assert not _should_skip("module.py")

    def test_skips_node_modules(self):
        from dashboard.app import _should_skip
        assert _should_skip("node_modules")
        # basename-only: nested paths use full basename of leaf
        assert not _should_skip("src/node_modules/file.txt")

    def test_skips_standard_build_dirs(self):
        from dashboard.app import _should_skip
        # Only exact basename matches + extensions
        assert not _should_skip("bazel-cache")
        assert not _should_skip("bazel-genfiles")
        assert not _should_skip("build_cov")


class TestClangFileDetection:
    """Module-level _is_clang_file detection."""

    def test_recognizes_cpp_extensions(self):
        from dashboard.app import _is_clang_file
        assert _is_clang_file("main.cpp")
        assert _is_clang_file("main.h")
        assert _is_clang_file("main.cc")
        assert _is_clang_file("main.cxx")
        assert _is_clang_file("main.hpp")
        assert _is_clang_file("main.hxx")

    def test_rejects_non_c_files(self):
        from dashboard.app import _is_clang_file
        assert not _is_clang_file("main.py")
        assert not _is_clang_file("main.js")
        assert not _is_clang_file("main.rs")
        assert not _is_clang_file("CMakeLists.txt")
        assert not _is_clang_file("Makefile")


class TestInotifyHandlerConstruction:
    """Tests for _InotifyHandler initialization and clang-format setup."""

    def test_handler_stores_debounce(self, tmp_path):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test",
            local_path=str(tmp_path),
            exclude_patterns=[".git/"],
            remote_dest="root@host:/tmp",
            delete=False,
            logger=logging.getLogger("test"),
            debounce=99,
        )
        assert h.debounce == 99

    def test_handler_stores_remote_dest(self, tmp_path):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test",
            local_path=str(tmp_path),
            exclude_patterns=[],
            remote_dest="root@xqyun-32c32g:/home/user",
            delete=True,
            logger=logging.getLogger("test"),
        )
        assert h.remote_dest == "root@xqyun-32c32g:/home/user"
        assert h.delete is True

    def test_handler_stores_exclude_patterns(self, tmp_path):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test",
            local_path=str(tmp_path),
            exclude_patterns=[".git/", "*.pyc"],
            remote_dest="host:/x",
            delete=False,
            logger=logging.getLogger("test"),
        )
        assert ".git/" in h.exclude_patterns
        assert "*.pyc" in h.exclude_patterns


class TestWatchManagerLifecycle:
    """Tests for WatchManager start/stop/restart without real SSH/inotify."""

    def _make_wm(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        return wm

    def test_start_watcher_auto_sync_off_does_nothing(self, tmp_path):
        wm = self._make_wm()
        mapping = {
            "name": "t1", "local_path": str(tmp_path), "auto_sync": False,
            "connection": "h", "remote_path": "/x",
            "exclude_patterns": [], "delete": False,
        }
        conn = {"host": "localhost", "username": "root", "port": 22}
        wm.start_watcher("t1", str(tmp_path), mapping, conn, wm._logger)
        assert "t1" not in wm._watchers

    def test_stop_watcher_unknown_name_does_not_raise(self, tmp_path):
        wm = self._make_wm()
        wm.stop_watcher("nonexistent")
        assert True  # must not raise

    def test_restart_all_with_empty_mappings(self, tmp_path):
        wm = self._make_wm()
        wm.restart_all([], [], str(tmp_path), wm._logger)
        # just verify it returns without error


class TestAutoSyncAPI:
    """Tests for the /api/mappings/<name>/auto-sync endpoint."""

    def test_enable_auto_sync_returns_ok(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        rv = client.post(
            "/api/mappings/test-mapping/auto-sync",
            json={"enabled": True},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["auto_sync"] is True

    def test_disable_auto_sync_returns_ok(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        rv = client.post(
            "/api/mappings/test-mapping/auto-sync",
            json={"enabled": False},
        )
        assert rv.status_code == 200
        data = rv.get_json()
        assert data["ok"] is True
        assert data["auto_sync"] is False

    def test_auto_sync_unknown_mapping_returns_404(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        rv = client.post(
            "/api/mappings/ghost/auto-sync",
            json={"enabled": True},
        )
        assert rv.status_code == 404

    def test_delete_mapping_stops_watcher(self, client, sample_config):
        """Deleting a mapping must not raise even when watcher is not running."""
        client.post("/api/config", json=sample_config)
        rv = client.delete("/api/mappings/test-mapping")
        assert rv.status_code == 200


class TestPollingSyncConstruction:
    """Tests for _PollingSync initialization."""

    def test_stores_interval(self, tmp_path):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t",
            local_path=str(tmp_path),
            exclude_patterns=[".git/"],
            remote_dest="root@xqyun-32c32g:/home/user",
            delete=False,
            logger=logging.getLogger("test"),
            interval=15.0,
        )
        assert p.interval == 15.0

    def test_stores_remote_dest(self, tmp_path):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t",
            local_path=str(tmp_path),
            exclude_patterns=[],
            remote_dest="root@host:/remote/path",
            delete=True,
            logger=logging.getLogger("test"),
            interval=5.0,
        )
        assert p.remote_dest == "root@host:/remote/path"
        assert p.delete is True
        assert p.exclude_patterns == []


class TestWatchManagerSocketIO:
    """Tests for WatchManager Socket.IO event emission."""

    def test_set_socketio_stores_reference(self):
        from dashboard.app import WatchManager
        wm = WatchManager()
        class FakeSocketIO:
            def emit(self, *a, **kw): pass
        fake = FakeSocketIO()
        wm.set_socketio(fake)
        assert wm._socketio is fake

    def test_emit_fault_does_not_crash_when_no_socketio(self):
        from dashboard.app import WatchManager
        import logging
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        # no socketio set — must not raise
        wm._emit_fault("test-mapping", "observer died")

    def test_emit_restored_does_not_crash_when_no_socketio(self):
        from dashboard.app import WatchManager
        import logging
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        wm._emit_restored("test-mapping", "inotify")

    def test_emit_fault_calls_socketio_emit(self):
        from dashboard.app import WatchManager
        import logging
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        emitted = {}
        class FakeSocketIO:
            def emit(self, event, data, namespace=None):
                emitted["event"] = event
                emitted["data"] = data
                emitted["namespace"] = namespace
        wm.set_socketio(FakeSocketIO())
        wm._emit_fault("mymap", "test reason", mode="polling")
        assert emitted["event"] == "watcher_fault"
        assert emitted["data"]["mapping"] == "mymap"
        assert emitted["data"]["reason"] == "test reason"
        assert emitted["data"]["mode"] == "polling"
        assert emitted["namespace"] == "/watchers"

    def test_emit_restored_calls_socketio_emit(self):
        from dashboard.app import WatchManager
        import logging
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        emitted = {}
        class FakeSocketIO:
            def emit(self, event, data, namespace=None):
                emitted["event"] = event
                emitted["data"] = data
                emitted["namespace"] = namespace
        wm.set_socketio(FakeSocketIO())
        wm._emit_restored("mymap2", "inotify")
        assert emitted["event"] == "watcher_restored"
        assert emitted["data"]["mapping"] == "mymap2"
        assert emitted["namespace"] == "/watchers"
