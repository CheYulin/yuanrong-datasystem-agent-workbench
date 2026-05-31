"""
conftest.py — shared pytest fixtures for dashboard tests.
"""

import os
import tempfile
from pathlib import Path

import pytest

# Set up a clean environment before importing the app
os.environ["DASHBOARD_CONFIG"] = ""
os.environ["DASHBOARD_BASE_DIR"] = ""


@pytest.fixture
def tmp_config_file():
    """A temporary YAML config file path (cleaned up after test)."""
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def tmp_base_dir():
    """A temporary directory to serve as LOCAL_BASE."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def app(tmp_config_file, tmp_base_dir, monkeypatch):
    """Flask test client backed by the real create_app() with temp dirs."""
    monkeypatch.setenv("DASHBOARD_CONFIG", tmp_config_file)
    monkeypatch.setenv("DASHBOARD_BASE_DIR", tmp_base_dir)
    # Disable log handlers so tests don't spam stdout
    import logging
    logging.getLogger("root").setLevel(logging.CRITICAL)

    from dashboard.app import create_app

    flask_app, socketio = create_app(log_dir=None)
    flask_app.config["TESTING"] = True
    return flask_app, socketio


@pytest.fixture
def client(app):
    """Flask test client."""
    flask_app, _ = app
    return flask_app.test_client()


@pytest.fixture
def sample_config():
    """Minimal config dict matching DEFAULT_CONFIG structure."""
    return {
        "connections": [
            {
                "name":     "test-host",
                "host":     "localhost",
                "port":     22,
                "username": "testuser",
                "auth_type": "key",
                "key_path":  "~/.ssh/id_ed25519",
                "password": "",
                "note":     "Test connection",
            }
        ],
        "mappings": [
            {
                "name":             "test-mapping",
                "connection":       "test-host",
                "local_path":       "myrepo",
                "remote_path":      "/home/testuser/remote/myrepo",
                "exclude_patterns": [".git/", "*.pyc", "__pycache__/"],
                "auto_sync":        False,
            }
        ],
        "settings": {"default_connection": ""},
    }


@pytest.fixture
def populated_base_dir(tmp_base_dir):
    """
    tmp_base_dir/
        myrepo/
            src/
                main.py
            tests/
                test_main.py
            .git/
                config
            build/
                artifact.o
            __pycache__/
                module.pyc
    """
    base = Path(tmp_base_dir)
    repo = base / "myrepo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "build").mkdir()
    (repo / "__pycache__").mkdir()

    (repo / "src" / "main.py").write_text("def main(): pass\n")
    (repo / "tests" / "test_main.py").write_text("def test_main(): pass\n")
    (repo / ".git" / "config").write_text("[core]\n")
    (repo / "build" / "artifact.o").write_bytes(b"\x00\x01\x02")
    (repo / "__pycache__" / "module.pyc").write_bytes(b"\x00\x01")

    return str(repo)
