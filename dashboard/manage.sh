#!/bin/bash
# Process management script for DataSystem Log Analyzer
# Usage: bash manage.sh start|stop|restart|status

APP_NAME="datasystem-log-analyzer"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="/tmp/${APP_NAME}.pid"
LOG_FILE="/tmp/${APP_NAME}.log"
VENV_PY="$APP_DIR/.venv/bin/python"
APP_PY="$APP_DIR/app.py"
PORT=8080

get_pid() {
    if [ -f "$PID_FILE" ]; then
        cat "$PID_FILE"
    else
        # Try to find by port
        pid=$(lsof -ti:$PORT 2>/dev/null | head -1)
        echo "$pid"
    fi
}

is_running() {
    pid=$(get_pid)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    return 1
}

start() {
    if is_running; then
        pid=$(get_pid)
        echo "$APP_NAME is already running (PID: $pid)"
        return 1
    fi

    echo "Starting $APP_NAME..."

    # Create venv if needed
    if [ ! -d "$APP_DIR/.venv" ]; then
        echo "Creating virtual environment..."
        python3 -m venv "$APP_DIR/.venv"
        "$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
    fi

    # Start in background
    nohup $VENV_PY $APP_PY --host 0.0.0.0 --port $PORT > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 2

    if is_running; then
        pid=$(get_pid)
        echo "$APP_NAME started (PID: $pid)"
        echo "Dashboard: http://localhost:$PORT"
    else
        echo "Failed to start. Check $LOG_FILE"
        return 1
    fi
}

stop() {
    if ! is_running; then
        echo "$APP_NAME is not running"
        rm -f "$PID_FILE"
        return 0
    fi

    pid=$(get_pid)
    echo "Stopping $APP_NAME (PID: $pid)..."
    kill "$pid" 2>/dev/null

    for i in {1..10}; do
        if ! is_running; then
            echo "$APP_NAME stopped"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done

    echo "Force killing..."
    kill -9 "$pid" 2>/dev/null
    rm -f "$PID_FILE"
    echo "$APP_NAME stopped (force)"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if is_running; then
        pid=$(get_pid)
        echo "$APP_NAME is running (PID: $pid)"
        if [ -f "$LOG_FILE" ]; then
            echo "--- Last 5 lines of log ---"
            tail -5 "$LOG_FILE"
        fi
    else
        echo "$APP_NAME is not running"
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        restart
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
