#!/usr/bin/env python3
"""
Dashboard Flask application — registered as a package so it can be
installed via `pip install -e .` and run as `dashboard` CLI command.

Environment overrides:
    DASHBOARD_BASE_DIR   filesystem root to browse (default: ~/workspace/git-repos)
    DASHBOARD_PORT       HTTP port (default: 8765)
    DASHBOARD_HOST       bind address (default: 0.0.0.0)
    DASHBOARD_CONFIG     config file path (default: ~/.config/dashboard.yaml)
"""

from __future__ import annotations

import datetime
import errno
import fcntl
import json
import logging
import os
import pty
import re
import select
import signal
import struct
import subprocess
import termios
import threading
import time
import uuid
import weakref
from pathlib import Path
from logging.handlers import RotatingFileHandler
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import paramiko
import yaml
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from flask_socketio import SocketIO, emit

__version__ = "0.1.0"

# ── Default configuration ─────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "connections": [
        {
            "name":      "xqyun-32c32g",
            "host":      "xqyun-32c32g",
            "port":      22,
            "username":  "root",
            "auth_type": "key",
            "key_path":  "~/.ssh/id_ed25519",
            "password":  "",
            "note":      "GPU 机器 · 32c32g",
        },
    ],
    "mappings": [
        {
            "name":             "yuanrong-datasystem \u2192 xqyun",
            "connection":       "xqyun-32c32g",
            "local_path":       "yuanrong-datasystem",
            "remote_path":      "/root/workspace/git-repos/yuanrong-datasystem",
            "exclude_patterns": [
                ".git/", "build/", "build_cov/", ".cache/", "bazel-*",
                "__pycache__/", "*.pyc", "CMakeCache.txt", "CMakeFiles/",
                "compile_commands.json", "node_modules/",
            ],
            "auto_sync": False,
        },
        {
            "name":             "agent-workbench \u2192 xqyun",
            "connection":       "xqyun-32c32g",
            "local_path":       "yuanrong-datasystem-agent-workbench",
            "remote_path":      "/root/workspace/git-repos/yuanrong-datasystem-agent-workbench",
            "exclude_patterns": [".git/", "__pycache__/", "*.pyc", ".venv/", "node_modules/"],
            "auto_sync": False,
        },
    ],
    "settings": {"default_connection": ""},
}


# ── Module-level helpers (exposed for unit testing) ──────────────────────────

def resolve_ssh_host(host: str) -> dict:
    """Resolve SSH host via ~/.ssh/config — returns {host, port, user}."""
    ssh_config = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(ssh_config):
        return {"host": host, "port": 22, "user": None}
    try:
        ssh_conf = paramiko.SSHConfig()
        with open(ssh_config) as f:
            ssh_conf.parse(f)
        hc = ssh_conf.lookup(host)
        return {
            "host": hc.get("hostname", host),
            "port": int(hc.get("port", 22)),
            "user": hc.get("user", None),
        }
    except Exception:
        return {"host": host, "port": 22, "user": None}


def make_excludes_file(patterns: list[str]) -> str:
    """Write patterns to a temp file and return its path (caller deletes it)."""
    tmp = f"/tmp/rsyncignore.{uuid.uuid4().hex[:8]}"
    with open(tmp, "w") as f:
        f.write("\n".join(patterns) + "\n")
    return tmp


def get_file_tree(root_path: str, rel_prefix: str = "") -> list[dict]:
    """Return flat list of {type, name, path, rel, size, mtime} for immediate children."""
    items = []
    try:
        entries = sorted(Path(root_path).iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return []
    for entry in entries:
        rel = f"{rel_prefix}/{entry.name}" if rel_prefix else entry.name
        try:
            s = entry.stat()
            items.append({
                "type": "dir" if entry.is_dir() else "file",
                "name": entry.name,
                "path": str(entry),
                "rel":  rel,
                "size": s.st_size,
                "mtime": datetime.datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except OSError:
            continue
    return items


def run_cmd(cmd: str, cwd: str = None, timeout: int = 120) -> dict:
    """Run a shell command and return {rc, out, err}."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": "Command timed out"}
    except Exception as e:
        return {"rc": -1, "out": "", "err": str(e)}


def _rsync_line_class(line: str) -> str:
    """Classify a raw rsync stdout line for colour coding."""
    s = line.strip()
    if s.startswith("sending incremental"):
        return "info"
    if "deleting" in s:
        return "del"
    if s.startswith((">f", "hf", "cd+", "cf+", "cL")):
        return "add"
    if s.startswith(("sent ", "total ")):
        return "info"
    if "password" in s.lower() or "permission denied" in s.lower():
        return "err"
    if "connection" in s.lower() or "timeout" in s.lower():
        return "err"
    if s.startswith("ssh:") or "rsync:" in s.lower():
        return "err"
    return "info"


def _parse_rsync_summary(lines: list[str]) -> dict:
    """Extract sent/received byte counts and speed from rsync stdout."""
    stats = {"sent": "", "received": "", "speed": ""}
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("sent "):
            parts = s.split()
            try:
                idx = parts.index("bytes")
                stats["sent"] = parts[idx - 1] + " bytes"
                if len(parts) > idx + 2 and parts[idx + 2] == "bytes":
                    stats["received"] = parts[idx + 1] + " bytes"
                for i, p in enumerate(parts):
                    if p == "bytes/sec":
                        stats["speed"] = parts[i - 1] + " " + p
            except (IndexError, ValueError):
                pass
            break
    return stats


# ── Auto-sync watcher ─────────────────────────────────────────────────────

_CLANG_EXT = frozenset({".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"})
_CLANG_FORMAT_CMD = "clang-format --style=file"

def _find_clang_format(local_path):
    cur = local_path
    for _ in range(20):
        cf = os.path.join(cur, ".clang-format")
        if os.path.isfile(cf):
            return cf
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None

def _is_clang_file(path):
    return any(path.endswith(ext) for ext in _CLANG_EXT)

class _InotifyHandler(FileSystemEventHandler):
    def __init__(self, mapping_name, local_path, exclude_patterns,
                 remote_dest, delete, logger, debounce=1.5,
                 pre_sync_hooks=None, socketio=None):
        super().__init__()
        self.mapping_name = mapping_name
        self.local_path = local_path
        self.exclude_patterns = exclude_patterns
        self.remote_dest = remote_dest
        self.delete = delete
        self._logger = logger
        self.debounce = debounce
        self._timer = None
        self._dirty = False
        self._hooks = pre_sync_hooks or []
        self._socketio = socketio
        self._clang_fmt = _find_clang_format(local_path)
        if self._clang_fmt:
            self._clang_cmd = "clang-format --style=file:" + self._clang_fmt
        else:
            self._clang_cmd = _CLANG_FORMAT_CMD

    def _run_clang_format(self, src_path):
        try:
            result = subprocess.run(
                self._clang_cmd.split() + [src_path],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and result.stdout:
                with open(src_path, "w") as f:
                    f.write(result.stdout)
                self._logger.info("[WATCHER] clang-format applied: %s", src_path)
            else:
                self._logger.warning("[WATCHER] clang-format failed rc=%d: %s",
                                  result.returncode, src_path)
        except Exception as e:
            self._logger.warning("[WATCHER] clang-format error on %s: %s", src_path, e)

    def _run_hooks(self, src_path: str):
        """Run all matching pre-sync hooks for src_path."""
        import fnmatch
        for hook in self._hooks:
            htype = hook.get("type", "")
            patterns = hook.get("patterns", [])
            if not any(fnmatch.fnmatch(src_path, p) for p in patterns):
                continue
            if htype == "clang-format":
                self._run_clang_format(src_path)
            elif htype == "cmd":
                cmd_tpl = hook.get("cmd", "")
                if not cmd_tpl:
                    continue
                cmd = cmd_tpl.replace("{{path}}", src_path)
                try:
                    r = subprocess.run(cmd, shell=True, capture_output=True,
                                      text=True, timeout=60)
                    if r.returncode == 0:
                        self._logger.info("[WATCHER] hook cmd ok: %s  ->  %s",
                                        src_path, cmd)
                    else:
                        self._logger.warning("[WATCHER] hook cmd failed rc=%d: %s",
                                           r.returncode, cmd)
                except Exception as e:
                    self._logger.warning("[WATCHER] hook cmd error: %s  ->  %s", e, cmd)

    def _fire_sync(self):
        self._logger.info("[WATCHER] triggered sync for mapping=%s", self.mapping_name)
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"rc": None, "out": "", "done": False}
        ignore_file = make_excludes_file(self.exclude_patterns)
        flags = "-az --exclude-from=" + ignore_file
        ssh_opts = "-e \"ssh -o ConnectTimeout=10\""
        cmd = "rsync " + flags + " " + ssh_opts + " " + self.local_path + "/ " + self.remote_dest + "/"
        if self.delete:
            cmd += " --delete"

        def do_sync():
            if self._socketio:
                self._socketio.emit("sync_start", {
                    "mapping": self.mapping_name,
                }, namespace="/watchers")
            jobs[job_id]["started"] = True
            jobs[job_id]["started_at"] = datetime.datetime.now().isoformat()
            parts = self.remote_dest.split("@")
            host = parts[1].split(":")[0] if len(parts) > 1 else ""
            jobs[job_id]["started_info"] = {
                "host": host, "remote": self.remote_dest,
                "local": self.local_path, "delete": self.delete, "auto": True,
            }
            p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True)
            lines_out = []
            for line in iter(p.stdout.readline, ""):
                if line:
                    lines_out.append(line.rstrip())
                    jobs[job_id]["out"] = "\n".join(lines_out[-500:])
            p.wait()
            jobs[job_id]["rc"] = p.returncode
            jobs[job_id]["done"] = True
            jobs[job_id]["finished_at"] = datetime.datetime.now().isoformat()
            self._logger.info("[WATCHER] sync done for mapping=%s  rc=%d",
                            self.mapping_name, p.returncode)
            if self._socketio:
                self._socketio.emit("sync_done", {
                    "mapping": self.mapping_name,
                    "rc": p.returncode,
                    "auto": True,
                }, namespace="/watchers")

        threading.Thread(target=do_sync, daemon=True).start()

    def _schedule(self):
        self._dirty = True
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self.debounce, self._fire_and_clear)
        self._timer.start()

    def _fire_and_clear(self):
        if self._dirty:
            self._fire_sync()
            self._dirty = False

    def _on_event(self, event):
        if event.is_directory:
            return
        src = event.src_path
        if _should_skip(src):
            return
        if self._hooks:
            self._run_hooks(src)
        elif _is_clang_file(src):
            self._run_clang_format(src)
        self._logger.debug("[WATCHER] inotify event: %s %s",
                         type(event).__name__, src)
        self._schedule()

    def on_modified(self, event):  self._on_event(event)
    def on_created(self, event):   self._on_event(event)
    def on_deleted(self, event):   self._on_event(event)
    def on_moved(self, event):
        if _should_skip(event.dest_path):
            return
        self._on_event(event)

    def stop(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


class _PollingSync:
    """Fallback: polls local directories for changes using os.stat() mtime."""

    def __init__(self, mapping_name, local_path, exclude_patterns,
                 remote_dest, delete, logger, interval=5.0, socketio=None):
        self.mapping_name = mapping_name
        self.local_path = local_path
        self.exclude_patterns = exclude_patterns
        self.remote_dest = remote_dest
        self.delete = delete
        self._logger = logger
        self.interval = interval
        self._running = False
        self._mtimes = {}
        self._timer = None
        self._socketio = socketio
        self._collect_mtimes()

    def _prune_dirs(self, dirs):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_BASENAMES and not d.startswith("bazel-")]

    def _collect_mtimes(self):
        self._mtimes.clear()
        try:
            for root, dirs, files in os.walk(self.local_path):
                self._prune_dirs(dirs)
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if _should_skip(fpath):
                        continue
                    try:
                        self._mtimes[fpath] = os.path.getmtime(fpath)
                    except OSError:
                        pass
        except Exception:
            pass

    def _scan(self):
        changed = []
        try:
            for root, dirs, files in os.walk(self.local_path):
                self._prune_dirs(dirs)
                for fname in files:
                    fpath = os.path.join(root, fname)
                    if _should_skip(fpath):
                        continue
                    try:
                        mtime = os.path.getmtime(fpath)
                        if fpath not in self._mtimes or mtime > self._mtimes[fpath]:
                            changed.append(fpath)
                    except OSError:
                        pass
        except Exception:
            pass
        return changed

    def _fire_sync(self):
        self._logger.info("[WATCHER] [polling] triggered sync for mapping=%s",
                        self.mapping_name)
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"rc": None, "out": "", "done": False}
        ignore_file = make_excludes_file(self.exclude_patterns)
        flags = "-az --exclude-from=" + ignore_file
        ssh_opts = "-e \"ssh -o ConnectTimeout=10\""
        cmd = "rsync " + flags + " " + ssh_opts + " " + self.local_path + "/ " + self.remote_dest + "/"
        if self.delete:
            cmd += " --delete"

        def do_sync():
            if self._socketio:
                self._socketio.emit("sync_start", {
                    "mapping": self.mapping_name,
                }, namespace="/watchers")
            jobs[job_id]["started"] = True
            jobs[job_id]["started_at"] = datetime.datetime.now().isoformat()
            parts = self.remote_dest.split("@")
            host = parts[1].split(":")[0] if len(parts) > 1 else ""
            jobs[job_id]["started_info"] = {
                "host": host, "remote": self.remote_dest,
                "local": self.local_path, "delete": self.delete, "auto": True,
            }
            p = subprocess.Popen(
                cmd, shell=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True,
            )
            lines_out = []
            for line in iter(p.stdout.readline, ""):
                if line:
                    lines_out.append(line.rstrip())
                    jobs[job_id]["out"] = "\n".join(lines_out[-500:])
            p.wait()
            jobs[job_id]["rc"] = p.returncode
            jobs[job_id]["done"] = True
            jobs[job_id]["finished_at"] = datetime.datetime.now().isoformat()
            self._mtimes.clear()
            self._collect_mtimes()
            self._logger.info("[WATCHER] [polling] sync done for mapping=%s  rc=%d",
                           self.mapping_name, p.returncode)
            if self._socketio:
                self._socketio.emit("sync_done", {
                    "mapping": self.mapping_name,
                    "rc": p.returncode,
                    "auto": True,
                }, namespace="/watchers")

        threading.Thread(target=do_sync, daemon=True).start()

    def _run_loop(self):
        if not self._running:
            return
        self._logger.debug("[WATCHER] [polling] tick for %s  files=%d",
                          self.mapping_name, len(self._mtimes))
        changed = self._scan()
        if changed:
            self._fire_sync()
        if self._running:
            self._timer = threading.Timer(self.interval, self._run_loop)
            self._timer.start()

    def start(self):
        self._running = True
        self._collect_mtimes()
        self._run_loop()

    def stop(self):
        self._running = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None


class WatchManager:
    def __init__(self):
        self._watchers = {}   # name -> (handler, observer_or_None, mode)
        self._lock = threading.Lock()
        self._health_timer = None
        self._health_interval = 30.0   # check every 30s
        self._logger = None            # set on first start_watcher
        self._local_base = None
        self._mappings = []
        self._connections = []
        self._socketio = None          # injected via set_socketio()

    def set_socketio(self, socketio):
        self._socketio = socketio

    def _build_remote_dest(self, conn, mapping):
        resolved = resolve_ssh_host(conn["host"])
        user = resolved.get("user") or conn.get("username", "")
        return user + "@" + resolved["host"] + ":" + mapping["remote_path"]

    # ── Socket.IO event emission ─────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        if self._socketio is not None:
            self._socketio.emit(event, data, namespace="/watchers")

    def _emit_fault(self, mapping_name: str, reason: str, mode: str = "inotify"):
        msg = f"Watcher 故障 [{mapping_name}]: {reason}"
        self._logger.error("[WATCHER] fault: %s", msg)
        self._emit("watcher_fault", {
            "mapping": mapping_name,
            "reason": reason,
            "mode": mode,
            "message": msg,
        })

    def _emit_restored(self, mapping_name: str, mode: str):
        msg = f"Watcher 已恢复 [{mapping_name}]"
        self._logger.info("[WATCHER] restored: %s", msg)
        self._emit("watcher_restored", {
            "mapping": mapping_name,
            "mode": mode,
            "message": msg,
        })

    def _start_health_check(self):
        if self._health_timer is not None:
            return
        self._health_timer = threading.Timer(self._health_interval, self._health_check)
        self._health_timer.daemon = True
        self._health_timer.start()

    def _stop_health_check(self):
        if self._health_timer is not None:
            self._health_timer.cancel()
            self._health_timer = None

    def _health_check(self):
        self._health_timer = None
        with self._lock:
            dead = []
            for name, (handler, observer, mode) in self._watchers.items():
                if mode == "inotify":
                    if observer is not None and not observer.is_alive():
                        dead.append(name)
                        self._emit_fault(name, "inotify observer died, auto-restart中", mode="inotify")
                        handler._logger.error(
                            "[WATCHER] inotify observer died for mapping=%s  auto-restarting", name)
            # restart dead inotify watchers
            for name in dead:
                handler, observer, mode = self._watchers[name]
                # find the mapping config
                mapping_cfg = next(
                    (m for m in self._mappings if m["name"] == name), None
                )
                if mapping_cfg:
                    conn = next(
                        (c for c in self._connections if c["name"] == mapping_cfg["connection"]),
                        None,
                    )
                    if conn:
                        ok = self._unlocked_start_inotify(name, handler, mapping_cfg, conn)
                        if ok:
                            self._emit_restored(name, "inotify")
        # reschedule
        if self._watchers:
            self._start_health_check()

    def _unlocked_start_inotify(self, mapping_name, old_handler, mapping, conn):
        """Restart inotify for a mapping (must hold self._lock). Returns True if inotify
        started successfully, False if fell back to polling."""
        lp = os.path.join(self._local_base, mapping["local_path"].lstrip("/"))
        if not os.path.isdir(lp):
            return False
        remote_dest = self._build_remote_dest(conn, mapping)
        handler = _InotifyHandler(
            mapping_name=mapping_name,
            local_path=lp,
            exclude_patterns=mapping.get("exclude_patterns", []),
            remote_dest=remote_dest,
            delete=mapping.get("delete", False),
            logger=old_handler._logger,
            debounce=1.5,
            pre_sync_hooks=mapping.get("pre_sync_hooks"),
            socketio=self._socketio,
        )
        observer = Observer()
        observer.schedule(handler, lp, recursive=True)
        try:
            observer.start()
            self._watchers[mapping_name] = (handler, observer, "inotify")
            handler._logger.info("[WATCHER] inotify restarted OK for mapping=%s", mapping_name)
            return True
        except Exception as e:
            handler._logger.warning(
                "[WATCHER] inotify restart failed for %s: %s  falling back to polling",
                mapping_name, e)
            self._emit_fault(mapping_name, f"inotify 启动失败: {e}，已切换轮询", mode="polling")
            try:
                observer.stop()
            except Exception:
                pass
            poll = _PollingSync(
                mapping_name=mapping_name,
                local_path=lp,
                exclude_patterns=mapping.get("exclude_patterns", []),
                remote_dest=remote_dest,
                delete=mapping.get("delete", False),
                logger=old_handler._logger,
                interval=5.0,
            )
            poll.start()
            self._watchers[mapping_name] = (poll, None, "polling")
            handler._logger.info("[WATCHER] restarted (polling) for mapping=%s", mapping_name)
            return False

    def start_watcher(self, mapping_name, local_base, mapping, conn, logger):
        self.stop_watcher(mapping_name)
        # Persist config for health-check restart
        self._logger = logger
        self._local_base = local_base
        if mapping not in self._mappings:
            self._mappings.append(mapping)
        if conn not in self._connections:
            self._connections.append(conn)

        lp = os.path.join(local_base, mapping["local_path"].lstrip("/"))
        if not os.path.isdir(lp):
            return
        remote_dest = self._build_remote_dest(conn, mapping)

        # Try inotify first
        handler = _InotifyHandler(
            mapping_name=mapping_name,
            local_path=lp,
            exclude_patterns=mapping.get("exclude_patterns", []),
            remote_dest=remote_dest,
            delete=mapping.get("delete", False),
            logger=logger,
            debounce=1.5,
            pre_sync_hooks=mapping.get("pre_sync_hooks"),
            socketio=self._socketio,
        )
        observer = Observer()
        observer.schedule(handler, lp, recursive=True)
        try:
            observer.start()
            with self._lock:
                self._watchers[mapping_name] = (handler, observer, "inotify")
            logger.info("[WATCHER] started (inotify) for mapping=%s  path=%s", mapping_name, lp)
            self._start_health_check()
            return
        except Exception as e:
            logger.warning("[WATCHER] inotify failed for %s: %s  falling back to polling",
                       mapping_name, e)
            self._emit_fault(mapping_name, f"inotify 不可用: {e}，已切换轮询", mode="polling")
            try:
                observer.stop()
            except Exception:
                pass

        # Fallback: polling
            poll = _PollingSync(
                mapping_name=mapping_name,
                local_path=lp,
                exclude_patterns=mapping.get("exclude_patterns", []),
                remote_dest=remote_dest,
                delete=mapping.get("delete", False),
                logger=logger,
                interval=5.0,
                socketio=self._socketio,
            )
            poll.start()
            with self._lock:
                self._watchers[mapping_name] = (poll, None, "polling")
            logger.info("[WATCHER] started (polling) for mapping=%s  path=%s", mapping_name, lp)
            self._start_health_check()

    def stop_watcher(self, mapping_name):
        with self._lock:
            pair = self._watchers.pop(mapping_name, None)
        if pair:
            watcher, observer, mode = pair
            watcher.stop()
            if mode == "inotify" and observer is not None:
                observer.stop()
                observer.join(timeout=3)
            watcher._logger.info("[WATCHER] stopped for mapping=%s", mapping_name)
        if not self._watchers:
            self._stop_health_check()

    def restart_all(self, mappings, connections, local_base, logger):
        self._mappings = list(mappings)
        self._connections = list(connections)
        self._local_base = local_base
        current_names = {m["name"] for m in mappings if m.get("auto_sync")}
        for name in list(self._watchers):
            if name not in current_names:
                self.stop_watcher(name)
        for mapping in mappings:
            if mapping.get("auto_sync"):
                conn = next((c for c in connections
                             if c["name"] == mapping["connection"]), None)
                if conn:
                    self.start_watcher(mapping["name"], local_base,
                                     mapping, conn, logger)

    def stop_all(self):
        for name in list(self._watchers):
            self.stop_watcher(name)
        self._stop_health_check()


# ── Global watcher (singleton, lives for process lifetime) ─────────────────

watcher = WatchManager()


# ── Auto-sync polling ────────────────────────────────────────────────────

_SKIP_BASENAMES = frozenset({
    ".git", "__pycache__", "node_modules", ".cache", "bazel-out",
    "bazel-bin", "bazel-testlogs", ".pytest_cache", ".tox", ".venv",
    ".idea", ".vscode", ".DS_Store",
})
_SKIP_EXTS = frozenset({".pyc", ".pyo", ".swp", ".swo", ".o", ".a", ".so", ".dylib"})


def _should_skip(path: str) -> bool:
    bn = os.path.basename(path)
    if bn in _SKIP_BASENAMES:
        return True
    return any(bn.endswith(ext) for ext in _SKIP_EXTS)

def create_app(log_dir: str = None) -> Flask:
    pkg_dir = Path(__file__).parent.resolve()
    template_dir = str(pkg_dir.parent.parent)

    local_base  = os.environ.get("DASHBOARD_BASE_DIR", os.path.expanduser("~/workspace/git-repos"))
    config_file = os.environ.get("DASHBOARD_CONFIG", os.path.expanduser("~/.config/dashboard.yaml"))

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "dashboard.log")
    else:
        log_file = None

    app = Flask(__name__, template_folder=template_dir)
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024

    # ── Structured logging ─────────────────────────────────────────────
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    if not root_logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(name)s:%(lineno)-4d  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        root_logger.addHandler(ch)
        if log_file:
            fh = RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            root_logger.addHandler(fh)
        root_logger.info(
            "Dashboard starting  [base=%s]  [config=%s]  [log=%s]",
            local_base, config_file, log_file,
        )

    app.logger.info("Flask app initialised")

    socketio = SocketIO(
        app,
        cors_allowed_origins="*",
        async_mode="threading",
        logger=False,
        engineio_logger=False,
        message_queue=None,
    )
    watcher.set_socketio(socketio)

    jobs: dict[str, dict] = {}

    # ── Config helpers ───────────────────────────────────────────────────

    def load_config() -> dict:
        try:
            with open(config_file) as f:
                cfg = yaml.safe_load(f) or {}
        except (FileNotFoundError, yaml.YAMLError):
            cfg = DEFAULT_CONFIG.copy()
            _save_config(cfg)
        for key in ("connections", "mappings", "settings"):
            if key not in cfg:
                cfg[key] = DEFAULT_CONFIG.get(key, {})
        return cfg

    def _save_config(cfg: dict):
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, "w") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        app.logger.info("[CONFIG] saved  path=%s  connections=%d  mappings=%d",
                       config_file, len(cfg.get("connections", [])), len(cfg.get("mappings", [])))

    # ── Start auto-sync (inotify) watchers for mappings with auto_sync=True ──
    def _start_watchers():
        try:
            cfg = load_config()
            watcher.restart_all(
                cfg.get("mappings", []),
                cfg.get("connections", []),
                local_base,
                app.logger,
            )
            app.logger.info("[WATCHER] initialised  active_mappings=%s",
                          [m["name"] for m in cfg.get("mappings", []) if m.get("auto_sync")])
        except Exception as e:
            app.logger.warning("[WATCHER] failed to start: %s", e)

    threading.Thread(target=_start_watchers, daemon=True).start()

    # ── SSH helpers ────────────────────────────────────────────────────

    def _expand_remote_path(remote_path: str, conn: dict) -> str:
        if "~" not in remote_path:
            return remote_path
        try:
            client = _ssh_for_connection(conn, resolve_ssh_host)
            try:
                _, stdout, _ = client.exec_command("echo $HOME")
                home = stdout.read().decode().strip()
                if home:
                    return remote_path.replace("~", home)
            finally:
                client.close()
        except Exception:
            pass
        return remote_path

    def _ssh_for_connection(conn: dict, resolve_fn=None) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:
            pass
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        resolved = (resolve_ssh_host if resolve_fn is None else resolve_fn)(conn["host"])
        kwargs = {
            "hostname": resolved["host"],
            "port":     conn.get("port") if conn.get("port") not in (None, 22) else resolved["port"],
            "username": conn["username"],
            "timeout":  10,
        }
        auth_type = conn.get("auth_type", "key")
        if auth_type == "key":
            kwargs["key_filename"] = os.path.expanduser(conn.get("key_path", "~/.ssh/id_ed25519"))
        elif auth_type == "password" and conn.get("password"):
            kwargs["password"] = conn["password"]
        client.connect(**kwargs)
        return client

    def sftp_ls(conn_name: str, remote_path: str = ".") -> list[dict]:
        cfg = load_config()
        conn = next((c for c in cfg["connections"] if c["name"] == conn_name), None)
        if not conn:
            raise ValueError(f"Connection '{conn_name}' not found")
        client = _ssh_for_connection(conn)
        sftp = client.open_sftp()
        try:
            entries = sftp.listdir_attr(remote_path)
        finally:
            sftp.close()
            client.close()
        items = []
        for entry in entries:
            item_path = remote_path.rstrip("/") + "/" + entry.filename if remote_path != "." else entry.filename
            is_dir = getattr(entry, "st_mode", 0) & 0o40000 if hasattr(entry, "st_mode") else False
            items.append({
                "name": entry.filename,
                "path": item_path,
                "type": "dir" if is_dir else "file",
                "size": getattr(entry, "st_size", 0),
                "mtime": datetime.datetime.fromtimestamp(getattr(entry, "st_atime", 0)).strftime("%Y-%m-%d %H:%M") if hasattr(entry, "st_atime") else "-",
            })
        items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        return items

    def test_connection(conn_name: str) -> dict:
        cfg = load_config()
        conn = next((c for c in cfg["connections"] if c["name"] == conn_name), None)
        if not conn:
            return {"ok": False, "error": f"Connection '{conn_name}' not found"}
        try:
            client = _ssh_for_connection(conn)
            sftp = client.open_sftp()
            sftp.listdir(".")
            sftp.close()
            client.close()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── HTTP Routes ────────────────────────────────────────────────────

    @app.route("/")
    def index():
        return render_template("index.html", local_base=local_base)

    @app.route("/api/config", methods=["GET"])
    def api_config_get():
        return jsonify(load_config())

    @app.route("/api/config", methods=["POST"])
    def api_config_post():
        body = request.json or {}
        cfg = load_config()
        for key in ("connections", "mappings", "settings"):
            if key in body:
                cfg[key] = body[key]
        _save_config(cfg)
        app.logger.info("[CONFIG] updated via API  keys=%s  method=POST  endpoint=/api/config",
                       list(body.keys()))
        try:
            watcher.restart_all(
                cfg.get("mappings", []),
                cfg.get("connections", []),
                local_base,
                app.logger,
            )
        except Exception as e:
            app.logger.warning("[WATCHER] restart after config update failed: %s", e)
        return jsonify({"ok": True, "config": cfg})

    @app.route("/api/connections", methods=["GET"])
    def api_connections_get():
        return jsonify({"connections": load_config().get("connections", [])})

    @app.route("/api/connections", methods=["POST"])
    def api_connections_post():
        body = request.json or {}
        name = body.get("name", "")
        if not name:
            return jsonify({"error": "name required"}), 400
        cfg = load_config()
        cfg["connections"] = [c for c in cfg.get("connections", []) if c["name"] != name]
        conn = {
            "name":      name,
            "host":      body.get("host", ""),
            "port":      int(body.get("port", 22)),
            "username":  body.get("username", ""),
            "auth_type": body.get("auth_type", "key"),
            "key_path":  body.get("key_path", "~/.ssh/id_ed25519"),
            "password":  body.get("password", ""),
            "note":      body.get("note", ""),
        }
        cfg["connections"].append(conn)
        _save_config(cfg)
        app.logger.info(
            "[CONN] created  name=%s  host=%s:%d  user=%s  auth=%s",
            name, conn["host"], conn["port"], conn["username"], conn["auth_type"]
        )
        return jsonify({"ok": True, "connection": conn})

    @app.route("/api/connections/<name>", methods=["DELETE"])
    def api_connections_delete(name: str):
        cfg = load_config()
        cfg["connections"] = [c for c in cfg.get("connections", []) if c["name"] != name]
        _save_config(cfg)
        app.logger.info("[CONN] deleted  name=%s", name)
        return jsonify({"ok": True})

    @app.route("/api/connections/<name>/test", methods=["POST"])
    def api_connections_test(name: str):
        result = test_connection(name)
        if result.get("ok"):
            app.logger.info("[CONN] SSH test OK  name=%s", name)
        else:
            app.logger.warning("[CONN] SSH test FAILED  name=%s  error=%s",
                            name, result.get("error", "unknown"))
        return jsonify(result)

    @app.route("/api/connections/<name>/sftp", methods=["GET"])
    def api_sftp_tree(name: str):
        path = request.args.get("path", ".")
        try:
            items = sftp_ls(name, path)
            return jsonify({"path": path, "items": items})
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/api/mappings", methods=["GET"])
    def api_mappings_get():
        return jsonify({"mappings": load_config().get("mappings", [])})

    @app.route("/api/mappings", methods=["POST"])
    def api_mappings_post():
        body = request.json or {}
        name = body.get("name", "")
        if not name:
            return jsonify({"error": "name required"}), 400
        remote_path = body.get("remote_path", "")
        if "~" in remote_path:
            return jsonify({"error": "remote_path 不能包含 ~，请使用绝对路径如 /root/workspace/..."}), 400
        cfg = load_config()
        cfg["mappings"] = [m for m in cfg.get("mappings", []) if m["name"] != name]
        mapping = {
            "name":             name,
            "connection":       body.get("connection", ""),
            "local_path":       body.get("local_path", ""),
            "remote_path":      remote_path,
            "exclude_patterns": body.get("exclude_patterns", []),
            "auto_sync":        body.get("auto_sync", False),
        }
        cfg["mappings"].append(mapping)
        _save_config(cfg)
        app.logger.info(
            "[MAPPING] created  name=%s  conn=%s  local=%s  remote=%s  excludes=%d",
            name, mapping["connection"], mapping["local_path"],
            mapping["remote_path"], len(mapping["exclude_patterns"])
        )
        return jsonify({"ok": True, "mapping": mapping})

    @app.route("/api/mappings/<name>", methods=["DELETE"])
    def api_mappings_delete(name: str):
        cfg = load_config()
        cfg["mappings"] = [m for m in cfg.get("mappings", []) if m["name"] != name]
        _save_config(cfg)
        watcher.stop_watcher(name)
        app.logger.info("[MAPPING] deleted  name=%s", name)
        return jsonify({"ok": True})

    @app.route("/api/mappings/<name>/auto-sync", methods=["POST"])
    def api_mapping_auto_sync(name: str):
        """Toggle auto-sync for a mapping (or update auto_sync flag for a specific mapping)."""
        body = request.json or {}
        enabled = body.get("enabled")
        cfg = load_config()
        mapping = next((m for m in cfg.get("mappings", []) if m["name"] == name), None)
        if not mapping:
            return jsonify({"error": f"Mapping '{name}' not found"}), 404
        if enabled is not None:
            mapping["auto_sync"] = bool(enabled)
            _save_config(cfg)
        conn = next((c for c in cfg.get("connections", []) if c["name"] == mapping["connection"]), None)
        if conn and mapping.get("auto_sync"):
            watcher.start_watcher(name, local_base, mapping, conn, app.logger)
            app.logger.info("[WATCHER] enabled for mapping=%s", name)
        else:
            watcher.stop_watcher(name)
            app.logger.info("[WATCHER] disabled for mapping=%s", name)
        return jsonify({"ok": True, "auto_sync": mapping.get("auto_sync", False)})

    @app.route("/api/local/tree")
    def api_local_tree():
        rel_path   = request.args.get("path", "")
        full_path = os.path.join(local_base, rel_path)
        if not os.path.exists(full_path):
            return jsonify({"error": "Path not found"}), 404
        return jsonify({"path": rel_path, "items": get_file_tree(full_path, rel_path)})

    @app.route("/api/local/read")
    def api_local_read():
        rel_path   = request.args.get("path", "")
        full_path = os.path.join(local_base, rel_path)
        if not os.path.exists(full_path) or os.path.isdir(full_path):
            return jsonify({"error": "Not a file"}), 400
        try:
            size = os.path.getsize(full_path)
            if size > 2 * 1024 * 1024:
                return jsonify({"error": "File too large (> 2 MB)"}), 400
            with open(full_path, "r", errors="replace") as f:
                content = f.read()
            return jsonify({"path": rel_path, "content": content, "size": size})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ── Sync ──────────────────────────────────────────────────────────

    @app.route("/api/mappings/<name>/preview", methods=["POST"])
    def api_mapping_preview(name: str):
        cfg     = load_config()
        mapping = next((m for m in cfg.get("mappings", []) if m["name"] == name), None)
        if not mapping:
            return jsonify({"error": f"Mapping '{name}' not found"}), 404
        lp   = os.path.join(local_base, mapping["local_path"].lstrip("/"))
        conn = next((c for c in cfg["connections"] if c["name"] == mapping["connection"]), None)
        if not conn:
            return jsonify({"error": f"Connection '{mapping['connection']}' not found"}), 404
        if not os.path.exists(lp):
            return jsonify({"error": f"Local path not found: {lp}"}), 404
        resolved    = resolve_ssh_host(conn["host"])
        remote_dest = f"{resolved.get('user', conn.get('username', ''))}@{resolved['host']}:{mapping['remote_path']}"
        ignore_file = make_excludes_file(mapping.get("exclude_patterns", []))
        cmd = f"rsync -azn --exclude-from={ignore_file} {lp}/ {remote_dest}/"
        result = run_cmd(cmd, timeout=120)
        app.logger.info(
            "[RSYNC] preview  mapping=%s  local=%s  remote=%s  rc=%d  excludes=%d patterns",
            name, lp, remote_dest, result["rc"], len(mapping.get("exclude_patterns", []))
        )
        added, deleted, changed = [], [], []
        for line in result["out"].splitlines():
            s = line.strip()
            if not s or s in ("sending incremental file list", "---"):
                continue
            if "deleting" in s:
                deleted.append(s)
            elif any(s.startswith(x) for x in (">f", "hf", "cd+", "cf+")):
                added.append(s)
            elif not any(s.startswith(x) for x in ("sent ", "total ", "sending")):
                changed.append(s)
        return jsonify({
            "added": added, "deleted": deleted, "changed": changed,
            "total": len(added) + len(deleted) + len(changed),
            "rc": result["rc"], "remote": remote_dest, "local": lp,
        })

    @app.route("/api/mappings/<name>/sync", methods=["POST"])
    def api_mapping_sync(name: str):
        body   = request.json or {}
        delete = body.get("delete", False)
        cfg    = load_config()
        mapping = next((m for m in cfg.get("mappings", []) if m["name"] == name), None)
        if not mapping:
            return jsonify({"error": f"Mapping '{name}' not found"}), 404
        lp   = os.path.join(local_base, mapping["local_path"].lstrip("/"))
        conn = next((c for c in cfg["connections"] if c["name"] == mapping["connection"]), None)
        if not conn:
            return jsonify({"error": f"Connection '{mapping['connection']}' not found"}), 404
        if not os.path.exists(lp):
            return jsonify({"error": f"Local path not found: {lp}"}), 404
        resolved    = resolve_ssh_host(conn["host"])
        remote_dest = f"{resolved.get('user', conn.get('username', ''))}@{resolved['host']}:{mapping['remote_path']}"
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"rc": None, "out": "", "done": False}
        ignore_file = make_excludes_file(mapping.get("exclude_patterns", []))
        flags = f"-az --exclude-from={ignore_file}"
        cmd = f"rsync {flags} {lp}/ {remote_dest}/"
        if delete:
            cmd += " --delete"
        app.logger.info(
            "[RSYNC] sync started  mapping=%s  job=%s  local=%s  remote=%s  excludes=%d patterns  delete=%s",
            name, job_id, lp, remote_dest, len(mapping.get("exclude_patterns", [])), delete
        )

        def do_sync():
            jobs[job_id]["started"] = True
            jobs[job_id]["started_at"] = datetime.datetime.now().isoformat()
            jobs[job_id]["started_info"] = {
                "host": resolved["host"],
                "remote": remote_dest,
                "local": lp,
                "delete": delete,
            }
            p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []
            for line in iter(p.stdout.readline, ""):
                if line:
                    lines.append(line.rstrip())
                    jobs[job_id]["out"] = "\n".join(lines[-500:])
            p.wait()
            jobs[job_id]["rc"] = p.returncode
            jobs[job_id]["done"] = True
            jobs[job_id]["finished_at"] = datetime.datetime.now().isoformat()
            app.logger.info("[RSYNC] sync finished  job=%s  rc=%d", job_id, p.returncode)

        threading.Thread(target=do_sync, daemon=True).start()
        return jsonify({"job_id": job_id, "message": f"Syncing {name} \u2192 {conn['host']}"})

    @app.route("/api/rsync-stream/<job_id>")
    def api_rsync_stream(job_id: str):
        def event_stream():
            try:
                last_len = 0
                seen_started = False
                while True:
                    job = jobs.get(job_id)
                    if not job:
                        yield "data: " + json.dumps({"type": "error", "msg": "Job not found"}) + "\n\n"
                        break
                    if not seen_started and job.get("started"):
                        seen_started = True
                        info = job.get("started_info", {})
                        yield "data: " + json.dumps({
                            "type": "started",
                            "host": info.get("host", ""),
                            "remote": info.get("remote", ""),
                            "local": info.get("local", ""),
                            "delete": info.get("delete", False),
                            "time": job.get("started_at", ""),
                        }) + "\n\n"
                    if job.get("done"):
                        finished_at = job.get("finished_at", "")
                        duration = ""
                        if finished_at and job.get("started_at"):
                            try:
                                s = datetime.datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
                                e = datetime.datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                                duration = f"{(e - s).total_seconds():.1f}s"
                            except Exception:
                                duration = "N/A"
                        lines = (job.get("out") or "").split("\n")
                        stats = _parse_rsync_summary(lines)
                        yield "data: " + json.dumps({
                            "type": "done",
                            "rc": job["rc"],
                            "duration": duration,
                            "stats": stats,
                        }) + "\n\n"
                        break
                    l = len(job.get("out") or "")
                    if l != last_len:
                        for line in (job.get("out") or "").split("\n")[last_len:]:
                            if line.strip():
                                cls = _rsync_line_class(line)
                                payload = json.dumps({"type": "line", "line": line, "cls": cls})
                                yield "data: " + payload + "\n\n"
                        last_len = l
                    import time; time.sleep(0.3)
            except Exception as e:
                app.logger.error("[RSYNC] stream error  job=%s  %s", job_id, e)
                yield "data: " + json.dumps({"type": "error", "msg": str(e)}) + "\n\n"
        return Response(stream_with_context(event_stream()),
                       mimetype="text/event-stream",
                       headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/api/upload-sync", methods=["POST"])
    def api_upload_sync():
        files        = request.files.getlist("files")
        mapping_name = request.form.get("mapping_name", "")
        do_delete    = request.form.get("delete", "false") == "true"
        if not files:
            return jsonify({"error": "No files provided"}), 400
        cfg     = load_config()
        mapping = next((m for m in cfg.get("mappings", []) if m["name"] == mapping_name), None)
        if not mapping:
            return jsonify({"error": f"Mapping '{mapping_name}' not found"}), 404
        lb = os.path.join(local_base, mapping["local_path"].lstrip("/"))
        conn = next((c for c in cfg["connections"] if c["name"] == mapping["connection"]), None)
        if not conn:
            return jsonify({"error": f"Connection '{mapping['connection']}' not found"}), 404
        os.makedirs(lb, exist_ok=True)
        for f in files:
            f.save(os.path.join(lb, os.path.basename(f.filename)))
        resolved    = resolve_ssh_host(conn["host"])
        remote_dest = f"{resolved.get('user', conn.get('username', ''))}@{resolved['host']}:{mapping['remote_path']}"
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {"rc": None, "out": "", "done": False, "started": True,
                        "started_at": datetime.datetime.now().isoformat(),
                        "started_info": {"host": resolved["host"], "remote": remote_dest, "local": lb, "delete": do_delete}}
        ignore_file = make_excludes_file(mapping.get("exclude_patterns", []))
        cmd = f"rsync -az --exclude-from={ignore_file} {lb}/ {remote_dest}/"
        if do_delete:
            cmd += " --delete"

        def do_sync():
            p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            lines = []
            for line in iter(p.stdout.readline, ""):
                if line:
                    lines.append(line.rstrip())
                    jobs[job_id]["out"] = "\n".join(lines[-500:])
            p.wait()
            jobs[job_id]["rc"] = p.returncode
            jobs[job_id]["done"] = True
            jobs[job_id]["finished_at"] = datetime.datetime.now().isoformat()

        threading.Thread(target=do_sync, daemon=True).start()
        return jsonify({"job_id": job_id, "uploaded": len(files),
                       "message": f"{len(files)} files \u2192 {remote_dest}"})

    @app.route("/api/logs")
    def api_logs():
        lines = int(request.args.get("lines", 200))
        log_file = os.path.join(os.environ.get(
            "DASHBOARD_LOG_DIR",
            os.path.expanduser("~/.local/state/dashboard")), "dashboard.log")
        if not os.path.exists(log_file):
            return jsonify({
                "lines": [], "log_file": log_file,
                "total_lines": 0, "returned_lines": 0,
            })
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
            tail = all_lines[-lines:]
            return jsonify({
                "log_file": log_file,
                "total_lines": len(all_lines),
                "returned_lines": len(tail),
                "lines": [l.rstrip("\n") for l in tail],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/deploy-status")
    def api_deploy_status():
        dirs = {
            "yuanrong-datasystem":                 os.path.join(local_base, "yuanrong-datasystem"),
            "yuanrong-datasystem-agent-workbench": os.path.join(local_base, "yuanrong-datasystem-agent-workbench"),
        }
        status = {}
        for name, path in dirs.items():
            exists = os.path.exists(path)
            branch = commit = "-"
            if exists:
                r = run_cmd("git rev-parse --abbrev-ref HEAD 2>/dev/null", cwd=path)
                if r["rc"] == 0:
                    branch = r["out"].strip()
                r2 = run_cmd("git log -1 --oneline 2>/dev/null", cwd=path)
                if r2["rc"] == 0:
                    commit = r2["out"].strip()[:12]
            status[name] = {"exists": exists, "branch": branch, "commit": commit}
        return jsonify(status)

    @app.route("/api/shell", methods=["POST"])
    def api_shell():
        cmd = request.json.get("cmd", "")
        cwd = request.json.get("cwd", local_base)
        if not cmd:
            return jsonify({"error": "cmd required"}), 400
        result = run_cmd(cmd, cwd=cwd)
        return jsonify({"rc": result["rc"], "stdout": result["out"], "stderr": result["err"]})

    # ── Shell Socket.IO Events ──────────────────────────────────────────

    shell_sessions: dict[str, dict] = {}

    def _shell_list() -> list[dict]:
        return [
            {"session_id": sid, "type": s["type"], "label": s["label"],
             "cwd": s.get("cwd", ""), "alive": s.get("alive", True)}
            for sid, s in shell_sessions.items()
        ]

    @socketio.on("shell_list", namespace="/shells")
    def on_shell_list():
        emit("shell_list", _shell_list())

    @socketio.on("shell_create", namespace="/shells")
    def on_shell_create(data):
        try:
            stype = data.get("type", "local")
            label = data.get("label", "")
            if stype == "local":
                sid, info = _create_local_shell(label)
            else:
                sid, info = _create_ssh_shell(data.get("connection", ""), label)
            emit("shell_created", {"session": info})
            emit("shell_list", _shell_list())
            app.logger.info("Shell created: id=%s type=%s label=%s", sid, stype, label)
        except Exception as e:
            app.logger.error("Shell create failed: %s", e)
            emit("shell_error", {"error": str(e)})

    @socketio.on("shell_input", namespace="/shells")
    def on_shell_input(data):
        sid, info = data.get("session_id", ""), shell_sessions.get(data.get("session_id", ""))
        if not sid or not info:
            return
        _shell_write(sid, info, data.get("data", ""))

    @socketio.on("shell_resize", namespace="/shells")
    def on_shell_resize(data):
        sid = data.get("session_id", "")
        info = shell_sessions.get(sid)
        if not sid or not info:
            return
        _shell_resize(sid, info, int(data.get("rows", 40)), int(data.get("cols", 120)))

    @socketio.on("shell_close", namespace="/shells")
    def on_shell_close(data):
        sid = data.get("session_id", "")
        if sid and sid in shell_sessions:
            app.logger.info("Shell closed: id=%s", sid)
            _shell_close(sid, shell_sessions[sid])
            del shell_sessions[sid]
        emit("shell_list", _shell_list())

    @socketio.on("connect", namespace="/shells")
    def on_shell_connect():
        pass

    @socketio.on("disconnect", namespace="/shells")
    def on_shell_disconnect():
        pass

    # ── Shell internals ──────────────────────────────────────────────

    def _create_local_shell(label: str = "") -> tuple[str, dict]:
        import tty
        shell_path = "/bin/zsh" if os.path.exists("/bin/zsh") else "/bin/bash"
        pid, master_fd = pty.fork()
        if pid == 0:
            os.close(master_fd)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            os.execve(shell_path, [os.path.basename(shell_path)], env)
        tty.setraw(master_fd)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, os.O_NONBLOCK)
        sid = uuid.uuid4().hex[:8]
        shell_sessions[sid] = {
            "type": "local", "label": label or f"local:{os.path.basename(shell_path)}",
            "pid": pid, "fd": master_fd, "cwd": os.path.expanduser("~"), "alive": True,
        }
        t = threading.Thread(target=_reader_local, args=(sid,), daemon=True)
        t.start()
        return sid, {"session_id": sid, "type": "local",
                      "label": label or f"local:{os.path.basename(shell_path)}",
                      "cwd": os.path.expanduser("~"), "alive": True}

    def _create_ssh_shell(connection_name: str, label: str = "") -> tuple[str, dict]:
        cfg = load_config()
        conn = next((c for c in cfg.get("connections", []) if c["name"] == connection_name), None)
        if not conn:
            raise ValueError(f"Connection '{connection_name}' not found")
        resolved = resolve_ssh_host(conn["host"])
        kwargs = {
            "hostname": resolved["host"],
            "port":     conn.get("port", 22),
            "username": conn["username"], "timeout": 10,
        }
        auth_type = conn.get("auth_type", "key")
        if auth_type == "key":
            kwargs["key_filename"] = os.path.expanduser(conn.get("key_path", "~/.ssh/id_ed25519"))
        else:
            kwargs["password"] = conn.get("password", "")
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:
            pass
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(**kwargs)
        channel = client.get_transport().open_session()
        channel.get_pty(width=120, height=40, term="xterm-256color")
        channel.invoke_shell()
        channel.get_transport().set_keepalive(10)
        sid = uuid.uuid4().hex[:8]
        shell_sessions[sid] = {
            "type": "ssh", "label": label or f"ssh:{resolved['host']}",
            "client": client, "channel": channel, "cwd": "~", "alive": True,
        }
        t = threading.Thread(target=_reader_ssh, args=(sid,), daemon=True)
        t.start()
        return sid, {"session_id": sid, "type": "ssh",
                      "label": label or f"ssh:{resolved['host']}", "cwd": "~", "alive": True}

    def _shell_write(sid: str, info: dict, data: str):
        try:
            if info["type"] == "local":
                os.write(info["fd"], data.encode())
            else:
                info["channel"].send(data.encode())
        except (OSError, IOError):
            pass

    def _shell_resize(sid: str, info: dict, rows: int, cols: int):
        try:
            if info["type"] == "local":
                winsz = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(info["fd"], fcntl.TIOCSWINSZ, winsz)
            else:
                info["channel"].resize_pty(width=cols, height=rows)
        except Exception:
            pass

    def _shell_close(sid: str, info: dict):
        info["alive"] = False
        try:
            if info["type"] == "local":
                os.close(info["fd"])
                os.kill(info["pid"], signal.SIGTERM)
            else:
                info["channel"].close()
                info["client"].close()
        except Exception:
            pass

    def _reader_local(sid: str):
        info = shell_sessions.get(sid)
        if not info:
            return
        while info.get("alive"):
            try:
                r, _, _ = select.select([info["fd"]], [], [], 0.5)
                if r:
                    data = os.read(info["fd"], 4096)
                    if not data:
                        break
                    socketio.start_background_task(
                        emit, "shell_output",
                        {"session_id": sid, "data": data.decode("utf-8", errors="replace")},
                        namespace="/shells"
                    )
            except (OSError, IOError) as e:
                if e.errno not in (errno.EIO, errno.EBADF):
                    break
                break
        info["alive"] = False
        socketio.start_background_task(
            emit, "shell_closed", {"session_id": sid}, namespace="/shells"
        )

    def _reader_ssh(sid: str):
        info = shell_sessions.get(sid)
        if not info:
            return
        channel = info["channel"]
        while info.get("alive") and channel.get_transport().is_active():
            try:
                r, _, _ = select.select([channel], [], [], 0.5)
                if r:
                    data = channel.recv(4096)
                    if not data:
                        break
                    socketio.start_background_task(
                        emit, "shell_output",
                        {"session_id": sid, "data": data.decode("utf-8", errors="replace")},
                        namespace="/shells"
                    )
            except (OSError, IOError, paramiko.SSHException):
                if not channel.get_transport().is_active():
                    break
        info["alive"] = False
        socketio.start_background_task(
            emit, "shell_closed", {"session_id": sid}, namespace="/shells"
        )

    # ── Watcher status namespace ────────────────────────────────────────

    @socketio.on("connect", namespace="/watchers")
    def on_watchers_connect():
        pass

    @socketio.on("disconnect", namespace="/watchers")
    def on_watchers_disconnect():
        pass

    @socketio.on("watcher_list", namespace="/watchers")
    def on_watcher_list():
        with watcher._lock:
            items = []
            for name, (handler, observer, mode) in watcher._watchers.items():
                alive = (observer is not None and observer.is_alive()) if mode == "inotify" else True
                items.append({
                    "mapping": name,
                    "mode": mode,
                    "alive": alive,
                    "local_path": getattr(handler, "local_path", ""),
                })
        emit("watcher_list", {"watchers": items})

    return app, socketio
