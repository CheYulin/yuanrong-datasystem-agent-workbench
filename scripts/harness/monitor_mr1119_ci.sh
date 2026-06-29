#!/usr/bin/env bash
# Track openEuler CI gate for MR 1119 (client direct read flow).
#
# Usage:
#   bash scripts/harness/monitor_mr1119_ci.sh
#   bash scripts/harness/monitor_mr1119_ci.sh --once   # print only, no state file update noise
#
# Crontab (every 5 min):
#   */5 * * * * /home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/harness/monitor_mr1119_ci.sh

set -euo pipefail

PR_ID="${PR_ID:-1119}"
BRANCH="${BRANCH:-feature/client-direct-read-flow}"
JENKINS_TRIGGER_URL="${JENKINS_TRIGGER_URL:-https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/trigger/job/yuanrong-datasystem}"
SCAN_BUILDS="${SCAN_BUILDS:-40}"
WORKBENCH="${WORKBENCH:-/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench}"
WORKTREE="${WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/client-direct-read-flow}"
LOG_DIR="${LOG_DIR:-${WORKBENCH}/logs}"
STATE_FILE="${STATE_FILE:-${LOG_DIR}/mr1119-ci.state}"
MR_URL="${MR_URL:-https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/${PR_ID}}"

mkdir -p "${LOG_DIR}"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

log() { echo "[$(ts)] $*"; }

fetch_console_head() {
  local build="$1"
  curl -fsSL --max-time 25 "${JENKINS_TRIGGER_URL}/${build}/consoleText" 2>/dev/null | head -5 || true
}

find_latest_pr_build() {
  local last
  last="$(curl -fsSL --max-time 20 "${JENKINS_TRIGGER_URL}/lastBuild/api/json" | python3 -c 'import sys,json; print(json.load(sys.stdin)["number"])')"
  local n="${last}"
  local min=$((last - SCAN_BUILDS))
  [[ "${min}" -lt 1 ]] && min=1
  while [[ "${n}" -ge "${min}" ]]; do
    if fetch_console_head "${n}" | grep -q "PR ${PR_ID} \\["; then
      echo "${n}"
      return 0
    fi
    n=$((n - 1))
  done
  return 1
}

parse_trigger_status() {
  local build="$1"
  local console
  console="$(curl -fsSL --max-time 45 "${JENKINS_TRIGGER_URL}/${build}/consoleText" 2>/dev/null || true)"

  local pr_line build_result acl_line codecheck codecheck_url
  pr_line="$(printf '%s\n' "${console}" | grep -m1 "PR ${PR_ID} \\[" || true)"
  build_result="$(curl -fsSL --max-time 15 "${JENKINS_TRIGGER_URL}/${build}/api/json" 2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("result") or ("RUNNING" if d.get("building") else "UNKNOWN"))' 2>/dev/null || echo UNKNOWN)"
  acl_line="$(printf '%s\n' "${console}" | grep -m1 'check result: ACL=' || true)"
  codecheck_url="$(printf '%s\n' "${console}" | grep -oE 'https://www.openlibing.com/apps/entryCheckDashCode/[^ ]+' | tail -1 || true)"

  if printf '%s\n' "${console}" | grep -q 'check code pass'; then
    codecheck="PASS"
  elif printf '%s\n' "${console}" | grep -q 'check code fail'; then
    codecheck="FAIL"
  elif printf '%s\n' "${console}" | grep -E 'check yuanrong-datasystem code|codecheck probably' >/dev/null; then
    codecheck="RUNNING"
  else
    codecheck="UNKNOWN"
  fi

  local x86="" aarch64="" bazel_x86="" bazel_arm=""
  if printf '%s\n' "${console}" | grep -q 'x86-64 » yuanrong-datasystem.* started'; then
    x86="RUNNING"
  fi
  if printf '%s\n' "${console}" | grep -q 'aarch64 » yuanrong-datasystem.* started'; then
    aarch64="RUNNING"
  fi
  if printf '%s\n' "${console}" | grep -q 'Test_Datasystem_Bazel_x86.* started'; then
    bazel_x86="RUNNING"
  fi
  if printf '%s\n' "${console}" | grep -q 'Test_Datasystem_Bazel_arm.* started'; then
    bazel_arm="RUNNING"
  fi

  # Child build numbers from "Waiting for the completion of" section (best effort)
  local child_x86 child_aarch64 child_bazel_x86 child_bazel_arm
  child_x86="$(printf '%s\n' "${console}" | grep -oE 'x86-64/yuanrong-datasystem,[0-9]+' | tail -1 | cut -d, -f2 || true)"
  child_aarch64="$(printf '%s\n' "${console}" | grep -oE 'aarch64/yuanrong-datasystem,[0-9]+' | tail -1 | cut -d, -f2 || true)"
  child_bazel_x86="$(printf '%s\n' "${console}" | grep -oE 'Test_Datasystem_Bazel_x86,[0-9]+' | tail -1 | cut -d, -f2 || true)"
  child_bazel_arm="$(printf '%s\n' "${console}" | grep -oE 'Test_Datasystem_Bazel_arm,[0-9]+' | tail -1 | cut -d, -f2 || true)"

  child_build_result() {
    local url="$1"
    [[ -z "${url}" ]] && return 0
    curl -fsSL --max-time 15 "${url}/api/json" 2>/dev/null \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d.get("result") or ("RUNNING" if d.get("building") else "?"))' 2>/dev/null || echo "?"
  }

  [[ -n "${child_x86}" ]] && x86="$(child_build_result "https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/x86-64/job/yuanrong-datasystem/${child_x86}")"
  [[ -n "${child_aarch64}" ]] && aarch64="$(child_build_result "https://ci.openeuler.openatom.cn/job/multiarch/job/openeuler/job/aarch64/job/yuanrong-datasystem/${child_aarch64}")"
  [[ -n "${child_bazel_x86}" ]] && bazel_x86="$(child_build_result "https://ci.openeuler.openatom.cn/job/multiarch/job/manual-jobs/job/openeuler/job/openyuanrong/job/Test_Datasystem_Bazel_x86/${child_bazel_x86}")"
  [[ -n "${child_bazel_arm}" ]] && bazel_arm="$(child_build_result "https://ci.openeuler.openatom.cn/job/multiarch/job/manual-jobs/job/openeuler/job/openyuanrong/job/Test_Datasystem_Bazel_arm/${child_bazel_arm}")"

  local git_head=""
  if [[ -e "${WORKTREE}/.git" ]]; then
    git_head="$(git -C "${WORKTREE}" rev-parse --short HEAD 2>/dev/null || true)"
  fi

  cat <<EOF
build=${build}
trigger_result=${build_result}
pr_line=${pr_line}
git_head=${git_head}
check_code=${codecheck}
check_code_acl=${acl_line}
codecheck_url=${codecheck_url}
x86_64=${x86:-pending}
aarch64=${aarch64:-pending}
bazel_x86=${bazel_x86:-pending}
bazel_arm=${bazel_arm:-pending}
trigger_console=${JENKINS_TRIGGER_URL}/${build}/console
mr=${MR_URL}
EOF
}

main() {
  local build
  if ! build="$(find_latest_pr_build)"; then
    log "MR ${PR_ID}: no trigger build found in last ${SCAN_BUILDS} runs"
    exit 0
  fi

  local snapshot
  snapshot="$(parse_trigger_status "${build}")"
  local summary
  summary="$(printf '%s\n' "${snapshot}" | grep -E '^(build|trigger_result|git_head|check_code|x86_64|aarch64|bazel_x86|bazel_arm)=' | paste -sd' | ' -)"

  if [[ -f "${STATE_FILE}" ]] && cmp -s <(printf '%s\n' "${snapshot}") "${STATE_FILE}"; then
    log "MR ${PR_ID} #${build} unchanged | ${summary}"
    exit 0
  fi

  local prev=""
  [[ -f "${STATE_FILE}" ]] && prev="$(cat "${STATE_FILE}")"
  printf '%s\n' "${snapshot}" > "${STATE_FILE}"

  log "=== MR ${PR_ID} CI update (trigger #${build}) ==="
  printf '%s\n' "${snapshot}" | while IFS= read -r line; do log "  ${line}"; done

  if [[ -n "${prev}" ]]; then
    log "--- delta ---"
    diff -u <(printf '%s\n' "${prev}") <(printf '%s\n' "${snapshot}") | tail -n +3 | while IFS= read -r line; do
      [[ -n "${line}" ]] && log "  ${line}"
    done || true
  fi

  if printf '%s\n' "${snapshot}" | grep -q 'trigger_result=SUCCESS' \
    && printf '%s\n' "${snapshot}" | grep -q 'check_code=PASS'; then
    local all_green=1
    for k in x86_64 aarch64 bazel_x86 bazel_arm; do
      val="$(printf '%s\n' "${snapshot}" | grep "^${k}=" | cut -d= -f2-)"
      if [[ "${val}" != "SUCCESS" && "${val}" != "pending" && "${val}" != "RUNNING" && "${val}" != "?" ]]; then
        all_green=0
      fi
    done
    if [[ "${all_green}" -eq 1 ]]; then
      log ">>> MR ${PR_ID} gate looks GREEN (verify child jobs manually if pending)"
    fi
  fi
}

main "$@"
