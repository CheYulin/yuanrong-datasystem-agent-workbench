#!/usr/bin/env bash
# Bootstrap etcd + datasystem Worker for dsbench/kvtest smoke on a remote or local host.
set -euo pipefail

BENCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="$(cd "${BENCH_DIR}/../../lib" && pwd)"
SCRIPT_DIR="${LIB_DIR}"
. "${LIB_DIR}/load_nodes.sh"
. "${LIB_DIR}/common.sh"
. "${LIB_DIR}/remote_defaults.sh"
. "${LIB_DIR}/timing.sh"

NODE="$(node_role_default build)"
LOCAL=0
WORKERS="${WORKERS:-127.0.0.1:31501}"
ETCD_ADDR="${ETCD_ADDR:-127.0.0.1:2379}"
WORKER_SHM_MB="${WORKER_SHM_MB:-4096}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) NODE="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --local) LOCAL=1; shift ;;
    -h|--help)
      echo "Usage: $0 [--node NAME] [--workers ADDRS] [--local]"
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 2 ;;
  esac
done

_bootstrap_impl() {
  local etcd_addr="$1"
  local workers="$2"
  local shm_mb="$3"
  local etcd_port="${etcd_addr##*:}"

  export PATH="${HOME}/.local/bin:${PATH}"
  export JD_HOST_IP="${JD_HOST_IP:-127.0.0.1}"

  if ! ss -ltn 2>/dev/null | grep -q ":${etcd_port} "; then
    command -v etcd >/dev/null 2>&1 || {
      echo "[bootstrap] ERROR: etcd not on ${etcd_addr} and binary missing" >&2
      return 1
    }
    local etcd_data="/tmp/etcd-bench-${etcd_port}"
    rm -rf "${etcd_data}"
    mkdir -p "${etcd_data}"
    echo "[bootstrap] Starting etcd on ${etcd_addr}..."
    nohup etcd \
      --name "bench-etcd-${etcd_port}" \
      --data-dir "${etcd_data}" \
      --listen-client-urls "http://${etcd_addr}" \
      --advertise-client-urls "http://${etcd_addr}" \
      --listen-peer-urls "http://127.0.0.1:$((etcd_port + 1))" \
      --initial-advertise-peer-urls "http://127.0.0.1:$((etcd_port + 1))" \
      --initial-cluster "bench-etcd-${etcd_port}=http://127.0.0.1:$((etcd_port + 1))" \
      --initial-cluster-state new \
      --initial-cluster-token bench-token \
      >"/tmp/etcd-bench-${etcd_port}.log" 2>&1 &
    for _ in $(seq 1 30); do
      curl -sf "http://${etcd_addr}/health" >/dev/null 2>&1 && break
      sleep 1
    done
    curl -sf "http://${etcd_addr}/health" >/dev/null 2>&1 || {
      echo "[bootstrap] ERROR: etcd failed to start on ${etcd_addr}" >&2
      tail -20 "/tmp/etcd-bench-${etcd_port}.log" >&2 || true
      return 1
    }
    echo "[bootstrap] etcd ready on ${etcd_addr}"
  else
    echo "[bootstrap] etcd port ${etcd_port} already listening"
  fi

  command -v dscli >/dev/null 2>&1 || {
    echo "[bootstrap] ERROR: dscli not in PATH" >&2
    return 1
  }

  IFS=',' read -ra WORKER_LIST <<< "${workers}"
  for waddr in "${WORKER_LIST[@]}"; do
    waddr="${waddr// /}"
    local host="${waddr%%:*}"
    local port="${waddr##*:}"
    [[ "${host}" == "127.0.0.1" || "${host}" == "localhost" ]] || {
      echo "[bootstrap] Skip remote worker ${waddr}"
      continue
    }
    if ss -ltn 2>/dev/null | grep -q ":${port} "; then
      echo "[bootstrap] Worker port ${port} already listening"
      continue
    fi
    echo "[bootstrap] Starting worker ${waddr} (shm=${shm_mb}MB)..."
    dscli start -w \
      --worker_address "${waddr}" \
      --etcd_address "${etcd_addr}" \
      --shared_memory_size_mb "${shm_mb}" \
      >"/tmp/dscli-bench-${port}.log" 2>&1
    for _ in $(seq 1 90); do
      ss -ltn 2>/dev/null | grep -q ":${port} " && break
      sleep 1
    done
    ss -ltn 2>/dev/null | grep -q ":${port} " || {
      echo "[bootstrap] ERROR: worker ${waddr} timeout" >&2
      tail -30 "/tmp/dscli-bench-${port}.log" >&2 || true
      return 1
    }
    echo "[bootstrap] Worker ${waddr} ready"
  done
}

if [[ "${LOCAL}" -eq 1 ]]; then
  _bootstrap_impl "${ETCD_ADDR}" "${WORKERS}" "${WORKER_SHM_MB}"
  exit $?
fi

init_remote "${NODE}"
banner "Bootstrap bench cluster on ${REMOTE}"

ssh_remote "${REMOTE}" bash -s -- "${ETCD_ADDR}" "${WORKERS}" "${WORKER_SHM_MB}" <<'REMOTE'
set -euo pipefail
ETCD_ADDR="$1"
WORKERS="$2"
WORKER_SHM_MB="$3"
etcd_port="${ETCD_ADDR##*:}"

export PATH="${HOME}/.local/bin:${PATH}"
export JD_HOST_IP="${JD_HOST_IP:-127.0.0.1}"

if ! ss -ltn 2>/dev/null | grep -q ":${etcd_port} "; then
  command -v etcd >/dev/null 2>&1 || { echo "[bootstrap] ERROR: etcd missing" >&2; exit 1; }
  etcd_data="/tmp/etcd-bench-${etcd_port}"
  rm -rf "${etcd_data}"
  mkdir -p "${etcd_data}"
  echo "[bootstrap] Starting etcd on ${ETCD_ADDR}..."
  nohup etcd \
    --name "bench-etcd-${etcd_port}" \
    --data-dir "${etcd_data}" \
    --listen-client-urls "http://${ETCD_ADDR}" \
    --advertise-client-urls "http://${ETCD_ADDR}" \
    --listen-peer-urls "http://127.0.0.1:$((etcd_port + 1))" \
    --initial-advertise-peer-urls "http://127.0.0.1:$((etcd_port + 1))" \
    --initial-cluster "bench-etcd-${etcd_port}=http://127.0.0.1:$((etcd_port + 1))" \
    --initial-cluster-state new \
    --initial-cluster-token bench-token \
    >"/tmp/etcd-bench-${etcd_port}.log" 2>&1 &
  for _ in $(seq 1 30); do
    curl -sf "http://${ETCD_ADDR}/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf "http://${ETCD_ADDR}/health" >/dev/null 2>&1 || {
    echo "[bootstrap] ERROR: etcd failed" >&2
    tail -20 "/tmp/etcd-bench-${etcd_port}.log" >&2
    exit 1
  }
  echo "[bootstrap] etcd ready"
else
  echo "[bootstrap] etcd port ${etcd_port} already listening"
fi

command -v dscli >/dev/null 2>&1 || { echo "[bootstrap] ERROR: dscli not in PATH" >&2; exit 1; }

IFS=',' read -ra WORKER_LIST <<< "${WORKERS}"
for waddr in "${WORKER_LIST[@]}"; do
  waddr="${waddr// /}"
  port="${waddr##*:}"
  host="${waddr%%:*}"
  [[ "${host}" == "127.0.0.1" || "${host}" == "localhost" ]] || continue
  if ss -ltn 2>/dev/null | grep -q ":${port} "; then
    echo "[bootstrap] Worker port ${port} already listening"
    continue
  fi
  echo "[bootstrap] Starting worker ${waddr}..."
  dscli start -w \
    --worker_address "${waddr}" \
    --etcd_address "${ETCD_ADDR}" \
    --shared_memory_size_mb "${WORKER_SHM_MB}" \
    >"/tmp/dscli-bench-${port}.log" 2>&1
  for _ in $(seq 1 90); do
    ss -ltn 2>/dev/null | grep -q ":${port} " && break
    sleep 1
  done
  ss -ltn 2>/dev/null | grep -q ":${port} " || {
    echo "[bootstrap] ERROR: worker ${waddr} timeout" >&2
    tail -30 "/tmp/dscli-bench-${port}.log" >&2
    exit 1
  }
  echo "[bootstrap] Worker ${waddr} ready"
done
REMOTE

log_info "Bootstrap done (${NODE})."
