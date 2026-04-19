#!/usr/bin/env bash
# =============================================================================
# check_cpp_line_width.sh
#
# Enforces G.FMT.05-CPP (≤120 chars per line) on .h/.hpp/.cpp/.cc files in
# yuanrong-datasystem/. Skips third_party/ and auto-generated protobuf code.
#
# Usage:
#   bash vibe-coding-files/scripts/lint/check_cpp_line_width.sh           # whole repo
#   bash vibe-coding-files/scripts/lint/check_cpp_line_width.sh --staged  # only staged files
#   bash vibe-coding-files/scripts/lint/check_cpp_line_width.sh path1 path2 ...
#
# Exit codes:
#   0  no violations
#   1  one or more lines exceed 120 chars
#   2  usage error
# =============================================================================
set -euo pipefail

MAX=120
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DS_DIR="${DS_DIR:-$(cd "${SCRIPT_DIR}/../../../yuanrong-datasystem" 2>/dev/null && pwd || true)}"
[[ -d "${DS_DIR}" ]] || { echo "ERROR: yuanrong-datasystem not found (set DS_DIR=...)" >&2; exit 2; }

mode="all"
files=()

if [[ $# -gt 0 ]]; then
  case "$1" in
    --staged)
      mode="staged"
      shift
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      mode="explicit"
      files=("$@")
      ;;
  esac
fi

case "$mode" in
  staged)
    mapfile -t files < <(cd "${DS_DIR}" && git diff --cached --name-only --diff-filter=ACMR \
      | grep -E '\.(h|hpp|cpp|cc)$' || true)
    if (( ${#files[@]} == 0 )); then
      echo "[lint] no staged C++ files; nothing to check"
      exit 0
    fi
    pushd "${DS_DIR}" >/dev/null
    ;;
  all)
    mapfile -t files < <(cd "${DS_DIR}" && find src tests include \
      \( -name '*.h' -o -name '*.hpp' -o -name '*.cpp' -o -name '*.cc' \) \
      -not -path '*/third_party/*' \
      -not -name '*.pb.h' -not -name '*.pb.cc' \
      2>/dev/null || true)
    pushd "${DS_DIR}" >/dev/null
    ;;
  explicit)
    : # use files as-is from CLI
    ;;
esac

violations=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || continue
  # skip vendored / generated
  case "$f" in
    *third_party/*) continue ;;
    *.pb.h|*.pb.cc) continue ;;
  esac
  while IFS= read -r line; do
    echo "$line"
    violations=$((violations + 1))
  done < <(awk -v max="$MAX" -v f="$f" '
    length($0) > max { printf "%s:%d: %d chars: %s\n", f, NR, length($0), substr($0, 1, 120) "…" }
  ' "$f")
done

[[ "$mode" != "explicit" ]] && popd >/dev/null

if (( violations > 0 )); then
  echo
  echo "[lint] FAIL: ${violations} line(s) exceed ${MAX} chars (G.FMT.05-CPP)" >&2
  exit 1
fi
echo "[lint] OK: all C++ lines within ${MAX} chars"
exit 0
