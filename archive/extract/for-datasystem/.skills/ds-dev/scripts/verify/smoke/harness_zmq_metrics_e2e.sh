#!/usr/bin/env bash
# E2E harness: Bazel 构建 datasystem（worker + whl）后复用 Python run_smoke.py 做 ZMQ 分段 metrics 验收。
# 见 rfc/2026-04-zmq-rpc-metrics-0.8.1/README.md
#
# Usage (local, 在 agent-workbench 根目录):
#   export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
#   export DATASYSTEM_ROOT="${PWD}/../yuanrong-datasystem"   # 或绝对路径
#   bash scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh --local
#
# Usage (remote xqyun-32c32g, 需已 rsync 两个仓):
#   bash scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh --remote
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMOKE_PY="${SCRIPT_DIR}/run_smoke.py"
WORKBENCH_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
DS_ROOT="${DATASYSTEM_ROOT:-$(cd "${WORKBENCH_ROOT}/../yuanrong-datasystem" 2>/dev/null && pwd || true)}"

MODE="local"
REMOTE="${ZMQ_SMOKE_REMOTE:-xqyun-32c32g}"

usage() {
  cat <<'EOF'
  --local     本机：build.sh -b bazel + 装 whl + run_smoke.py
  --remote    远程：SSH 后在 DS 目录 Bazel 构建 + 装 whl + run_smoke.py
  -h          help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) MODE=local; shift ;;
    --remote) MODE=remote; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "${SMOKE_PY}" ]]; then
  echo "Missing ${SMOKE_PY}" >&2
  exit 1
fi

if [[ "${MODE}" == "local" ]]; then
  if [[ -z "${DS_ROOT}" || ! -d "${DS_ROOT}" ]]; then
    echo "Set DATASYSTEM_ROOT to yuanrong-datasystem root." >&2
    exit 1
  fi
  export DS_OPENSOURCE_DIR="${DS_OPENSOURCE_DIR:-${HOME}/.cache/yuanrong-datasystem-third-party}"
  mkdir -p "${DS_OPENSOURCE_DIR}"
  echo "== [local] Bazel build: ${DS_ROOT} =="
  (cd "${DS_ROOT}" && bash build.sh -b bazel -t build -j "$(nproc)")
  WHL="$(find "${DS_ROOT}/bazel-bin" "${DS_ROOT}/output" -maxdepth 8 -name 'openyuanrong_datasystem-*.whl' 2>/dev/null | head -1 || true)"
  if [[ -n "${WHL}" ]]; then
    echo "== [local] pip install whl: ${WHL} =="
    python3 -m pip install --user --force-reinstall "${WHL}"
  else
    echo "WARN: no whl found; run_smoke may fail if yr client is stale." >&2
  fi
  echo "== [local] run_smoke.py =="
  exec python3 "${SMOKE_PY}"
fi

echo "== [remote] ${REMOTE} Bazel + smoke =="
ssh "${REMOTE}" bash -s <<'REMOTE_EOF'
set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
mkdir -p "${DS_OPENSOURCE_DIR}"
DS="${HOME}/workspace/git-repos/yuanrong-datasystem"
WB="${HOME}/workspace/git-repos/yuanrong-datasystem-agent-workbench"
cd "${DS}"
bash build.sh -b bazel -t build -j "$(nproc)"
WHL="$(find bazel-bin output -maxdepth 8 -name 'openyuanrong_datasystem-*.whl' 2>/dev/null | head -1 || true)"
if [[ -n "${WHL}" ]]; then python3 -m pip install --user --force-reinstall "${WHL}"; fi
cd "${WB}/scripts/testing/verify/smoke"
exec python3 run_smoke.py
REMOTE_EOF
