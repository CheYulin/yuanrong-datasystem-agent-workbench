#!/usr/bin/env bash
set -uo pipefail

if [[ $# -lt 6 ]]; then
  echo "usage: $0 <binary> <bucket> <filter> <expected-count> <timeout-seconds> <evidence-dir>" >&2
  exit 2
fi

binary=$1
bucket=$2
filter=$3
expected_count=$4
timeout_seconds=$5
evidence_dir=$6
bucket_dir="$evidence_dir/$bucket"

mkdir -p "$bucket_dir"
printf '%s\n' "$binary" > "$bucket_dir/binary"
printf '%s\n' "$filter" > "$bucket_dir/filter"
printf '%s\n' "$expected_count" > "$bucket_dir/expected-count"
printf '%s\n' "$timeout_seconds" > "$bucket_dir/timeout-seconds"
date '+%F %T %z' > "$bucket_dir/start"

"$binary" --gtest_list_tests --gtest_filter="$filter" > "$bucket_dir/list.log" 2>&1
list_rc=$?
printf '%d\n' "$list_rc" > "$bucket_dir/list.exit"
count=$(awk '/^  [^ ]/{n++} END{print n+0}' "$bucket_dir/list.log")
printf '%d\n' "$count" > "$bucket_dir/test-count"
if [[ $list_rc -ne 0 || $count -ne $expected_count ]]; then
  printf 'filter validation failed: list_rc=%d expected=%d actual=%d\n' \
    "$list_rc" "$expected_count" "$count" > "$bucket_dir/result"
  printf '3\n' > "$bucket_dir/run.exit"
  date '+%F %T %z' > "$bucket_dir/end"
  exit 3
fi

start_ns=$(date +%s%N)
timeout --signal=TERM --kill-after=30 "$timeout_seconds" \
  "$binary" --gtest_filter="$filter" --gtest_output="xml:$bucket_dir/results.xml" \
  > "$bucket_dir/run.log" 2>&1
run_rc=$?
end_ns=$(date +%s%N)
printf '%d\n' "$run_rc" > "$bucket_dir/run.exit"
printf '%d\n' "$(((end_ns - start_ns) / 1000000))" > "$bucket_dir/duration-ms"
date '+%F %T %z' > "$bucket_dir/end"

if [[ $run_rc -eq 0 ]]; then
  printf 'PASS tests=%d\n' "$count" > "$bucket_dir/result"
else
  printf 'FAIL tests=%d exit=%d\n' "$count" "$run_rc" > "$bucket_dir/result"
fi
exit "$run_rc"

