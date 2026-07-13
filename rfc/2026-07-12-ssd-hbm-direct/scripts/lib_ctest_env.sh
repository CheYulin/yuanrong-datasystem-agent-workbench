#!/usr/bin/env bash
# Source after gtest_filters.sh. Run ST/UT via direct gtest with worker LD_LIBRARY_PATH.
set -euo pipefail

BUILD="${BUILD:-/root/workspace/build-ssd-hbm-direct}"
export BUILD
export WORKER_BIN_DIR="${WORKER_BIN_DIR:-${BUILD}/src/datasystem/worker}"
export ST_BIN_DIR="${ST_BIN_DIR:-${BUILD}/tests/st}"
export UT_BIN_DIR="${UT_BIN_DIR:-${BUILD}/tests/ut}"

export_ld_path_st() {
  export LD_LIBRARY_PATH="${WORKER_BIN_DIR}:${ST_BIN_DIR}:${LD_LIBRARY_PATH:-}"
}

export_ld_path_ut() {
  export LD_LIBRARY_PATH="${WORKER_BIN_DIR}:${UT_BIN_DIR}:${LD_LIBRARY_PATH:-}"
}

run_device_st_gtest() {
  local filter="${1:?gtest filter required}"
  export GTEST_FILTER="${filter}"
  export_ld_path_st
  cd "${ST_BIN_DIR}"
  ./ds_device_llt --gtest_filter="${filter}"
}

run_ut_nds_gtest() {
  local filter="${1:?gtest filter required}"
  export GTEST_FILTER="${filter}"
  export_ld_path_ut
  cd "${UT_BIN_DIR}"
  ./ds_ut_nds --gtest_filter="${filter}"
}

run_ctest_target() {
  local target="${1:?ctest -R name}"
  local filter="${2:-}"
  if [[ -n "${filter}" ]]; then
    export GTEST_FILTER="${filter}"
  else
    unset GTEST_FILTER || true
  fi
  ctest --test-dir "${BUILD}" --output-on-failure -R "^${target}\$" -j 1
}

# Run direct gtest when ctest has no aggregate target name (common on isolated builds).
run_gate0_gtest() {
  local filter="${1:?gtest filter required}"
  if ctest --test-dir "${BUILD}" -N -R '^ds_device_llt$' 2>/dev/null | grep -q 'ds_device_llt'; then
    run_ctest_target ds_device_llt "${filter}"
  else
    run_device_st_gtest "${filter}"
  fi
}

run_ut_nds_or_direct() {
  local filter="${1:?gtest filter required}"
  if ctest --test-dir "${BUILD}" -N -R '^ds_ut_nds$' 2>/dev/null | grep -q 'ds_ut_nds'; then
    run_ctest_target ds_ut_nds "${filter}"
  else
    run_ut_nds_gtest "${filter}"
  fi
}
