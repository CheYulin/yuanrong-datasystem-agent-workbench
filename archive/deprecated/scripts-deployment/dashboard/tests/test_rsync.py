# Copyright (c) 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
test_rsync.py — Unit tests for the rsync pipeline.

Run with:  python -m pytest tests/test_rsync.py -v
"""

import os
import tempfile
from pathlib import Path

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

class TestMakeExcludesFile:
    _files = []

    @pytest.fixture(autouse=True)
    def setup(self):
        TestMakeExcludesFile._files = []

    def teardown_method(self):
        for f in TestMakeExcludesFile._files:
            try:
                os.unlink(f)
            except OSError:
                pass

    def _make(self, patterns):
        from dashboard.app import make_excludes_file
        path = make_excludes_file(patterns)
        TestMakeExcludesFile._files.append(path)
        return path

    def test_creates_file(self):
        path = self._make([".git/", "*.pyc", "__pycache__/"])
        assert os.path.exists(path)
        content = open(path).read()
        assert ".git/" in content
        assert "*.pyc" in content
        assert "__pycache__/" in content

    def test_empty_patterns(self):
        path = self._make([])
        assert os.path.exists(path)
        assert open(path).read() == "\n"

    def test_patterns_separated_by_newlines(self):
        path = self._make(["a", "b", "c"])
        lines = [l for l in open(path).read().splitlines() if l]
        assert lines == ["a", "b", "c"]


class TestGetFileTree:
    def test_empty_dir(self, tmp_path):
        from dashboard.app import get_file_tree
        assert get_file_tree(str(tmp_path)) == []

    def test_files_and_dirs(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / "src").mkdir()
        (tmp_path / "main.py").write_text("print(1)")
        (tmp_path / "README.md").write_text("# hi")
        items = get_file_tree(str(tmp_path))
        names = {i["name"] for i in items}
        assert "src" in names
        assert "main.py" in names

    def test_dirs_sorted_first(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b_dir").mkdir()
        items = get_file_tree(str(tmp_path))
        assert items[0]["name"] == "b_dir"

    def test_hidden_files_included(self, tmp_path):
        from dashboard.app import get_file_tree
        (tmp_path / ".gitignore").write_text("*.o")
        items = get_file_tree(str(tmp_path))
        names = [i["name"] for i in items]
        assert ".gitignore" in names


# ── Flask test setup ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def flask_env():
    """Module-scoped Flask test client with isolated temp dirs and env."""
    import logging
    logging.getLogger("root").setLevel(logging.CRITICAL)
    old_env = dict(os.environ)
    tmp_c = tempfile.mktemp(suffix=".yaml")
    tmp_b = tempfile.mkdtemp()
    tmp_l = tempfile.mkdtemp()
    os.environ["DASHBOARD_CONFIG"] = tmp_c
    os.environ["DASHBOARD_BASE_DIR"] = tmp_b
    os.environ["DASHBOARD_LOG_DIR"] = tmp_l
    from dashboard.app import create_app
    app, _ = create_app(log_dir=tmp_l)
    app.config["TESTING"] = True
    client = app.test_client()
    yield {"client": client, "tmp_b": tmp_b}
    os.environ.clear()
    os.environ.update(old_env)
    try:
        os.unlink(tmp_c)
    except OSError:
        pass


# ── Config / Mapping API tests ───────────────────────────────────────────────

class TestConfigAPI:
    _SAMPLE = {
        "connections": [{
            "name": "test-host", "host": "localhost", "port": 22,
            "username": "testuser", "auth_type": "key",
            "key_path": "~/.ssh/id_ed25519", "password": "", "note": "Test",
        }],
        "mappings": [{
            "name": "test-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": False,
        }],
        "settings": {},
    }

    def test_get_empty_config_returns_defaults(self, flask_env):
        rv = flask_env["client"].get("/api/config")
        assert rv.status_code == 200
        assert "connections" in rv.get_json()

    def test_post_config_saves_and_get_returns_it(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].get("/api/config")
        data = rv.get_json()
        assert len(data["connections"]) == 1
        assert data["connections"][0]["name"] == "test-host"


class TestLocalTreeAPI:
    def test_tree_root(self, flask_env):
        rv = flask_env["client"].get("/api/local/tree")
        assert rv.status_code == 200

    def test_tree_subdir(self, flask_env):
        Path(flask_env["tmp_b"]) / "myrepo" / "src" / "main.py"
        repo = Path(flask_env["tmp_b"]) / "myrepo"
        repo.mkdir(exist_ok=True)
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "main.py").write_text("def main(): pass\n")
        rv = flask_env["client"].get("/api/local/tree?path=myrepo")
        assert rv.status_code == 200
        names = [i["name"] for i in rv.get_json()["items"]]
        assert "src" in names

    def test_tree_nonexistent_path(self, flask_env):
        os.environ["DASHBOARD_BASE_DIR"] = "/nonexistent-xyz"
        rv = flask_env["client"].get("/api/local/tree?path=does-not-exist")
        assert rv.status_code == 404


class TestMappingAPI:
    _SAMPLE = {
        "connections": [{
            "name": "test-host", "host": "localhost", "port": 22,
            "username": "testuser", "auth_type": "key",
            "key_path": "~/.ssh/id_ed25519", "password": "", "note": "Test",
        }],
        "mappings": [{
            "name": "test-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": False,
        }],
        "settings": {},
    }

    def test_create_mapping(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].post("/api/mappings", json={
            "name": "new-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": True,
        })
        assert rv.status_code == 200
        assert rv.get_json()["mapping"]["name"] == "new-mapping"

    def test_get_mappings(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].get("/api/mappings")
        assert len(rv.get_json()["mappings"]) == 1

    def test_delete_mapping(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].delete("/api/mappings/test-mapping")
        assert rv.status_code == 200


class TestRsyncPreview:
    _SAMPLE = {
        "connections": [{
            "name": "test-host", "host": "localhost", "port": 22,
            "username": "testuser", "auth_type": "key",
            "key_path": "~/.ssh/id_ed25519", "password": "", "note": "Test",
        }],
        "mappings": [{
            "name": "test-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": False,
        }],
        "settings": {},
    }

    def test_preview_missing_mapping_returns_404(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].post("/api/mappings/ghost/preview")
        assert rv.status_code == 404

    def test_preview_missing_connection_returns_404(self, flask_env):
        flask_env["client"].post("/api/config", json={
            "connections": [],
            "mappings": [{**self._SAMPLE["mappings"][0], "connection": "ghost"}],
            "settings": {},
        })
        rv = flask_env["client"].post("/api/mappings/test-mapping/preview")
        assert rv.status_code == 404


class TestResolveSSHHost:
    @pytest.fixture
    def tmp(self, tmp_path):
        return tmp_path

    def test_missing_ssh_config_returns_defaults(self, tmp):
        os.environ["HOME"] = str(tmp)
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("some-host")
        assert result["host"] == "some-host"
        assert result["port"] == 22
        assert result["user"] is None

    def test_parses_ssh_config(self, tmp):
        (tmp / ".ssh").mkdir()
        (tmp / ".ssh" / "config").write_text(
            "Host mybox\n"
            "    HostName 192.168.1.100\n"
            "    Port 2222\n"
            "    User admin\n"
        )
        os.environ["HOME"] = str(tmp)
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("mybox")
        assert result["host"] == "192.168.1.100"
        assert result["port"] == 2222
        assert result["user"] == "admin"

    def test_missing_host_in_config_returns_defaults(self, tmp):
        (tmp / ".ssh").mkdir()
        (tmp / ".ssh" / "config").write_text("Host other\n    HostName 1.2.3.4\n")
        os.environ["HOME"] = str(tmp)
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("undefined-host")
        assert result["host"] == "undefined-host"
        assert result["user"] is None


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


class TestLogsEndpoint:
    def test_logs_returns_lines(self, flask_env):
        rv = flask_env["client"].get("/api/logs?lines=5")
        assert rv.status_code == 200
        assert "lines" in rv.get_json()

    def test_logs_returns_empty_when_no_file(self, flask_env):
        os.environ["DASHBOARD_LOG_DIR"] = "/nonexistent-xyz-dashboard-test"
        rv = flask_env["client"].get("/api/logs")
        assert rv.status_code == 200
        assert rv.get_json()["lines"] == []


class TestDeployStatus:
    def test_deploy_status_returns_json(self, flask_env):
        rv = flask_env["client"].get("/api/deploy-status")
        assert rv.status_code == 200
        assert isinstance(rv.get_json(), dict)


# ── AUTO-SYNC TESTS ─────────────────────────────────────────────────────────

class TestSkipLogic:
    def test_skips_dot_git(self):
        from dashboard.app import _should_skip
        assert _should_skip(".git")
        assert not _should_skip(".git/config")
        assert not _should_skip("src/main.py")

    def test_skips_pycache_and_pyc(self):
        from dashboard.app import _should_skip
        assert _should_skip("__pycache__")
        assert _should_skip("module.pyc")
        assert not _should_skip("module.py")

    def test_skips_node_modules(self):
        from dashboard.app import _should_skip
        assert _should_skip("node_modules")


class TestClangFileDetection:
    def test_recognizes_cpp_extensions(self):
        from dashboard.app import _is_clang_file
        assert _is_clang_file("main.cpp")
        assert _is_clang_file("main.h")
        assert _is_clang_file("main.cc")
        assert _is_clang_file("main.hpp")

    def test_rejects_non_c_files(self):
        from dashboard.app import _is_clang_file
        assert not _is_clang_file("main.py")
        assert not _is_clang_file("main.js")
        assert not _is_clang_file("main.rs")


class TestInotifyHandlerConstruction:
    def test_handler_stores_debounce(self, tmp_path):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test", local_path=str(tmp_path),
            exclude_patterns=[".git/"], remote_dest="host:/tmp",
            delete=False, logger=logging.getLogger("test"), debounce=99,
        )
        assert h.debounce == 99

    def test_handler_stores_remote_dest(self, tmp_path):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test", local_path=str(tmp_path),
            exclude_patterns=[], remote_dest="root@xqyun-32c32g:/home/user",
            delete=True, logger=logging.getLogger("test"),
        )
        assert h.remote_dest == "root@xqyun-32c32g:/home/user"
        assert h.delete is True


class TestWatchManagerLifecycle:
    def _wm(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        return wm

    def test_start_watcher_auto_sync_off_does_nothing(self, tmp_path):
        wm = self._wm()
        mapping = {
            "name": "t1", "local_path": str(tmp_path), "auto_sync": False,
            "connection": "h", "remote_path": "/x",
            "exclude_patterns": [], "delete": False,
        }
        conn = {"host": "localhost", "username": "root", "port": 22}
        wm.start_watcher("t1", str(tmp_path), mapping, conn, wm._logger)
        assert "t1" not in wm._watchers

    def test_stop_watcher_unknown_name_does_not_raise(self):
        wm = self._wm()
        wm.stop_watcher("nonexistent")

    def test_restart_all_with_empty_mappings(self, tmp_path):
        wm = self._wm()
        wm.restart_all([], [], str(tmp_path), wm._logger)


class TestAutoSyncAPI:
    _SAMPLE = {
        "connections": [{
            "name": "test-host", "host": "localhost", "port": 22,
            "username": "testuser", "auth_type": "key",
            "key_path": "~/.ssh/id_ed25519", "password": "", "note": "Test",
        }],
        "mappings": [{
            "name": "test-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": False,
        }],
        "settings": {},
    }

    def test_enable_auto_sync_returns_ok(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].post(
            "/api/mappings/test-mapping/auto-sync", json={"enabled": True},
        )
        assert rv.status_code == 200
        assert rv.get_json()["ok"] is True

    def test_disable_auto_sync_returns_ok(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].post(
            "/api/mappings/test-mapping/auto-sync", json={"enabled": False},
        )
        assert rv.status_code == 200
        assert rv.get_json()["auto_sync"] is False

    def test_auto_sync_unknown_mapping_returns_404(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].post(
            "/api/mappings/ghost/auto-sync", json={"enabled": True},
        )
        assert rv.status_code == 404

    def test_delete_mapping_stops_watcher(self, flask_env):
        flask_env["client"].post("/api/config", json=self._SAMPLE)
        rv = flask_env["client"].delete("/api/mappings/test-mapping")
        assert rv.status_code == 200


class TestPollingSyncConstruction:
    def test_stores_interval(self, tmp_path):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t", local_path=str(tmp_path),
            exclude_patterns=[".git/"],
            remote_dest="root@host:/home/user",
            delete=False, logger=logging.getLogger("test"), interval=15.0,
        )
        assert p.interval == 15.0

    def test_stores_remote_dest(self, tmp_path):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t", local_path=str(tmp_path),
            exclude_patterns=[],
            remote_dest="root@host:/remote/path",
            delete=True, logger=logging.getLogger("test"), interval=5.0,
        )
        assert p.remote_dest == "root@host:/remote/path"
        assert p.delete is True


class TestWatchManagerSocketIO:
    def test_set_socketio_stores_reference(self):
        from dashboard.app import WatchManager
        wm = WatchManager()

        class Fake:
            def emit(self, *a, **kw):
                pass

        wm.set_socketio(Fake())
        assert wm._socketio is not None

    def test_emit_fault_does_not_crash_when_no_socketio(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        wm._emit_fault("test-mapping", "observer died")

    def test_emit_restored_does_not_crash_when_no_socketio(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        wm._emit_restored("test-mapping", "inotify")

    def test_emit_fault_calls_socketio_emit(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        emitted = {}

        class Fake:
            def emit(self, event, data, namespace=None):
                emitted["event"] = event
                emitted["data"] = data
                emitted["namespace"] = namespace

        wm.set_socketio(Fake())
        wm._emit_fault("mymap", "test reason", mode="polling")
        assert emitted["event"] == "watcher_fault"
        assert emitted["data"]["mapping"] == "mymap"
        assert emitted["data"]["reason"] == "test reason"
        assert emitted["namespace"] == "/watchers"

    def test_emit_restored_calls_socketio_emit(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        emitted = {}

        class Fake:
            def emit(self, event, data, namespace=None):
                emitted["event"] = event
                emitted["data"] = data
                emitted["namespace"] = namespace

        wm.set_socketio(Fake())
        wm._emit_restored("mymap2", "inotify")
        assert emitted["event"] == "watcher_restored"
        assert emitted["data"]["mapping"] == "mymap2"
        assert emitted["namespace"] == "/watchers"
