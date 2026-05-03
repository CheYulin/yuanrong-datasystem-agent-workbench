#!/usr/bin/env bash
# Full remote flow for P99 metrics work: rsync → build → test.
#
# Defaults: REMOTE=root@xqyun-32c32g, paths under /root/workspace/git-repos/
# Override any stage: run only the piece you need (see sibling scripts).
#
# Usage:
#   cd yuanrong-datasystem-agent-workbench/rfc/2026-05-03-metrics-p99-histogram
#   bash scripts/run_full_tests_remote.sh
#   REMOTE=user@host BAZEL_JOBS=16 bash scripts/run_full_tests_remote.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional: first argument sets REMOTE (e.g. xqyun-32c32g); prefix with user if needed.
if [[ "${#}" -ge 1 && -n "${1:-}" ]]; then
  export REMOTE="${1}"
fi

echo ">>> [1/3] rsync local datasystem → remote"
bash "${SCRIPT_DIR}/rsync_datasystem.sh"

echo ">>> [2/3] remote bazel build (metrics + st p99 perf binary)"
bash "${SCRIPT_DIR}/bazel_build.sh"

echo ">>> [3/3] remote bazel test (//tests/ut/common/metrics/...)"
bash "${SCRIPT_DIR}/bazel_run_tests.sh"

echo "=== run_full_tests_remote.sh finished ==="
