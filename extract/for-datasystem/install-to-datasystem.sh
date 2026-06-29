#!/usr/bin/env bash
# Install extract/for-datasystem/.skills into yuanrong-datasystem/.skills/
#
# Usage:
#   bash extract/for-datasystem/install-to-datasystem.sh [DATASYSTEM_ROOT]
#
# Default DATASYSTEM_ROOT: sibling ../yuanrong-datasystem from workbench root.

set -euo pipefail

EXTRACT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WB_ROOT="$(cd "${EXTRACT_DIR}/../.." && pwd)"
DS_ROOT="${1:-${WB_ROOT}/../yuanrong-datasystem}"

if [[ ! -d "${EXTRACT_DIR}/.skills" ]]; then
  echo "Missing ${EXTRACT_DIR}/.skills — run: python3 extract/for-datasystem/build_extract.py" >&2
  exit 1
fi

if [[ ! -f "${DS_ROOT}/build.sh" ]]; then
  echo "Not a datasystem repo: ${DS_ROOT}" >&2
  exit 1
fi

echo "Installing ds-build, ds-dev, ds-daily, ds-harness → ${DS_ROOT}/.skills/"
mkdir -p "${DS_ROOT}/.skills"
rsync -av "${EXTRACT_DIR}/.skills/" "${DS_ROOT}/.skills/"

echo "Done. Verify:"
echo "  cd ${DS_ROOT}"
echo "  python3 .skills/ds-harness/scripts/ds_harness.py build --dry-run --json"
