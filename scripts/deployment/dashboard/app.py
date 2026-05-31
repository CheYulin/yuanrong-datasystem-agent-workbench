#!/usr/bin/env python3
"""
Remote Deployment Dashboard — Flask Web UI
Run locally on WSL, browse LOCAL files, rsync to REMOTE nodes via SFTP.
Access via http://localhost:8765

Features:
  - SSH connection manager: add/edit/delete, test connectivity
  - SFTP remote browser: browse remote directories
  - Deployment mappings: local dir ↔ remote dir, per-mapping ignore patterns
  - Sync status: pending changes per mapping
  - YAML config: ~/.config/dashboard.yaml
"""

import os, subprocess, threading, uuid, datetime, time, json, yaml, re
from pathlib import Path
from flask import Flask, render_template, jsonify, request, Response, stream_with_context

import paramiko

# ── Config ───────────────────────────────────────────────────────────────────

LOCAL_BASE  = os.environ.get("DASHBOARD_BASE_DIR", os.path.expanduser("~/workspace/git-repos"))
PORT        = int(os.environ.get("DASHBOARD_PORT", "8765"))
CONFIG_FILE = os.path.expanduser("~/.config/dashboard.yaml")

# Resolve to this script's directory so Flask can find templates from any cwd
_DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=_DASHBOARD_DIR)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
jobs = {}

# ── YAML Config ──────────────────────────────────────────────────────────────

def _default_config() -> dict:
    return {
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
                "name":             "yuanrong-datasystem → xqyun",
                "connection":       "xqyun-32c32g",
                "local_path":       "yuanrong-datasystem",
                "remote_path":      "~/workspace/git-repos/yuanrong-datasystem",
                "exclude_patterns": [
                    ".git/",
                    "build/",
                    "build_cov/",
                    ".cache/",
                    "bazel-*",
                    "__pycache__/",
                    "*.pyc",
                    "CMakeCache.txt",
                    "CMakeFiles/",
                    "compile_commands.json",
                    "node_modules/",
                ],
                "auto_sync": False,
            },
            {
                "name":             "agent-workbench → xqyun",
                "connection":       "xqyun-32c32g",
                "local_path":       "yuanrong-datasystem-agent-workbench",
                "remote_path":      "~/workspace/git-repos/yuanrong-datasystem-agent-workbench",
                "exclude_patterns": [
                    ".git/",
                    "__pycache__/",
                    "*.pyc",
                    ".venv/",
                    "node_modules/",
                ],
                "auto_sync": False,
            },
        ],
        "settings": {
            "default_connection": "",
        },
    }

def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            cfg = yaml.safe_load(f) or {}
    except (FileNotFoundError, yaml.YAMLError):
        cfg = _default_config()
        save_config(cfg)   # persist defaults on first run
    for k in ("connections", "mappings", "settings"):
        if k not in cfg:
            cfg[k] = _default_config()[k]
    return cfg

def save_config(cfg: dict):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

# ── SSH/SFTP Helpers ─────────────────────────────────────────────────────────

def _resolve_ssh_host(host: str) -> dict:
    """Resolve SSH alias via ~/.ssh/config, return {host, port, user}."""
    ssh_config = os.path.expanduser("~/.ssh/config")
    if not os.path.exists(ssh_config):
        return {"host": host, "port": 22, "user": None}
    try:
        ssh_conf = paramiko.SSHConfig()
        with open(ssh_config) as f:
            ssh_conf.parse(f)
        # Look up the host
        host_config = ssh_conf.lookup(host)
        return {
            "host": host_config.get("hostname", host),
            "port": int(host_config.get("port", 22)),
            "user": host_config.get("user", None),
        }
    except Exception:
        return {"host": host, "port": 22, "user": None}

def _expand_remote_path(remote_path: str, conn: dict) -> str:
    """Expand ~ in remote_path to actual remote home dir via SSH."""
    if "~" not in remote_path:
        return remote_path
    try:
        client = _ssh_for_connection(conn)
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

def _ssh_for_connection(conn: dict) -> paramiko.SSHClient:
    """Create authenticated SSHClient from connection dict."""
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
    except Exception:
        pass
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Resolve SSH alias from ~/.ssh/config
    resolved = _resolve_ssh_host(conn["host"])

    connect_kwargs = {
        "hostname": resolved["host"],
        "port": conn.get("port") if conn.get("port") not in (None, 22) else resolved["port"],
        "username": conn["username"],
        "timeout": 10,
    }

    auth_type = conn.get("auth_type", "key")
    if auth_type == "key":
        key_path = os.path.expanduser(conn.get("key_path", "~/.ssh/id_ed25519"))
        try:
            connect_kwargs["key_filename"] = key_path
        except Exception:
            pass
    elif auth_type == "password" and conn.get("password"):
        connect_kwargs["password"] = conn["password"]

    client.connect(**connect_kwargs)
    return client

def sftp_ls(conn_name: str, remote_path: str = ".") -> list:
    """List remote directory via SFTP."""
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
        is_dir = entry.st_mode & 0o40000 if hasattr(entry, 'st_mode') else False
        items.append({
            "name":    entry.filename,
            "path":    item_path,
            "type":    "dir" if is_dir else "file",
            "size":    getattr(entry, 'st_size', 0),
            "mtime":   datetime.datetime.fromtimestamp(getattr(entry, 'st_atime', 0)).strftime("%Y-%m-%d %H:%M") if hasattr(entry, 'st_atime') else "-",
        })
    items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
    return items

def test_connection(conn_name: str) -> dict:
    """Test SSH connectivity and SFTP."""
    cfg = load_config()
    conn = next((c for c in cfg["connections"] if c["name"] == conn_name), None)
    if not conn:
        return {"ok": False, "error": f"Connection '{conn_name}' not found"}

    try:
        client = _ssh_for_connection(conn)
        # Try SFTP
        sftp = client.open_sftp()
        sftp.listdir(".")
        sftp.close()
        client.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: str, cwd: str = None, timeout: int = 120) -> dict:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
        return {"rc": p.returncode, "out": p.stdout, "err": p.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "out": "", "err": "Command timed out"}
    except Exception as e:
        return {"rc": -1, "out": "", "err": str(e)}

def make_excludes_file(patterns: list) -> str:
    tmp = f"/tmp/rsyncignore.{uuid.uuid4().hex[:8]}"
    with open(tmp, "w") as f:
        f.write("\n".join(patterns) + "\n")
    return tmp

def get_file_tree(root_path: str, rel_prefix: str = "") -> list:
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
                "rel": rel,
                "size": s.st_size,
                "mtime": datetime.datetime.fromtimestamp(s.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
        except OSError:
            continue
    return items

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", local_base=LOCAL_BASE)

# ── Config (full) ────────────────────────────────────────────────────────────

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
    save_config(cfg)
    return jsonify({"ok": True, "config": cfg})

# ── Connections ────────────────────────────────────────────────────────────────

@app.route("/api/connections", methods=["GET"])
def api_connections_get():
    return jsonify({"connections": load_config().get("connections", [])})

@app.route("/api/connections", methods=["POST"])
def api_connections_post():
    """Add or update a connection."""
    body = request.json or {}
    name = body.get("name", "")
    if not name:
        return jsonify({"error": "name required"}), 400

    cfg = load_config()
    # Remove existing if updating
    cfg["connections"] = [c for c in cfg.get("connections", []) if c["name"] != name]
    conn = {
        "name":     name,
        "host":     body.get("host", ""),
        "port":     int(body.get("port", 22)),
        "username": body.get("username", ""),
        "auth_type": body.get("auth_type", "key"),
        "key_path": body.get("key_path", "~/.ssh/id_ed25519"),
        "password": body.get("password", ""),
        "note":     body.get("note", ""),
    }
    cfg["connections"].append(conn)
    save_config(cfg)
    return jsonify({"ok": True, "connection": conn})

@app.route("/api/connections/<name>", methods=["DELETE"])
def api_connections_delete(name: str):
    cfg = load_config()
    cfg["connections"] = [c for c in cfg.get("connections", []) if c["name"] != name]
    save_config(cfg)
    return jsonify({"ok": True})

@app.route("/api/connections/<name>/test", methods=["POST"])
def api_connections_test(name: str):
    result = test_connection(name)
    return jsonify(result)

@app.route("/api/connections/<name>/sftp", methods=["GET"])
def api_sftp_tree(name: str):
    """Browse remote directory via SFTP."""
    path = request.args.get("path", ".")
    try:
        items = sftp_ls(name, path)
        return jsonify({"path": path, "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# ── Mappings ────────────────────────────────────────────────────────────────────

@app.route("/api/mappings", methods=["GET"])
def api_mappings_get():
    return jsonify({"mappings": load_config().get("mappings", [])})

@app.route("/api/mappings", methods=["POST"])
def api_mappings_post():
    """Add or update a mapping."""
    body = request.json or {}
    name = body.get("name", "")
    if not name:
        return jsonify({"error": "name required"}), 400

    cfg = load_config()
    cfg["mappings"] = [m for m in cfg.get("mappings", []) if m["name"] != name]
    mapping = {
        "name":              name,
        "connection":        body.get("connection", ""),
        "local_path":        body.get("local_path", ""),
        "remote_path":       body.get("remote_path", ""),
        "exclude_patterns":  body.get("exclude_patterns", []),
        "auto_sync":         body.get("auto_sync", False),
    }
    cfg["mappings"].append(mapping)
    save_config(cfg)
    return jsonify({"ok": True, "mapping": mapping})

@app.route("/api/mappings/<name>", methods=["DELETE"])
def api_mappings_delete(name: str):
    cfg = load_config()
    cfg["mappings"] = [m for m in cfg.get("mappings", []) if m["name"] != name]
    save_config(cfg)
    return jsonify({"ok": True})

# ── Local File Browser ─────────────────────────────────────────────────────────

@app.route("/api/local/tree")
def api_local_tree():
    rel_path = request.args.get("path", "")
    full_path = os.path.join(LOCAL_BASE, rel_path)
    if not os.path.exists(full_path):
        return jsonify({"error": "Path not found"}), 404
    return jsonify({"path": rel_path, "items": get_file_tree(full_path, rel_path)})

@app.route("/api/local/read")
def api_local_read():
    rel_path = request.args.get("path", "")
    full_path = os.path.join(LOCAL_BASE, rel_path)
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

# ── Sync (preview + sync) ────────────────────────────────────────────────────

@app.route("/api/mappings/<name>/preview", methods=["POST"])
def api_mapping_preview(name: str):
    """Rsync dry-run for a mapping."""
    cfg = load_config()
    mapping = next((m for m in cfg.get("mappings", []) if m["name"] == name), None)
    if not mapping:
        return jsonify({"error": f"Mapping '{name}' not found"}), 404

    local_path  = os.path.join(LOCAL_BASE, mapping["local_path"].lstrip("/"))
    conn_name   = mapping["connection"]
    conn        = next((c for c in cfg.get("connections", []) if c["name"] == conn_name), None)
    if not conn:
        return jsonify({"error": f"Connection '{conn_name}' not found"}), 404

    excludes    = mapping.get("exclude_patterns", [])
    resolved    = _resolve_ssh_host(conn["host"])
    remote_path = _expand_remote_path(mapping["remote_path"], conn)
    remote_dest = f"{resolved['host']}:{remote_path}"

    if not os.path.exists(local_path):
        return jsonify({"error": f"Local path not found: {local_path}"}), 404

    ignore_file = make_excludes_file(excludes)
    cmd = (f"rsync -azn "
           f"--exclude-from={ignore_file} "
           f"{local_path}/ {remote_dest}/")

    result = run(cmd, timeout=120)

    added, deleted, changed = [], [], []
    for line in result["out"].splitlines():
        s = line.strip()
        if not s or s in ("sending incremental file list", "---"):
            continue
        if any(s.startswith(x) for x in (">f", "hf", "cd+", "cf+")):
            added.append(s)
        elif "deleting" in s:
            deleted.append(s)
        elif not any(s.startswith(x) for x in ("sent ", "total ", "Δ", "sending")):
            changed.append(s)

    return jsonify({
        "rc": result["rc"],
        "added": added, "deleted": deleted, "changed": changed,
        "total": len(added)+len(deleted)+len(changed),
        "remote": remote_dest,
        "local": local_path,
    })

@app.route("/api/mappings/<name>/sync", methods=["POST"])
def api_mapping_sync(name: str):
    """Execute rsync for a mapping."""
    body    = request.json or {}
    delete  = body.get("delete", False)
    cfg     = load_config()
    mapping = next((m for m in cfg.get("mappings", []) if m["name"] == name), None)
    if not mapping:
        return jsonify({"error": f"Mapping '{name}' not found"}), 404

    local_path  = os.path.join(LOCAL_BASE, mapping["local_path"].lstrip("/"))
    conn_name   = mapping["connection"]
    conn        = next((c for c in cfg.get("connections", []) if c["name"] == conn_name), None)
    if not conn:
        return jsonify({"error": f"Connection '{conn_name}' not found"}), 404

    excludes    = mapping.get("exclude_patterns", [])
    resolved    = _resolve_ssh_host(conn["host"])
    remote_path = _expand_remote_path(mapping["remote_path"], conn)
    remote_dest = f"{resolved['host']}:{remote_path}"

    if not os.path.exists(local_path):
        return jsonify({"error": f"Local path not found: {local_path}"}), 404

    ignore_file = make_excludes_file(excludes)
    cmd = (f"rsync -az "
           f"--exclude-from={ignore_file} "
           f"{local_path}/ {remote_dest}/")
    if delete:
        cmd += " --delete"

    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"rc": None, "out": "", "done": False}

    def run_sync():
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for line in iter(p.stdout.readline, ""):
            if line:
                lines.append(line.rstrip())
                jobs[job_id]["out"] = "\n".join(lines[-500:])
        p.wait()
        jobs[job_id]["rc"] = p.returncode
        jobs[job_id]["done"] = True

    threading.Thread(target=run_sync, daemon=True).start()
    return jsonify({"job_id": job_id, "message": f"Syncing {name} → {conn['host']}"})

# ── Rsync Stream ───────────────────────────────────────────────────────────────

@app.route("/api/rsync-stream/<job_id>")
def api_rsync_stream(job_id: str):
    def event_stream():
        last_len = 0
        while True:
            job = jobs.get(job_id)
            if not job:
                yield "data: {'type':'error','msg':'Job not found'}\n\n"
                break
            if job["done"]:
                yield f"data: {{'type':'done','rc':{job['rc']}}}\n\n"
                break
            l = len(job["out"])
            if l != last_len:
                for line in job["out"].split("\n")[last_len:]:
                    if line.strip():
                        import json as _json
                        yield f"data: {_json.dumps({'type':'line','line':line})}\n\n"
                last_len = l
            time.sleep(0.3)
    return Response(stream_with_context(event_stream()),
                   mimetype="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Upload ────────────────────────────────────────────────────────────────────

@app.route("/api/upload-sync", methods=["POST"])
def api_upload_sync():
    """Receive file, save locally, then sync via mapping."""
    files       = request.files.getlist("files")
    mapping_name = request.form.get("mapping_name", "")
    do_delete  = request.form.get("delete", "false") == "true"

    if not files:
        return jsonify({"error": "No files provided"}), 400

    cfg     = load_config()
    mapping = next((m for m in cfg.get("mappings", []) if m["name"] == mapping_name), None)
    if not mapping:
        return jsonify({"error": f"Mapping '{mapping_name}' not found"}), 404

    local_base  = os.path.join(LOCAL_BASE, mapping["local_path"].lstrip("/"))
    conn_name   = mapping["connection"]
    conn        = next((c for c in cfg.get("connections", []) if c["name"] == conn_name), None)
    if not conn:
        return jsonify({"error": f"Connection '{conn_name}' not found"}), 404

    excludes    = mapping.get("exclude_patterns", [])
    resolved    = _resolve_ssh_host(conn["host"])
    remote_path = _expand_remote_path(mapping["remote_path"], conn)
    remote_dest = f"{resolved['host']}:{remote_path}"

    os.makedirs(local_base, exist_ok=True)
    for f in files:
        f.save(os.path.join(local_base, os.path.basename(f.filename)))

    ignore_file = make_excludes_file(excludes)
    cmd = (f"rsync -az "
           f"--exclude-from={ignore_file} "
           f"{local_base}/ {remote_dest}/")
    if do_delete:
        cmd += " --delete"

    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"rc": None, "out": "", "done": False}

    def run_sync():
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = []
        for line in iter(p.stdout.readline, ""):
            if line:
                lines.append(line.rstrip())
                jobs[job_id]["out"] = "\n".join(lines[-500:])
        p.wait()
        jobs[job_id]["rc"] = p.returncode
        jobs[job_id]["done"] = True

    threading.Thread(target=run_sync, daemon=True).start()
    return jsonify({"job_id": job_id, "uploaded": len(files),
                    "message": f"{len(files)} files → {remote_dest}"})

# ── Deploy Status ──────────────────────────────────────────────────────────────

@app.route("/api/deploy-status")
def api_deploy_status():
    dirs = {
        "yuanrong-datasystem":                  os.path.join(LOCAL_BASE, "yuanrong-datasystem"),
        "yuanrong-datasystem-agent-workbench":  os.path.join(LOCAL_BASE, "yuanrong-datasystem-agent-workbench"),
    }
    status = {}
    for name, path in dirs.items():
        exists = os.path.exists(path)
        branch = commit = "-"
        if exists:
            r = run("git rev-parse --abbrev-ref HEAD 2>/dev/null", cwd=path)
            if r["rc"] == 0: branch = r["out"].strip()
            r2 = run("git log -1 --oneline 2>/dev/null", cwd=path)
            if r2["rc"] == 0: commit = r2["out"].strip()[:12]
        status[name] = {"exists": exists, "branch": branch, "commit": commit}
    return jsonify(status)

# ── Shell ─────────────────────────────────────────────────────────────────────

@app.route("/api/shell", methods=["POST"])
def api_shell():
    cmd = request.json.get("cmd", "")
    cwd = request.json.get("cwd", LOCAL_BASE)
    if not cmd:
        return jsonify({"error": "cmd required"}), 400
    result = run(cmd, cwd=cwd)
    return jsonify({"rc": result["rc"], "stdout": result["out"], "stderr": result["err"]})

# ── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cfg = load_config()
    print(f"Starting Dashboard on port {PORT}")
    print(f"Config: {CONFIG_FILE}")
    print(f"Connections: {[c['name'] for c in cfg.get('connections', [])]}")
    print(f"Mappings: {[m['name'] for m in cfg.get('mappings', [])]}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
