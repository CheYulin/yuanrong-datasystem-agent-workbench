#!/usr/bin/env bash
MOONCAKE_ROOT=${MOONCAKE_ROOT:-/home/worktrees/kvc-fair-20261020/mooncake}
export MOONCAKE_ROOT
export LD_LIBRARY_PATH="$MOONCAKE_ROOT/deps/lib:${LD_LIBRARY_PATH:-}"
export GLOG_minloglevel=2
export MC_LOG_LEVEL=ERROR
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MC_STORE_LOCAL_HOT_CACHE_SIZE=0
export MOONCAKE_STORE_CHECKSUM=false
