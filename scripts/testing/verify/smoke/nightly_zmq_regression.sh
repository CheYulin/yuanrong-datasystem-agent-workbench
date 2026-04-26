#!/usr/bin/env bash
# 夜间/长时间 ZMQ metrics 相关回归：rsync → 远程 Bazel 单测 + bazel 构建 whl + run_smoke
# 与 rfc/2026-04-zmq-rpc-metrics-0.8.1/sequence_diagram.puml 验收路径一致。
# 所有日志写入本地 RESULTS_DIR，并 scp 远程 results/nightlies/zmq_metrics_<STAMP> 到 RESULTS_DIR/remote_mirror/
#
# 用法（在 agent-workbench 根目录或任意目录）:
#   export DATASYSTEM_ROOT="/path/to/yuanrong-datasystem"
#   export ZMQ_SMOKE_REMOTE="xqyun-32c32g"
#   export RESULTS_DIR="${PWD}/results/nightlies/zmq_metrics_$(date +%Y%m%d_%H%M%S)"
#   bash scripts/testing/verify/smoke/nightly_zmq_regression.sh
#
# 后台:
#   mkdir -p results/nightlies && RESULTS_DIR=.../zmq_metrics_$(date +%Y%m%d_%H%M%S) && mkdir -p "$RESULTS_DIR" && \
#   nohup env RESULTS_DIR="$RESULTS_DIR" bash path/to/nightly_zmq_regression.sh > "$RESULTS_DIR/nohup_local.out" 2>&1 & echo $!
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_ROOT="$(cd "${SCRIPT_DIR}/../../../../" && pwd)"
DS_ROOT="${DATASYSTEM_ROOT:-$(cd "${WORKBENCH_ROOT}/../yuanrong-datasystem" 2>/dev/null && pwd || true)}"
REMOTE="${ZMQ_SMOKE_REMOTE:-xqyun-32c32g}"
# 若外部已建目录并传入 RESULTS_DIR，从目录名同步 STAMP，避免与远程 zmq_metrics_<STAMP> 不一致
if [[ -n "${RESULTS_DIR:-}" ]]; then
  if [[ "$(basename "$RESULTS_DIR")" =~ ^zmq_metrics_(.+)$ ]]; then
    STAMP="${BASH_REMATCH[1]}"
  else
    STAMP="$(date +%Y%m%d_%H%M%S)"
  fi
else
  STAMP="$(date +%Y%m%d_%H%M%S)"
  RESULTS_DIR="${WORKBENCH_ROOT}/results/nightlies/zmq_metrics_${STAMP}"
fi
# rsync 目标: host:~/workspace/git-repos/...
REMOTE_BENCH_PREFIX="${REMOTE}:~/workspace/git-repos"

mkdir -p "${RESULTS_DIR}"

{
  echo "=== nightly_zmq_regression ==="
  echo "STAMP=${STAMP}"
  echo "WORKBENCH_ROOT=${WORKBENCH_ROOT}"
  echo "DS_ROOT=${DS_ROOT}"
  echo "RESULTS_DIR=${RESULTS_DIR}"
  echo "REMOTE=${REMOTE}"
  date -u
} | tee "${RESULTS_DIR}/00_run_info.txt"

if [[ -z "${DS_ROOT}" || ! -d "${DS_ROOT}" ]]; then
  echo "Set DATASYSTEM_ROOT to a valid yuanrong-datasystem path." | tee -a "${RESULTS_DIR}/00_run_info.txt" >&2
  exit 1
fi

echo "== [1/3] rsync datasystem + workbench -> ${REMOTE} ==" | tee -a "${RESULTS_DIR}/01_rsync.log"
# 镜像代码树；不删远程多余文件，避免误伤
rsync -az \
  --exclude .git/ --exclude bazel-out/ --exclude bazel-bin/ --exclude output/ --exclude build/ \
  "${DS_ROOT}/" "${REMOTE_BENCH_PREFIX}/yuanrong-datasystem/" 2>&1 | tee -a "${RESULTS_DIR}/01_rsync.log"
rsync -az --exclude .git/ \
  "${WORKBENCH_ROOT}/" "${REMOTE_BENCH_PREFIX}/yuanrong-datasystem-agent-workbench/" 2>&1 | tee -a "${RESULTS_DIR}/01_rsync.log"

echo "== [2/3] remote Bazel unit tests ==" | tee -a "${RESULTS_DIR}/02_bazel_tests.log"
ssh -o BatchMode=yes "${REMOTE}" bash -s <<'REMOTE_BAZEL' 2>&1 | tee -a "${RESULTS_DIR}/02_bazel_tests.log"
set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${HOME}/workspace/git-repos/yuanrong-datasystem"
bazel test \
  //tests/ut/common/rpc:zmq_metrics_test \
  //tests/ut/common/metrics:metrics_test \
  --test_output=errors
REMOTE_BAZEL

echo "== [3/3] remote build + run_smoke ==" | tee -a "${RESULTS_DIR}/03_run_smoke.log"
# 使用 cat|ssh|tee，避免 `ssh|tee <<EOF` 时 here-doc 被接到 tee 上的 bash 行为
# shellcheck disable=SC2029
{
  cat <<SMOKE_REMOTE
set -euo pipefail
export DS_OPENSOURCE_DIR="\${HOME}/.cache/yuanrong-datasystem-third-party"
mkdir -p "\${DS_OPENSOURCE_DIR}"
STAMP='${STAMP}'
DS="\${HOME}/workspace/git-repos/yuanrong-datasystem"
WB="\${HOME}/workspace/git-repos/yuanrong-datasystem-agent-workbench"
ON_REMOTE_RESULT="\${WB}/results/nightlies/zmq_metrics_\${STAMP}"
mkdir -p "\${ON_REMOTE_RESULT}"
cd "\${DS}"
bash build.sh -b bazel -t build -j "\$(nproc)" 2>&1 | tee "\${ON_REMOTE_RESULT}/bazel_build.log"
WHL="\$(find bazel-bin output -maxdepth 8 -name 'openyuanrong_datasystem-*.whl' 2>/dev/null | head -1 || true)"
if [[ -n "\${WHL}" ]]; then
  python3 -m pip install --user --force-reinstall "\${WHL}" 2>&1 | tee -a "\${ON_REMOTE_RESULT}/pip_install.log"
fi
cd "\${WB}/scripts/testing/verify/smoke"
python3 run_smoke.py 2>&1 | tee "\${ON_REMOTE_RESULT}/run_smoke.log"
ls -la "\${ON_REMOTE_RESULT}" 2>&1 | tee -a "\${ON_REMOTE_RESULT}/ls.txt"
echo "REMOTE_STAMP done: \${STAMP}"
SMOKE_REMOTE
} | ssh -o BatchMode=yes "${REMOTE}" bash -s 2>&1 | tee -a "${RESULTS_DIR}/03_run_smoke.log"

echo "== [4/4] scp remote artifacts ==" | tee -a "${RESULTS_DIR}/04_scp.log"
mkdir -p "${RESULTS_DIR}/remote_mirror"
if scp -o BatchMode=yes -r \
  "${REMOTE}:~/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/nightlies/zmq_metrics_${STAMP}/" \
  "${RESULTS_DIR}/remote_mirror/" 2>&1 | tee -a "${RESULTS_DIR}/04_scp.log"
then
  echo "scp ok" | tee -a "${RESULTS_DIR}/04_scp.log"
else
  echo "WARN: scp failed; full console is in 03_run_smoke.log" | tee -a "${RESULTS_DIR}/04_scp.log"
fi

{
  echo "=== done ==="
  date -u
  echo "Local RESULTS_DIR=${RESULTS_DIR}"
  echo "RFC: rfc/2026-04-zmq-rpc-metrics-0.8.1/sequence_diagram.puml"
} | tee -a "${RESULTS_DIR}/99_done.txt"
echo "Nightly run finished. Artifacts: ${RESULTS_DIR}"
