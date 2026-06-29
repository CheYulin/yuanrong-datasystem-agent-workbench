#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/testing/verify/validate_urma_tcp_observability_logs.sh <log-path> [<log-path>...]

Description:
  Validate URMA/TCP observability log patterns for Phase 3/4 acceptance.
  Each <log-path> can be a file or directory. Directories are scanned recursively.

Checks:
  1) URMA reconnect logs:
     - must contain "URMA_NEED_CONNECT"
     - must include "remoteAddress="
  2) URMA poll error logs:
     - must contain "URMA_POLL_ERROR"
  3) URMA JFS recreate logs:
     - at least one of:
       "URMA_RECREATE_JFS", "URMA_RECREATE_JFS_FAILED", "URMA_RECREATE_JFS_SKIP"
  4) 1002 subclassification logs:
     - at least 3 distinct prefixes from:
       [RPC_RECV_TIMEOUT], [RPC_SERVICE_UNAVAILABLE], [TCP_CONNECT_RESET],
       [TCP_CONNECT_FAILED], [UDS_CONNECT_FAILED], [SOCK_CONN_WAIT_TIMEOUT],
       [REMOTE_SERVICE_WAIT_TIMEOUT], [SHM_FD_TRANSFER_FAILED], [TCP_NETWORK_UNREACHABLE]
EOF
}

if [[ $# -lt 1 ]]; then
  usage >&2
  exit 2
fi

declare -a SEARCH_PATHS=("$@")
for p in "${SEARCH_PATHS[@]}"; do
  if [[ ! -e "${p}" ]]; then
    echo "[error] path does not exist: ${p}" >&2
    exit 2
  fi
done

count_matches() {
  local pattern="$1"
  grep -R -h -E --binary-files=without-match "${pattern}" "${SEARCH_PATHS[@]}" 2>/dev/null | wc -l | tr -d ' '
}

echo "== URMA/TCP observability log validation =="
echo "Paths:"
for p in "${SEARCH_PATHS[@]}"; do
  echo "  - ${p}"
done
echo

need_connect_count="$(count_matches 'URMA_NEED_CONNECT')"
remote_address_count="$(count_matches 'remoteAddress=')"
poll_error_count="$(count_matches 'URMA_POLL_ERROR')"
recreate_count="$(count_matches 'URMA_RECREATE_JFS|URMA_RECREATE_JFS_FAILED|URMA_RECREATE_JFS_SKIP')"

echo "[check] URMA_NEED_CONNECT count: ${need_connect_count}"
echo "[check] remoteAddress= count:    ${remote_address_count}"
echo "[check] URMA_POLL_ERROR count:   ${poll_error_count}"
echo "[check] URMA_RECREATE_JFS* count:${recreate_count}"

declare -a PREFIXES=(
  "\\[RPC_RECV_TIMEOUT\\]"
  "\\[RPC_SERVICE_UNAVAILABLE\\]"
  "\\[TCP_CONNECT_RESET\\]"
  "\\[TCP_CONNECT_FAILED\\]"
  "\\[UDS_CONNECT_FAILED\\]"
  "\\[SOCK_CONN_WAIT_TIMEOUT\\]"
  "\\[REMOTE_SERVICE_WAIT_TIMEOUT\\]"
  "\\[SHM_FD_TRANSFER_FAILED\\]"
  "\\[TCP_NETWORK_UNREACHABLE\\]"
)

echo
echo "[check] 1002 subclassification prefix counts:"
prefix_hit_types=0
for prefix in "${PREFIXES[@]}"; do
  c="$(count_matches "${prefix}")"
  label="$(echo "${prefix}" | sed 's/\\\\//g')"
  echo "  - ${label}: ${c}"
  if [[ "${c}" -gt 0 ]]; then
    prefix_hit_types=$((prefix_hit_types + 1))
  fi
done
echo "  -> distinct prefix types: ${prefix_hit_types}"

echo
failed=0
if [[ "${need_connect_count}" -lt 1 ]]; then
  echo "[fail] Missing URMA_NEED_CONNECT logs"
  failed=1
fi
if [[ "${remote_address_count}" -lt 1 ]]; then
  echo "[fail] Missing remoteAddress field in logs"
  failed=1
fi
if [[ "${poll_error_count}" -lt 1 ]]; then
  echo "[fail] Missing URMA_POLL_ERROR logs"
  failed=1
fi
if [[ "${recreate_count}" -lt 1 ]]; then
  echo "[fail] Missing URMA_RECREATE_JFS* logs"
  failed=1
fi
if [[ "${prefix_hit_types}" -lt 3 ]]; then
  echo "[fail] Need at least 3 distinct 1002 subclassification prefixes"
  failed=1
fi

if [[ "${failed}" -ne 0 ]]; then
  echo
  echo "Validation failed."
  exit 1
fi

echo
echo "Validation passed."
