#!/usr/bin/env bash
# Isolated remote verify via git worktree (separate from rsync tree and other worktrees).
#
# Remote layout (all under /home/cache — does NOT touch /root/workspace rsync tree):
#   GIT_MAIN=/home/cache/git-repos/yuanrong-datasystem
#   WORKTREE=${GIT_MAIN}/.worktrees/${WORKTREE_SLUG}
#   BUILD_DIR=/home/cache/build-wt-${WORKTREE_SLUG}
#   VERIFY_LOG_DIR=/home/cache/verify-logs/wt-${WORKTREE_SLUG}
#
# Usage:
#   bash scripts/testing/verify/run_worktree_verify_remote.sh \
#     --worktree client-direct-read-flow \
#     --branch feature/client-direct-read-flow \
#     --sync-local --phase st
#
#   ST_CTEST_REGEX='ClientDirectRead' \
#     bash scripts/testing/verify/run_worktree_verify_remote.sh \
#       --worktree client-direct-read-flow --phase st --skip-build
#
#   # Perf benchmarks (requires ENABLE_PERF=on build + DS_DIRECT_READ_PERF=1):
#   ENABLE_PERF=on DS_DIRECT_READ_PERF=1 ST_CTEST_REGEX='CrossNode.*LatencyBenchmark' \
#     bash scripts/testing/verify/run_worktree_verify_remote.sh ...
#
# Environment:
#   BUILD_JOBS — cmake build parallelism (default 40)
#   CTEST_JOBS — ctest parallelism for st/ut (default: ut=40, st=8)
#   ST_CTEST_LABEL_EXCLUDE — ctest -LE labels (default level2; cleared when DS_DIRECT_READ_PERF=1)
#   WORKTREE_SLUG, WORKTREE_BRANCH, GIT_CLONE_URL, LOCAL_WORKTREE
#   UT_CTEST_REGEX / ST_CTEST_REGEX (phase ut/st)
#   SKIP_SYNC — skip rsync of local WIP (use pure git pull on remote)

set -euo pipefail

VERIFY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${VERIFY_DIR}/../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
# shellcheck source=load_nodes.sh
. "${LIB_DIR}/load_nodes.sh"
# shellcheck source=remote_defaults.sh
. "${LIB_DIR}/remote_defaults.sh"
# shellcheck source=common.sh
. "${LIB_DIR}/common.sh"
# shellcheck source=timing.sh
. "${LIB_DIR}/timing.sh"

MAIN_DS="${MAIN_DS:-/home/t14s/workspace/git-repos/yuanrong-datasystem}"
NODE="${NODE_NAME:-$(node_role_default verify_st)}"
WORKTREE_SLUG=""
WORKTREE_BRANCH=""
PHASE="st"
DRY_RUN=0
SKIP_SYNC=1
SKIP_BUILD=0
SYNC_LOCAL=0
BUILD_BACKEND="${BUILD_BACKEND:-cmake}"
BUILD_JOBS="${BUILD_JOBS:-40}"
CTEST_JOBS_UT="${CTEST_JOBS_UT:-40}"
CTEST_JOBS_ST="${CTEST_JOBS_ST:-8}"
GIT_CLONE_URL="${GIT_CLONE_URL:-git@github.com:CheYulin/yuanrong-datasystem.git}"

usage() {
  sed -n '1,26p' "$0" | tail -n +2
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worktree) WORKTREE_SLUG="$2"; shift 2 ;;
    --branch) WORKTREE_BRANCH="$2"; shift 2 ;;
    --node) NODE="$2"; shift 2 ;;
    --phase) PHASE="$2"; shift 2 ;;
    --ctest-regex) CTEST_REGEX_OVERRIDE="$2"; shift 2 ;;
    --sync-local) SYNC_LOCAL=1; SKIP_SYNC=0; shift ;;
    --skip-sync) SKIP_SYNC=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown: $1" >&2; usage 2 ;;
  esac
done

[[ -n "${WORKTREE_SLUG}" ]] || { echo "--worktree <slug> required" >&2; usage 2; }
WORKTREE_BRANCH="${WORKTREE_BRANCH:-feature/${WORKTREE_SLUG}}"

LOCAL_WORKTREE="${LOCAL_WORKTREE:-${MAIN_DS}/.worktrees/${WORKTREE_SLUG}}"
REMOTE_GIT_MAIN="/home/cache/git-repos/yuanrong-datasystem"
REMOTE_WORKTREE="${REMOTE_GIT_MAIN}/.worktrees/${WORKTREE_SLUG}"
BUILD_DIR="${BUILD_DIR:-/home/cache/build-wt-${WORKTREE_SLUG}}"
VERIFY_LOG_DIR="${VERIFY_LOG_DIR:-/home/cache/verify-logs/wt-${WORKTREE_SLUG}}"

init_remote "${NODE}"

UT_CTEST_REGEX="${UT_CTEST_REGEX:-DirectRead|ObjectReadAccess|read_access}"
ST_CTEST_REGEX="${ST_CTEST_REGEX:-ClientDirectRead|direct_read|ObjectClientDirectRead}"
ST_CTEST_LABEL_EXCLUDE="${ST_CTEST_LABEL_EXCLUDE:-level2}"
ST_CTEST_EXCLUDE="${ST_CTEST_EXCLUDE:-}"
CTEST_REGEX="${CTEST_REGEX_OVERRIDE:-}"
if [[ -z "${CTEST_REGEX}" ]]; then
  case "${PHASE}" in
    ut) CTEST_REGEX="${UT_CTEST_REGEX}" ;;
    st) CTEST_REGEX="${ST_CTEST_REGEX}" ;;
    *) CTEST_REGEX="${ST_CTEST_REGEX}" ;;
  esac
fi

CTEST_REGEX_QUOTED="$(printf '%q' "${CTEST_REGEX}")"

if [[ "${SYNC_LOCAL}" -eq 1 && ! -d "${LOCAL_WORKTREE}/src" ]]; then
  log_error "Local worktree missing: ${LOCAL_WORKTREE}"
  log_error "Create: git -C ${MAIN_DS} worktree add .worktrees/${WORKTREE_SLUG} ${WORKTREE_BRANCH}"
  exit 1
fi

LOCAL_HEAD="$(git -C "${LOCAL_WORKTREE}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
LOCAL_BRANCH="$(git -C "${LOCAL_WORKTREE}" branch --show-current 2>/dev/null || echo detached)"

banner "Worktree verify: ${WORKTREE_SLUG}"
log_info "Branch: ${WORKTREE_BRANCH}"
log_info "Local:  ${LOCAL_WORKTREE} (${LOCAL_BRANCH}@${LOCAL_HEAD})"
log_info "Remote git main: ${REMOTE_GIT_MAIN}"
log_info "Remote worktree: ${REMOTE_WORKTREE}"
log_info "Build:  ${BUILD_DIR}"
log_info "Logs:   ${VERIFY_LOG_DIR}"
log_info "Phase:  ${PHASE}"
[[ "${PHASE}" != "setup" ]] && log_info "Regex:  ${CTEST_REGEX}"
[[ "${PHASE}" == "st" && -n "${ST_CTEST_EXCLUDE}" ]] && log_info "ST exclude: ${ST_CTEST_EXCLUDE}"
[[ "${PHASE}" == "st" && -n "${ST_CTEST_LABEL_EXCLUDE}" ]] && log_info "ST label exclude: ${ST_CTEST_LABEL_EXCLUDE}"

if [[ "${SYNC_LOCAL}" -eq 1 ]]; then
  RSYNC_IGNORE="${MAIN_DS}/.skills/ds-harness/scripts/sync/sync_workspace.rsyncignore"
  rsync_opts=(-avz --delete --exclude='.git' --exclude='build/')
  if [[ -f "${RSYNC_IGNORE}" ]]; then
    rsync_opts+=(--exclude-from="${RSYNC_IGNORE}")
  fi
  (( DRY_RUN )) && rsync_opts+=(--dry-run)
  log_info "Will rsync local WIP -> remote worktree after git worktree ensure"
fi

REMOTE_BODY=$(cat <<'REMOTE_EOF'
set -eo pipefail
: "${WT_GIT_MAIN:?}" "${WT_WORKTREE:?}" "${WT_BUILD_DIR:?}" "${WT_VERIFY_LOG_DIR:?}"
: "${WT_BRANCH:?}" "${WT_CLONE_URL:?}" "${WT_SKIP_BUILD:?}" "${WT_BUILD_BACKEND:?}"
: "${WT_PHASE:?}" "${WT_CTEST_REGEX_B64:?}" "${WT_SYNC_LOCAL:?}"
WT_ALLOW_ORPHAN="${WT_ALLOW_ORPHAN:-0}"

GIT_MAIN="${WT_GIT_MAIN}"
WORKTREE="${WT_WORKTREE}"
BUILD_DIR="${WT_BUILD_DIR}"
VERIFY_LOG_DIR="${WT_VERIFY_LOG_DIR}"
BRANCH="${WT_BRANCH}"
CLONE_URL="${WT_CLONE_URL}"
SKIP_BUILD="${WT_SKIP_BUILD}"
BUILD_BACKEND="${WT_BUILD_BACKEND}"
PHASE="${WT_PHASE}"
CTEST_REGEX="$(printf '%s' "${WT_CTEST_REGEX_B64}" | base64 -d)"
SYNC_LOCAL="${WT_SYNC_LOCAL}"

mkdir -p "$(dirname "${GIT_MAIN}")" "${VERIFY_LOG_DIR}" "${BUILD_DIR}/tests/st/cluster"
THIRD_PARTY="${WT_THIRD_PARTY:-/home/ds-thirdparty-cache}"
mkdir -p "${THIRD_PARTY}"
export DS_OPENSOURCE_DIR="${DS_OPENSOURCE_DIR:-${THIRD_PARTY}}"

ensure_main_repo() {
  if [[ ! -d "${GIT_MAIN}/.git" ]]; then
    echo "[git] clone ${CLONE_URL} -> ${GIT_MAIN}"
    git clone "${CLONE_URL}" "${GIT_MAIN}"
  fi
  git -C "${GIT_MAIN}" fetch origin --prune
}

repair_or_add_worktree() {
  mkdir -p "${GIT_MAIN}/.worktrees"
  if [[ -d "${WORKTREE}" && ! -e "${WORKTREE}/.git" ]]; then
    echo "[git] remove orphan worktree path ${WORKTREE} (no .git)"
    rm -rf "${WORKTREE}"
    git -C "${GIT_MAIN}" worktree prune || true
  fi
  if [[ -e "${WORKTREE}/.git" ]]; then
    if git -C "${WORKTREE}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "[git] reuse worktree ${WORKTREE}"
      git -C "${WORKTREE}" fetch origin "${BRANCH}" 2>/dev/null || true
      git -C "${WORKTREE}" checkout "${BRANCH}"
      git -C "${WORKTREE}" pull --ff-only origin "${BRANCH}" 2>/dev/null || true
      return 0
    fi
    echo "[git] remove broken worktree path ${WORKTREE}"
    rm -rf "${WORKTREE}"
    git -C "${GIT_MAIN}" worktree prune || true
  fi
  if git -C "${GIT_MAIN}" show-ref --verify --quiet "refs/heads/${BRANCH}"; then
    git -C "${GIT_MAIN}" worktree add "${WORKTREE}" "${BRANCH}"
  elif git -C "${GIT_MAIN}" show-ref --verify --quiet "refs/remotes/origin/${BRANCH}"; then
    git -C "${GIT_MAIN}" worktree add -B "${BRANCH}" "${WORKTREE}" "origin/${BRANCH}"
  elif [[ "${WT_ALLOW_ORPHAN}" -eq 1 ]]; then
    local base_ref="origin/main"
    if ! git -C "${GIT_MAIN}" show-ref --verify --quiet refs/remotes/origin/main; then
      base_ref="origin/master"
    fi
    if ! git -C "${GIT_MAIN}" show-ref --verify --quiet "refs/remotes/${base_ref#origin/}"; then
      base_ref="$(git -C "${GIT_MAIN}" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo origin/main)"
    fi
    echo "[git] branch ${BRANCH} not on origin; worktree from ${base_ref} (--sync-local will overlay)"
    git -C "${GIT_MAIN}" worktree add -B "${BRANCH}" "${WORKTREE}" "${base_ref}"
  else
    echo "[git] branch missing: ${BRANCH} (push to origin or use --sync-local)" >&2
    exit 1
  fi
}

STAMP="$(date -u +%Y%m%d_%H%M%S)"
LOG="${VERIFY_LOG_DIR}/${PHASE}_${STAMP}.log"
SUMMARY="${VERIFY_LOG_DIR}/latest_summary.md"
ln -sf "${LOG}" "${VERIFY_LOG_DIR}/latest_${PHASE}.log"

ensure_main_repo
repair_or_add_worktree
git config --global --add safe.directory "${GIT_MAIN}" 2>/dev/null || true
git config --global --add safe.directory "${WORKTREE}" 2>/dev/null || true

{
  echo "# Worktree verify (${PHASE})"
  echo "- time: $(date -Is)"
  echo "- host: $(hostname -s)"
  echo "- branch: ${BRANCH}"
  echo "- worktree: ${WORKTREE}"
  echo "- build: ${BUILD_DIR}"
  echo "- head: $(git -C "${WORKTREE}" log -1 --oneline)"
} > "${SUMMARY}"

if [[ "${SYNC_LOCAL}" -eq 1 ]]; then
  echo "[sync-local] waiting for rsync from local (worktree must exist)" | tee -a "${LOG}"
fi

cd "${WORKTREE}"

BUILD_JOBS="${WT_BUILD_JOBS:-40}"
CTEST_JOBS_UT="${WT_CTEST_JOBS_UT:-40}"
CTEST_JOBS_ST="${WT_CTEST_JOBS_ST:-8}"
ST_LABEL_EXCLUDE="${WT_ST_CTEST_LABEL_EXCLUDE:-level2}"
if [[ "${ST_LABEL_EXCLUDE}" == "none" || -n "${WT_DS_DIRECT_READ_PERF:-}" ]]; then
  ST_LABEL_EXCLUDE=""
fi
ST_NAME_EXCLUDE="${WT_ST_CTEST_EXCLUDE:-}"

if [[ "${SKIP_BUILD}" -eq 0 && "${PHASE}" != "setup" ]]; then
  echo "[build] $(date -Is) backend=${BUILD_BACKEND} jobs=${BUILD_JOBS} enable_perf=${WT_ENABLE_PERF:-0}" | tee -a "${LOG}"
  BUILD_ENV=()
  if [[ "${WT_ENABLE_PERF:-}" == "1" || -n "${WT_DS_DIRECT_READ_PERF:-}" ]]; then
    BUILD_ENV=(env ENABLE_PERF=on)
    BUILD_PERF_ARGS=(-p on)
  else
    BUILD_PERF_ARGS=()
  fi
  "${BUILD_ENV[@]}" bash build.sh -t build -B "${BUILD_DIR}" -b "${BUILD_BACKEND}" -j "${BUILD_JOBS}" \
    "${BUILD_PERF_ARGS[@]}" -i on 2>&1 | tee -a "${LOG}" | tail -80
fi

case "${PHASE}" in
  setup)
    echo "[setup] worktree ready" | tee -a "${LOG}"
    exit 0
    ;;
  sync)
    echo "[sync] worktree ready (use --sync-local from local host)" | tee -a "${LOG}"
    exit 0
    ;;
  ut)
    echo "[ut] regex=${CTEST_REGEX} jobs=${CTEST_JOBS_UT}" | tee -a "${LOG}"
    set +e
    ctest --test-dir "${BUILD_DIR}" --output-on-failure \
      -R "${CTEST_REGEX}" -E ' st |IntegrationTest' -j "${CTEST_JOBS_UT}" 2>&1 | tee -a "${LOG}"
    rc=${PIPESTATUS[0]}
    set -e
    grep -oE '[0-9]+ - [^(]+ \(Failed\)' "${LOG}" 2>/dev/null | sort -u > "${VERIFY_LOG_DIR}/ut_failures.txt" || true
    exit "${rc}"
    ;;
  st)
    echo "[st] regex=${CTEST_REGEX} jobs=${CTEST_JOBS_ST} label_exclude=${ST_LABEL_EXCLUDE:-none} name_exclude=${ST_NAME_EXCLUDE:-none}" | tee -a "${LOG}"
    ln -sf "${WORKTREE}/tests/st/cluster/mock_obs_service.py" \
      "${BUILD_DIR}/tests/st/cluster/mock_obs_service.py" 2>/dev/null || true
    if [[ -n "${WT_DS_DIRECT_READ_PERF:-}" ]]; then
      export DS_DIRECT_READ_PERF="${WT_DS_DIRECT_READ_PERF}"
      export DS_DIRECT_READ_PERF_ITERS="${WT_DS_DIRECT_READ_PERF_ITERS:-1000}"
      export DS_DIRECT_READ_PERF_WARMUP="${WT_DS_DIRECT_READ_PERF_WARMUP:-50}"
      export DS_DIRECT_READ_PERF_SIZE="${WT_DS_DIRECT_READ_PERF_SIZE:-262144}"
      echo "[st] DS_DIRECT_READ_PERF=${DS_DIRECT_READ_PERF} iters=${DS_DIRECT_READ_PERF_ITERS} size=${DS_DIRECT_READ_PERF_SIZE}" | tee -a "${LOG}"
    fi
    CTEST_ST_ARGS=(--test-dir "${BUILD_DIR}" --output-on-failure -R "${CTEST_REGEX}" -j "${CTEST_JOBS_ST}" --timeout 600)
    if [[ -n "${ST_LABEL_EXCLUDE}" ]]; then
      CTEST_ST_ARGS+=(-LE "${ST_LABEL_EXCLUDE}")
    fi
    if [[ -n "${ST_NAME_EXCLUDE}" ]]; then
      CTEST_ST_ARGS+=(-E "${ST_NAME_EXCLUDE}")
    fi
    set +e
    ctest "${CTEST_ST_ARGS[@]}" 2>&1 | tee -a "${LOG}"
    rc=${PIPESTATUS[0]}
    set -e
    grep -oE '[0-9]+ - [^(]+ \(Failed\)' "${LOG}" 2>/dev/null | sort -u > "${VERIFY_LOG_DIR}/st_failures.txt" || true
    exit "${rc}"
    ;;
  *)
    echo "Unknown phase: ${PHASE}" >&2
    exit 2
    ;;
esac
REMOTE_EOF
)

run_remote() {
  local regex_b64
  regex_b64="$(printf '%s' "${CTEST_REGEX}" | base64 -w0 2>/dev/null || printf '%s' "${CTEST_REGEX}" | base64)"
  ssh_remote "${REMOTE}" env \
    WT_GIT_MAIN="${REMOTE_GIT_MAIN}" \
    WT_WORKTREE="${REMOTE_WORKTREE}" \
    WT_BUILD_DIR="${BUILD_DIR}" \
    WT_VERIFY_LOG_DIR="${VERIFY_LOG_DIR}" \
    WT_BRANCH="${WORKTREE_BRANCH}" \
    WT_CLONE_URL="${GIT_CLONE_URL}" \
    WT_SKIP_BUILD="${SKIP_BUILD}" \
    WT_BUILD_BACKEND="${BUILD_BACKEND}" \
    WT_PHASE="${PHASE}" \
    WT_CTEST_REGEX_B64="${regex_b64}" \
    WT_SYNC_LOCAL="${SYNC_LOCAL}" \
    WT_ALLOW_ORPHAN="${SYNC_LOCAL}" \
    WT_DS_DIRECT_READ_PERF="${DS_DIRECT_READ_PERF:-}" \
    WT_DS_DIRECT_READ_PERF_ITERS="${DS_DIRECT_READ_PERF_ITERS:-}" \
    WT_DS_DIRECT_READ_PERF_WARMUP="${DS_DIRECT_READ_PERF_WARMUP:-}" \
    WT_DS_DIRECT_READ_PERF_SIZE="${DS_DIRECT_READ_PERF_SIZE:-}" \
    WT_ENABLE_PERF="${ENABLE_PERF:+1}" \
    WT_BUILD_JOBS="${BUILD_JOBS}" \
    WT_CTEST_JOBS_UT="${CTEST_JOBS_UT}" \
    WT_CTEST_JOBS_ST="${CTEST_JOBS_ST}" \
    WT_ST_CTEST_LABEL_EXCLUDE="${ST_CTEST_LABEL_EXCLUDE}" \
    WT_ST_CTEST_EXCLUDE="${ST_CTEST_EXCLUDE}" \
    WT_THIRD_PARTY="${DS_OPENSOURCE_DIR}" \
    bash -s <<EOF
${REMOTE_BODY}
EOF
}

if (( DRY_RUN )); then
  log_info "[dry-run] would ensure remote worktree and run phase=${PHASE}"
  exit 0
fi

# Step 1: ensure remote git worktree exists (before rsync overlay)
ssh_remote "${REMOTE}" env \
  WT_GIT_MAIN="${REMOTE_GIT_MAIN}" \
  WT_WORKTREE="${REMOTE_WORKTREE}" \
  WT_BUILD_DIR="${BUILD_DIR}" \
  WT_VERIFY_LOG_DIR="${VERIFY_LOG_DIR}" \
  WT_BRANCH="${WORKTREE_BRANCH}" \
  WT_CLONE_URL="${GIT_CLONE_URL}" \
  WT_SKIP_BUILD="1" \
  WT_BUILD_BACKEND="${BUILD_BACKEND}" \
  WT_PHASE="setup" \
  WT_CTEST_REGEX_B64="Lg==" \
  WT_SYNC_LOCAL="0" \
  WT_ALLOW_ORPHAN="${SYNC_LOCAL}" \
  bash -s <<EOF
${REMOTE_BODY}
EOF

# Step 2: overlay local WIP onto remote worktree
if [[ "${SYNC_LOCAL}" -eq 1 ]]; then
  RSYNC_IGNORE="${MAIN_DS}/.skills/ds-harness/scripts/sync/sync_workspace.rsyncignore"
  rsync_opts=(-avz --delete --exclude='.git' --exclude='build/')
  if [[ -f "${RSYNC_IGNORE}" ]]; then
    rsync_opts+=(--exclude-from="${RSYNC_IGNORE}")
  fi
  log_info "rsync ${LOCAL_WORKTREE}/ -> ${REMOTE}:${REMOTE_WORKTREE}/"
  rsync "${rsync_opts[@]}" "${LOCAL_WORKTREE}/" "${REMOTE}:${REMOTE_WORKTREE}/"
fi

# Step 3: build + test
if [[ "${PHASE}" == "setup" ]]; then
  log_info "Setup complete (worktree + optional rsync)."
  log_info "Next: run with --phase ut|st (add --skip-build if already built)."
  exit 0
fi

run_remote

log_info "Done. Fetch logs:"
log_info "  rsync -avz ${REMOTE}:${VERIFY_LOG_DIR}/ ./verify-logs-wt-${WORKTREE_SLUG}/"
