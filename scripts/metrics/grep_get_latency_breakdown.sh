#!/usr/bin/env bash
# Summarize Get-related KV metrics from glog / text; **emit RFC performance breakdown ASCII tree**
# for integration/smoke verification (see rfc/2026-04-worker-get-metrics-breakdown/issue-rfc.md).
#
# Usage:
#   grep_get_latency_breakdown.sh [DIR|FILE...]
#   grep_get_latency_breakdown.sh --tree-only   # print tree only (no log scan)
#
set -euo pipefail

PATTERNS=(
  client_rpc_get_latency
  worker_process_get_latency
  worker_get_threadpool_queue_latency
  worker_get_threadpool_exec_latency
  worker_rpc_query_meta_latency
  worker_rpc_remote_get_outbound_latency
  worker_rpc_remote_get_inbound_latency
  worker_get_meta_addr_hashring_latency
  worker_get_post_query_meta_phase_latency
  worker_urma_write_latency
  worker_urma_wait_latency
)

print_breakdown_tree() {
  cat <<'TREE'
================================================================================
Generated: Get performance breakdown tree (RFC 2026-04-worker-get-metrics-breakdown)
================================================================================
Wall-clock: Client / Entry / Peer are often parallel; tree = metric names + roles
(not sum to client). ZMQ segments: see rfc/2026-04-zmq-rpc-metrics/issue-rfc.md

[Client process]
  client_rpc_get_latency ..................... client read E2E (Histogram us)
         |
         |  (link / ZMQ: zmq_* / zmq_rpc_e2e_latency, ...)
         v
[Entry worker process]
  worker_process_get_latency .................  handle E2E (MsgQ ~ queue+exec)
      |
      |-- worker_get_threadpool_queue_latency ...  MsgQ only: before Execute -> callback start
      '-- worker_get_threadpool_exec_latency ....  ProcessGetObjectRequest
              |
              |-- worker_get_meta_addr_hashring_latency
              |-- worker_rpc_query_meta_latency
              |-- worker_get_post_query_meta_phase_latency
              '-- worker_rpc_remote_get_outbound_latency
                        |
        [ cross-worker RPC / data path ]   |
                        |                  |
                        v                  v
                 [Peer worker process] <---'
                      |
                      |-- worker_rpc_remote_get_inbound_latency
                      |-- worker_urma_write_latency ........  URMA 写 (含 W<->W 对端、与其它路径同桶)
                      |-- worker_urma_wait_latency ........  URMA 等完成 (同上)
                      '-- worker_rpc_query_meta_latency .....  process-level (may not bind one pull)
================================================================================
TREE
}

if [[ "${1:-}" == "--tree-only" ]]; then
  print_breakdown_tree
  exit 0
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <log_dir_or_file>..." >&2
  echo "       $0 --tree-only" >&2
  exit 1
fi

GREP=grep
if command -v rg >/dev/null 2>&1; then
  GREP=rg
fi

# Glog can use several naming schemes; include all common ones (see issue-rfc / sample logs).
# - datasystem: *.INFO.log, datasystem_worker.INFO.log, client_*ds_client_*.INFO.log
# - Google glog: *log.INFO.<date>.<time>.<pid>  (not matched by *.log nor *.INFO.log alone)
GLOG_GLOB() {
  find "$1" -type f \( \
    -name "*.log" -o -name "*.INFO" -o -name "*.txt" -o -name "worker*.log" -o -name "*.INFO.log" -o \
    -name "*log.INFO*" -o -name "*.log.INFO.*" \
  \) -print0 2>/dev/null
}

all_files=()
for p in "$@"; do
  if [[ -d "$p" ]]; then
    while IFS= read -r -d '' f; do
      all_files+=("$f")
    done < <(GLOG_GLOB "$p")
  else
    all_files+=("$p")
  fi
done

if [[ ${#all_files[@]} -eq 0 ]]; then
  echo "grep_get_latency_breakdown: no log files matched under given paths (glog: *.INFO.log, *log.INFO*, ...). Check cwd or use absolute paths. Inputs: $*" >&2
  print_breakdown_tree
  exit 0
fi
echo "grep_get_latency_breakdown: scanning ${#all_files[@]} file(s)" >&2

for name in "${PATTERNS[@]}"; do
  if [[ $GREP == rg ]]; then
    if rg -l --no-messages -F "$name" "${all_files[@]}" >/dev/null 2>&1; then
      echo "=== $name (sample lines) ==="
      rg -n --no-heading -F "$name" "${all_files[@]}" 2>/dev/null | head -n 8
      echo
    fi
  else
    if "${GREP}" -l -F -q "$name" "${all_files[@]}" 2>/dev/null; then
      echo "=== $name (sample lines) ==="
      "${GREP}" -n -F "$name" "${all_files[@]}" 2>/dev/null | head -n 8
      echo
    fi
  fi
done

echo "Per-metric grep done. See JSON lines with metrics_summary or name/count/avg in samples above."
echo
print_breakdown_tree
