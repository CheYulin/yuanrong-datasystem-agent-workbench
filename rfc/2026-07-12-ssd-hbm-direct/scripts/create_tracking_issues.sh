#!/usr/bin/env bash
# Create GitCode tracking issues for SSD→HBM deferred tasks.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "${DIR}/create_tracking_issues.py"
