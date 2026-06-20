#!/usr/bin/env python3
"""Entry point for the `dashboard` package CLI."""

import argparse
import os
import sys

DASHBOARD_BASE_DIR = os.environ.get(
    "DASHBOARD_BASE_DIR",
    os.path.expanduser("~/workspace/git-repos"),
)
DASHBOARD_CONFIG = os.environ.get(
    "DASHBOARD_CONFIG",
    os.path.expanduser("~/.config/dashboard.yaml"),
)
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8765"))
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
DASHBOARD_LOG_DIR = os.environ.get(
    "DASHBOARD_LOG_DIR",
    os.path.join(os.path.expanduser("~/.local/state"), "dashboard"),
)


def main():
    parser = argparse.ArgumentParser(description="Dashboard — local-to-remote sync + shell")
    parser.add_argument("--port", type=int, default=DASHBOARD_PORT)
    parser.add_argument("--host", default=DASHBOARD_HOST)
    parser.add_argument("--base-dir", default=DASHBOARD_BASE_DIR)
    parser.add_argument("--config", default=DASHBOARD_CONFIG)
    parser.add_argument("--log-dir", default=DASHBOARD_LOG_DIR)
    args = parser.parse_args()

    if args.base_dir:
        os.environ["DASHBOARD_BASE_DIR"] = args.base_dir
    if args.config:
        os.environ["DASHBOARD_CONFIG_FILE"] = args.config

    os.makedirs(args.log_dir, exist_ok=True)
    log_file = os.path.join(args.log_dir, "dashboard.log")

    from dashboard.app import create_app

    result = create_app(log_dir=args.log_dir)
    if isinstance(result, tuple):
        app, socketio = result
    else:
        app = result
        socketio = None

    print(f"Starting Dashboard on http://{args.host}:{args.port}")
    print(f"  LOCAL_BASE : {os.environ.get('DASHBOARD_BASE_DIR', DASHBOARD_BASE_DIR)}")
    print(f"  CONFIG     : {os.environ.get('DASHBOARD_CONFIG', DASHBOARD_CONFIG)}")
    print(f"  LOG        : {log_file}")
    print("  Press Ctrl+C to stop")

    if socketio:
        socketio.run(app, host=args.host, port=args.port, debug=False, allow_unsafe_werkzeug=True)
    else:
        app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
