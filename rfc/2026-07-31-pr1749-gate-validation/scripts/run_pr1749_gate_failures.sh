#!/usr/bin/env bash
set -uo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: $0 <cmake-build-dir> <source-dir> <evidence-dir>" >&2
  exit 2
fi

build_dir=$1
source_dir=$2
evidence_dir=$3
script_dir=$(cd "$(dirname "$0")" && pwd)
runner="$script_dir/pr1749_run_gtest_bucket.sh"
mkdir -p "$evidence_dir"

export TEST_SRCDIR=$source_dir
export TEST_WORKSPACE=.

stream_filter='StreamClientScaleTest.LEVEL1_TestScaleDownAutoDeleteStream1:StreamClientPassiveScaleTest.LEVEL1_TestClearAsyncNotifyTask:StreamClientVoluntaryScaleDownTest.LEVEL1_TestScaleDownAutoDeleteStream2:StreamDfxHeartbeatTest.LEVEL1_TestWorkerCrashTimeout'
kv_filter='KVClientWorkerTimeoutStorage.LEVEL1_WorkerTimeoutAndMetaGetFromEtcd'

date '+%F %T %z' > "$evidence_dir/repro.start"
"$runner" "$build_dir/tests/st/ds_st_stream_cache" gate-stream "$stream_filter" 4 1800 "$evidence_dir" &
stream_pid=$!
"$runner" "$build_dir/tests/st/ds_st_kv_cache" gate-kv "$kv_filter" 1 900 "$evidence_dir" &
kv_pid=$!

parallel_rc=0
wait "$stream_pid" || parallel_rc=1
wait "$kv_pid" || parallel_rc=1
printf '%d\n' "$parallel_rc" > "$evidence_dir/parallel.exit"
date '+%F %T %z' > "$evidence_dir/repro.parallel-end"

python3 "$script_dir/rerun_failed_cases.py" "$evidence_dir" "$runner"
serial_rc=$?
printf '%d\n' "$serial_rc" > "$evidence_dir/serial.exit"
date '+%F %T %z' > "$evidence_dir/repro.end"
exit "$serial_rc"
