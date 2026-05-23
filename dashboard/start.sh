#!/bin/bash
# One-line deployment for DataSystem Log Analyzer
# Usage: bash start.sh [port] [log_dir]

set -e

PORT=${1:-8080}
LOG_DIR=${2:-"/var/log/datasystem"}
VENV_DIR="$(dirname "$0")/.venv"

echo "=== DataSystem Log Analyzer ==="
echo "Port: $PORT"
echo "Log dir: $LOG_DIR"

# Create venv if not exists
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Install dependencies
echo "Installing dependencies..."
"$VENV_DIR/bin/pip" install --quiet -r "$(dirname "$0")/requirements.txt"

# Start server
echo "Starting server on :$PORT..."
"$VENV_DIR/bin/python" "$(dirname "$0")/app.py" --port "$PORT"
