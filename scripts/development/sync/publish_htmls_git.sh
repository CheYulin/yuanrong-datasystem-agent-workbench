#!/usr/bin/env bash
# Publish yche.me content via git on xqyun (canonical). No local htmls/ copy required.
#
# Usage:
#   bash scripts/development/sync/publish_htmls_git.sh pull
#   bash scripts/development/sync/publish_htmls_git.sh status
#   bash scripts/development/sync/publish_htmls_git.sh push   # after you committed on xqyun

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${SCRIPT_DIR}/../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
# shellcheck source=../../lib/load_nodes.sh
. "${LIB_DIR}/load_nodes.sh"
# shellcheck source=../../lib/common.sh
. "${LIB_DIR}/common.sh"

WEB_ROOT="${WEB_ROOT:-/var/www/html}"

_on_publish_host() {
  local host
  host="$(node_ssh_host "${PUBLISH_NODE}")"
  local short
  short="$(hostname -s 2>/dev/null || hostname)"
  [[ "${short}" == "${host}" ]] && return 0
  [[ -f "${WEB_ROOT}/.git" ]] && return 0
  return 1
}

_run_git() {
  git -c "safe.directory=${WEB_ROOT}" -C "${WEB_ROOT}" "$@"
}

usage() {
  cat <<'EOF'
Usage:
  bash scripts/development/sync/publish_htmls_git.sh <pull|status|push>

  pull    — git pull on xqyun /var/www/html
  status  — git status -sb on xqyun
  push    — git push on xqyun (after commit there)

Edit HTML on xqyun (or ~/yche-me-site clone), commit, then push/pull.
See .cursor/skills/wb-html-publish/SKILL.md
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

PUBLISH_NODE="$(node_role_default publish_web)"
SSH_USER="$(node_ssh_user "${PUBLISH_NODE}")"
SSH_HOST="$(node_ssh_host "${PUBLISH_NODE}")"
REMOTE="${SSH_USER}@${SSH_HOST}"
WR="$(node_web_root "${PUBLISH_NODE}")"
[[ -n "${WR}" ]] && WEB_ROOT="${WR}"

cmd="$1"
case "${cmd}" in
  pull)
    if _on_publish_host; then
      log_info "git pull on local ${WEB_ROOT}"
      _run_git pull
    else
      log_info "git pull on ${REMOTE}:${WEB_ROOT}"
      ssh "${REMOTE}" "git -c safe.directory=${WEB_ROOT} -C ${WEB_ROOT} pull"
    fi
    ;;
  status)
    if _on_publish_host; then
      _run_git status -sb
    else
      ssh "${REMOTE}" "git -c safe.directory=${WEB_ROOT} -C ${WEB_ROOT} status -sb"
    fi
    ;;
  push)
    if _on_publish_host; then
      log_info "git push on local ${WEB_ROOT}"
      _run_git push
    else
      ssh "${REMOTE}" "git -c safe.directory=${WEB_ROOT} -C ${WEB_ROOT} push"
    fi
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "publish_htmls_git.sh: unknown command: ${cmd}" >&2
    usage
    exit 2
    ;;
esac

log_info "Done."
