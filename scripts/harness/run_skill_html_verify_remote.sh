#!/usr/bin/env bash
# wb-html-publish verification on xqyun-32c32g.
#
# Usage:
#   bash scripts/harness/run_skill_html_verify_remote.sh
#   bash scripts/harness/run_skill_html_verify_remote.sh --skip-sync
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SKIP_SYNC=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-sync) SKIP_SYNC=1; shift ;;
    -h|--help)
      sed -n '1,10p' "$0" | tail -n +2
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

OPTS=()
(( SKIP_SYNC )) || OPTS+=(--sync)

exec bash "${HARNESS_DIR}/verify_skill.sh" --skill wb-html-publish "${OPTS[@]}"
