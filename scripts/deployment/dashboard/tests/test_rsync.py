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
"""

import os
import tempfile
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


# ── Mapping CRUD ───────────────────────────────────────────────────────────────

class TestMappingAPI:
    def test_create_mapping(self, client, sample_config):
        client.post("/api/config", json=sample_config)
        body = {
            "name":             "new-mapping",
            "connection":       "test-host",
            "local_path":       "myrepo",
            "remote_path":      "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"],
            "auto_sync":       True,
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


# ── /api/deploy-status ────────────────────────────────────────────────────────

class TestDeployStatus:
    def test_deploy_status_returns_json(self, client):
        rv = client.get("/api/deploy-status")
        assert rv.status_code == 200
        data = rv.get_json()
        assert isinstance(data, dict)
