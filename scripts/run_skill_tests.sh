#!/usr/bin/env bash
# TDD contract tests for workbench .skills/ (tool-neutral canonical).
# Run on tiantiyun: bash scripts/harness/run_skill_verification_remote.sh --tests-only
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

for dir in .skills/*/tests .skills/tests; do
  [[ -d "${dir}" ]] || continue
  echo "==> ${dir}"
  python3 -m unittest discover -s "${dir}" -p 'test_*.py' -v
done

echo "All workbench skill tests passed."
