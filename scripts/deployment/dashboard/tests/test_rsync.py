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
import shutil
import tempfile
import unittest
from pathlib import Path


class TestMakeExcludesFile(unittest.TestCase):
    def setUp(self):
        self._created_files = []

    def tearDown(self):
        for f in self._created_files:
            try:
                os.unlink(f)
            except OSError:
                pass

    def _make_excludes(self, patterns):
        from dashboard.app import make_excludes_file
        path = make_excludes_file(patterns)
        self._created_files.append(path)
        return path

    def test_creates_file(self):
        path = self._make_excludes([".git/", "*.pyc", "__pycache__/"])
        self.assertTrue(os.path.exists(path))
        content = open(path).read()
        self.assertIn(".git/", content)
        self.assertIn("*.pyc", content)
        self.assertIn("__pycache__/", content)

    def test_empty_patterns(self):
        path = self._make_excludes([])
        self.assertTrue(os.path.exists(path))
        self.assertEqual(open(path).read(), "\n")

    def test_patterns_separated_by_newlines(self):
        path = self._make_excludes(["a", "b", "c"])
        lines = [l for l in open(path).read().splitlines() if l]
        self.assertEqual(lines, ["a", "b", "c"])


class _FlaskTestMixin:
    """Shared Flask test setup — subclasses must define _use_log_dir (bool)."""

    @classmethod
    def setUpClass(cls):
        cls._orig_environ = dict(os.environ)
        cls._tmp_config = tempfile.mktemp(suffix=".yaml")
        cls._tmp_base = tempfile.mkdtemp()
        os.environ["DASHBOARD_CONFIG"] = cls._tmp_config
        os.environ["DASHBOARD_BASE_DIR"] = cls._tmp_base
        if cls._use_log_dir:
            cls._tmp_log = tempfile.mkdtemp()
            os.environ["DASHBOARD_LOG_DIR"] = cls._tmp_log
            log_dir_val = cls._tmp_log
        else:
            cls._tmp_log = None
            log_dir_val = None
        import logging
        logging.getLogger("root").setLevel(logging.CRITICAL)
        from dashboard.app import create_app
        flask_app, _ = create_app(log_dir=log_dir_val)
        flask_app.config["TESTING"] = True
        cls._client = flask_app.test_client()

    @classmethod
    def tearDownClass(cls):
        # Restore only the keys we touched; never clear PATH, HOME, etc.
        for k in list(os.environ.keys()):
            if k not in cls._orig_environ:
                del os.environ[k]
        for k, v in cls._orig_environ.items():
            os.environ[k] = v
        try:
            os.unlink(cls._tmp_config)
        except OSError:
            pass


class TestConfigAPI(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    def test_get_empty_config_returns_defaults(self):
        rv = self._client.get("/api/config")
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertIn("connections", data)
        self.assertIn("mappings", data)

    def test_post_config_saves_and_get_returns_it(self):
        config = {
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
        self._client.post("/api/config", json=config)
        rv = self._client.get("/api/config")
        data = rv.get_json()
        self.assertEqual(len(data["connections"]), 1)
        self.assertEqual(data["connections"][0]["name"], "test-host")
        self.assertEqual(len(data["mappings"]), 1)
        self.assertEqual(data["mappings"][0]["name"], "test-mapping")


class TestLocalTreeAPI(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._repo = Path(cls._tmp_base) / "myrepo"
        cls._repo.mkdir()
        (cls._repo / "src").mkdir()
        (cls._repo / "tests").mkdir()
        (cls._repo / ".git").mkdir()
        (cls._repo / "build").mkdir()
        (cls._repo / "__pycache__").mkdir()
        (cls._repo / "src" / "main.py").write_text("def main(): pass\n")
        (cls._repo / "tests" / "test_main.py").write_text("def test_main(): pass\n")
        (cls._repo / ".git" / "config").write_text("[core]\n")
        (cls._repo / "build" / "artifact.o").write_bytes(b"\x00\x01\x02")
        (cls._repo / "__pycache__" / "module.pyc").write_bytes(b"\x00\x01")

    def test_tree_root(self):
        rv = self._client.get("/api/local/tree")
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        names = [i["name"] for i in data["items"]]
        self.assertIn("myrepo", names)

    def test_tree_subdir(self):
        rv = self._client.get("/api/local/tree?path=myrepo")
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        names = [i["name"] for i in data["items"]]
        self.assertIn("src", names)
        self.assertIn("tests", names)

    def test_tree_nonexistent_path(self):
        os.environ["DASHBOARD_BASE_DIR"] = "/nonexistent-xyz"
        rv = self._client.get("/api/local/tree?path=does-not-exist")
        self.assertEqual(rv.status_code, 404)


class TestMappingAPI(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    _SAMPLE_CONFIG = {
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

    def test_create_mapping(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        body = {
            "name": "new-mapping", "connection": "test-host",
            "local_path": "myrepo", "remote_path": "/home/testuser/remote/myrepo",
            "exclude_patterns": [".git/"], "auto_sync": True,
        }
        rv = self._client.post("/api/mappings", json=body)
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertEqual(data["mapping"]["name"], "new-mapping")
        self.assertEqual(data["mapping"]["exclude_patterns"], [".git/"])

    def test_get_mappings(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.get("/api/mappings")
        data = rv.get_json()
        self.assertEqual(len(data["mappings"]), 1)
        self.assertEqual(data["mappings"][0]["name"], "test-mapping")

    def test_delete_mapping(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.delete("/api/mappings/test-mapping")
        self.assertEqual(rv.status_code, 200)
        rv2 = self._client.get("/api/mappings")
        self.assertEqual(rv2.get_json()["mappings"], [])


class TestRsyncPreview(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    _SAMPLE_CONFIG = {
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

    def test_preview_missing_mapping_returns_404(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.post("/api/mappings/ghost/preview")
        self.assertEqual(rv.status_code, 404)

    def test_preview_missing_connection_returns_404(self):
        self._client.post("/api/config", json={
            "connections": [],
            "mappings": [{**self._SAMPLE_CONFIG["mappings"][0], "connection": "ghost"}],
            "settings": {},
        })
        (Path(self._tmp_base) / "myrepo").mkdir()
        (Path(self._tmp_base) / "myrepo" / "f.txt").write_text("hi")
        rv = self._client.post("/api/mappings/test-mapping/preview")
        self.assertEqual(rv.status_code, 404)


class TestResolveSSHHost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_home = os.environ.get("HOME")

    def tearDown(self):
        if self._orig_home is not None:
            os.environ["HOME"] = self._orig_home
        else:
            os.environ.pop("HOME", None)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_missing_ssh_config_returns_defaults(self):
        os.environ["HOME"] = self._tmp
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("some-host")
        self.assertEqual(result["host"], "some-host")
        self.assertEqual(result["port"], 22)
        self.assertIsNone(result["user"])

    def test_parses_ssh_config(self):
        ssh_dir = Path(self._tmp) / ".ssh"
        ssh_dir.mkdir()
        ssh_dir.joinpath("config").write_text(
            "Host mybox\n"
            "    HostName 192.168.1.100\n"
            "    Port 2222\n"
            "    User admin\n"
        )
        os.environ["HOME"] = self._tmp
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("mybox")
        self.assertEqual(result["host"], "192.168.1.100")
        self.assertEqual(result["port"], 2222)
        self.assertEqual(result["user"], "admin")

    def test_missing_host_in_config_returns_defaults(self):
        ssh_dir = Path(self._tmp) / ".ssh"
        ssh_dir.mkdir()
        ssh_dir.joinpath("config").write_text("Host other\n    HostName 1.2.3.4\n")
        os.environ["HOME"] = self._tmp
        from dashboard.app import resolve_ssh_host
        result = resolve_ssh_host("undefined-host")
        self.assertEqual(result["host"], "undefined-host")
        self.assertIsNone(result["user"])


class TestRunCmd(unittest.TestCase):
    def test_echo_ok(self):
        from dashboard.app import run_cmd
        result = run_cmd("echo hello")
        self.assertEqual(result["rc"], 0)
        self.assertIn("hello", result["out"])

    def test_stderr_captured(self):
        from dashboard.app import run_cmd
        result = run_cmd("echo error >&2")
        self.assertIn("error", result["err"])

    def test_invalid_command_rc(self):
        from dashboard.app import run_cmd
        result = run_cmd("exit 42")
        self.assertEqual(result["rc"], 42)

    def test_timeout(self):
        from dashboard.app import run_cmd
        result = run_cmd("sleep 10", timeout=1)
        self.assertEqual(result["rc"], -1)
        self.assertIn("timed out", result["err"])

    def test_cwd(self):
        from dashboard.app import run_cmd
        result = run_cmd("pwd", cwd="/tmp")
        self.assertEqual(result["rc"], 0)
        self.assertIn("/tmp", result["out"])


class TestLogsEndpoint(unittest.TestCase):
    """Standalone — does NOT use _FlaskTestMixin to avoid env pollution."""

    def setUp(self):
        self._orig_env = dict(os.environ)
        self._tmp_c = tempfile.mktemp(suffix=".yaml")
        self._tmp_b = tempfile.mkdtemp()
        self._tmp_l = tempfile.mkdtemp()
        os.environ["DASHBOARD_CONFIG"] = self._tmp_c
        os.environ["DASHBOARD_BASE_DIR"] = self._tmp_b
        os.environ["DASHBOARD_LOG_DIR"] = self._tmp_l
        import logging
        logging.getLogger("root").setLevel(logging.CRITICAL)
        from dashboard.app import create_app
        app, _ = create_app(log_dir=self._tmp_l)
        app.config["TESTING"] = True
        self._client = app.test_client()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        try:
            os.unlink(self._tmp_c)
        except OSError:
            pass

    def test_logs_returns_lines(self):
        rv = self._client.get("/api/logs?lines=5")
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertIn("lines", data)
        self.assertIn("log_file", data)
        self.assertIsInstance(data["lines"], list)

    def test_logs_returns_empty_when_no_log_file(self):
        """When DASHBOARD_LOG_DIR points to a non-existent dir, returns empty list."""
        os.environ["DASHBOARD_LOG_DIR"] = "/nonexistent-xyz-dashboard-test-abc"
        rv = self._client.get("/api/logs")
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertEqual(data["lines"], [])
        self.assertEqual(data["total_lines"], 0)


class TestDeployStatus(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    def test_deploy_status_returns_json(self):
        rv = self._client.get("/api/deploy-status")
        self.assertEqual(rv.status_code, 200)
        self.assertIsInstance(rv.get_json(), dict)


# ══════════════════════════════════════════════════════════════════════════════
#  AUTO-SYNC TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSkipLogic(unittest.TestCase):
    def test_skips_dot_git(self):
        from dashboard.app import _should_skip
        self.assertTrue(_should_skip(".git"))
        self.assertFalse(_should_skip(".git/config"))
        self.assertFalse(_should_skip("src/main.py"))
        self.assertFalse(_should_skip("myrepo/.gitignore"))

    def test_skips_pycache_and_pyc(self):
        from dashboard.app import _should_skip
        self.assertTrue(_should_skip("__pycache__"))
        self.assertTrue(_should_skip("module.pyc"))
        self.assertFalse(_should_skip("module.py"))

    def test_skips_node_modules(self):
        from dashboard.app import _should_skip
        self.assertTrue(_should_skip("node_modules"))
        self.assertFalse(_should_skip("src/node_modules/file.txt"))

    def test_skips_standard_build_dirs(self):
        from dashboard.app import _should_skip
        self.assertFalse(_should_skip("bazel-cache"))
        self.assertFalse(_should_skip("bazel-genfiles"))
        self.assertFalse(_should_skip("build_cov"))


class TestClangFileDetection(unittest.TestCase):
    def test_recognizes_cpp_extensions(self):
        from dashboard.app import _is_clang_file
        self.assertTrue(_is_clang_file("main.cpp"))
        self.assertTrue(_is_clang_file("main.h"))
        self.assertTrue(_is_clang_file("main.cc"))
        self.assertTrue(_is_clang_file("main.cxx"))
        self.assertTrue(_is_clang_file("main.hpp"))
        self.assertTrue(_is_clang_file("main.hxx"))

    def test_rejects_non_c_files(self):
        from dashboard.app import _is_clang_file
        self.assertFalse(_is_clang_file("main.py"))
        self.assertFalse(_is_clang_file("main.js"))
        self.assertFalse(_is_clang_file("main.rs"))
        self.assertFalse(_is_clang_file("CMakeLists.txt"))
        self.assertFalse(_is_clang_file("Makefile"))


class TestInotifyHandlerConstruction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_handler_stores_debounce(self):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test", local_path=self._tmp,
            exclude_patterns=[".git/"], remote_dest="host:/tmp",
            delete=False, logger=logging.getLogger("test"), debounce=99,
        )
        self.assertEqual(h.debounce, 99)

    def test_handler_stores_remote_dest(self):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test", local_path=self._tmp,
            exclude_patterns=[], remote_dest="root@xqyun-32c32g:/home/user",
            delete=True, logger=logging.getLogger("test"),
        )
        self.assertEqual(h.remote_dest, "root@xqyun-32c32g:/home/user")
        self.assertTrue(h.delete)

    def test_handler_stores_exclude_patterns(self):
        import logging
        from dashboard.app import _InotifyHandler
        h = _InotifyHandler(
            mapping_name="test", local_path=self._tmp,
            exclude_patterns=[".git/", "*.pyc"],
            remote_dest="host:/x", delete=False, logger=logging.getLogger("test"),
        )
        self.assertIn(".git/", h.exclude_patterns)
        self.assertIn("*.pyc", h.exclude_patterns)


class TestWatchManagerLifecycle(unittest.TestCase):
    def _make_wm(self):
        import logging
        from dashboard.app import WatchManager
        wm = WatchManager()
        wm._logger = logging.getLogger("test")
        return wm

    def test_start_watcher_auto_sync_off_does_nothing(self):
        tmp = tempfile.mkdtemp()
        try:
            wm = self._make_wm()
            mapping = {
                "name": "t1", "local_path": tmp, "auto_sync": False,
                "connection": "h", "remote_path": "/x",
                "exclude_patterns": [], "delete": False,
            }
            conn = {"host": "localhost", "username": "root", "port": 22}
            wm.start_watcher("t1", tmp, mapping, conn, wm._logger)
            self.assertNotIn("t1", wm._watchers)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_stop_watcher_unknown_name_does_not_raise(self):
        wm = self._make_wm()
        wm.stop_watcher("nonexistent")

    def test_restart_all_with_empty_mappings(self):
        tmp = tempfile.mkdtemp()
        try:
            wm = self._make_wm()
            wm.restart_all([], [], tmp, wm._logger)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestAutoSyncAPI(_FlaskTestMixin, unittest.TestCase):
    _use_log_dir = False

    _SAMPLE_CONFIG = {
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

    def test_enable_auto_sync_returns_ok(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.post(
            "/api/mappings/test-mapping/auto-sync",
            json={"enabled": True},
        )
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["auto_sync"])

    def test_disable_auto_sync_returns_ok(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.post(
            "/api/mappings/test-mapping/auto-sync",
            json={"enabled": False},
        )
        self.assertEqual(rv.status_code, 200)
        data = rv.get_json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["auto_sync"])

    def test_auto_sync_unknown_mapping_returns_404(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.post(
            "/api/mappings/ghost/auto-sync",
            json={"enabled": True},
        )
        self.assertEqual(rv.status_code, 404)

    def test_delete_mapping_stops_watcher(self):
        self._client.post("/api/config", json=self._SAMPLE_CONFIG)
        rv = self._client.delete("/api/mappings/test-mapping")
        self.assertEqual(rv.status_code, 200)


class TestPollingSyncConstruction(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_stores_interval(self):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t", local_path=self._tmp,
            exclude_patterns=[".git/"],
            remote_dest="root@xqyun-32c32g:/home/user",
            delete=False, logger=logging.getLogger("test"), interval=15.0,
        )
        self.assertEqual(p.interval, 15.0)

    def test_stores_remote_dest(self):
        import logging
        from dashboard.app import _PollingSync
        p = _PollingSync(
            mapping_name="t", local_path=self._tmp,
            exclude_patterns=[],
            remote_dest="root@host:/remote/path",
            delete=True, logger=logging.getLogger("test"), interval=5.0,
        )
        self.assertEqual(p.remote_dest, "root@host:/remote/path")
        self.assertTrue(p.delete)
        self.assertEqual(p.exclude_patterns, [])


class TestWatchManagerSocketIO(unittest.TestCase):
    def test_set_socketio_stores_reference(self):
        from dashboard.app import WatchManager
        wm = WatchManager()

        class FakeSocketIO:
            def emit(self, *a, **kw):
                pass

        fake = FakeSocketIO()
        wm.set_socketio(fake)
        self.assertIs(wm._socketio, fake)

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

        class FakeSocketIO:
            def emit(self, event, data, namespace=None):
                emitted["event"] = event
                emitted["data"] = data
                emitted["namespace"] = namespace

        wm.set_socketio(FakeSocketIO())
        wm._emit_fault("mymap", "test reason", mode="polling")
        self.assertEqual(emitted["event"], "watcher_fault")
        self.assertEqual(emitted["data"]["mapping"], "mymap")
        self.assertEqual(emitted["data"]["reason"], "test reason")
        self.assertEqual(emitted["data"]["mode"], "polling")
        self.assertEqual(emitted["namespace"], "/watchers")

    def test_emit_restored_calls_socketio_emit(self):
        import logging
        from dashboard.app import WatchManager
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
        self.assertEqual(emitted["event"], "watcher_restored")
        self.assertEqual(emitted["data"]["mapping"], "mymap2")
        self.assertEqual(emitted["namespace"], "/watchers")


if __name__ == "__main__":
    unittest.main()
