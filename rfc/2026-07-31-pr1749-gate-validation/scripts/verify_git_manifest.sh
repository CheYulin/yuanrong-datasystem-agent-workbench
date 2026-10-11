#!/usr/bin/env bash
set -euo pipefail

manifest=$1
failures=0
checked=0
root=$PWD
exec 3<"$manifest"
cd /

while IFS=$'\t' read -r metadata path; do
  mode=${metadata%% *}
  rest=${metadata#* }
  expected=${rest%% *}
  if [[ $mode == 120000 ]]; then
    actual=$(printf %s "$(readlink "$root/$path")" | git hash-object --no-filters --stdin)
  else
    actual=$(git hash-object --no-filters "$root/$path" 2>/dev/null || true)
  fi
  checked=$((checked + 1))
  if [[ $actual != "$expected" ]]; then
    printf 'MISMATCH\t%s\texpected=%s\tactual=%s\n' "$path" "$expected" "$actual"
    failures=$((failures + 1))
  fi
done <&3

printf 'checked=%d failures=%d\n' "$checked" "$failures"
test "$failures" -eq 0

