#!/usr/bin/env bash
# Per-skill verification — single entry for tiantiyun/xqyun/local.
#
# Usage:
#   bash scripts/harness/verify_skill.sh --skill wb-build
#   bash scripts/harness/verify_skill.sh --skill wb-html-publish --sync
#   bash scripts/harness/verify_skill.sh --all --dry-run
#   bash scripts/harness/verify_skill.sh --skill wb-dev --local   # on-node only
set -euo pipefail

HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HARNESS_DIR}/verify_skill.py" "$@"
