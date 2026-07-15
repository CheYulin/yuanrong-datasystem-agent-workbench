#!/usr/bin/env python3
"""Merge NDS Track① UT targets into tests/ut/CMakeLists.txt from main/master base."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

WD = Path(
    sys.argv[1]
    if len(sys.argv) > 1
    else "/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct"
)
BASE = sys.argv[2] if len(sys.argv) > 2 else "0dd746a1"


def git_show(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=WD, text=True)


def main() -> int:
    ut = WD / "tests/ut/CMakeLists.txt"
    ut.write_text(git_show("tests/ut/CMakeLists.txt"))

    md = WD / ".repo_context/modules/infra/common-infra.md"
    md.write_text(git_show(".repo_context/modules/infra/common-infra.md"))

    nds = """
add_library(worker_hbm_mapping STATIC
    ${PROJECT_DIR}/src/datasystem/worker/object_cache/hbm_mapping_table.cpp
)
target_include_directories(worker_hbm_mapping PUBLIC ${PROJECT_SOURCE_DIR}/src)
target_link_libraries(worker_hbm_mapping PUBLIC common_util)

add_executable(ds_ut_nds
        common/device/nds/alignment_gate_test.cpp
        common/device/nds/fake_nds_spill_reader_test.cpp
        common/device/nds/hbm_mapping_table_test.cpp
        common/device/nds/nds_spill_direct_path_test.cpp
        common/device/hbm_ipc/mock_ipc_hbm_backend_test.cpp
        test_main.cpp)
target_link_libraries(ds_ut_nds PRIVATE
        GTest::gtest
        GTest::gmock
        common_device_nds
        common_device_hbm_ipc
        worker_hbm_mapping)
add_datasystem_test(ds_ut_nds TEST_ENVIRONMENTS ${TEST_ENVIRONMENT})

"""
    text = ut.read_text()
    marker = "endif()\n\nset(EMBEDDED"
    if marker not in text:
        raise SystemExit(f"marker missing in {ut}")
    ut.write_text(text.replace(marker, "endif()\n\n" + nds + "set(EMBEDDED"))

    old = (
        "| `device`                | Ascend and optional Nvidia device support wrappers"
        "                               | builds `common_device` over backend-specific device libs"
        "                             |"
    )
    new = (
        "| `device`                | Ascend and optional Nvidia device support wrappers"
        "                               | builds `common_device`; `device/nds` -> `common_device_nds`;"
        " `device/hbm_ipc` -> `common_device_hbm_ipc`; worker `hbm_mapping_table` for Register mapping |"
    )
    md.write_text(md.read_text().replace(old, new))

    device_cmake = WD / "src/datasystem/common/device/CMakeLists.txt"
    if "<<<<<<<" in device_cmake.read_text():
        device_cmake.write_text(
            git_show("src/datasystem/common/device/CMakeLists.txt").replace(
                "add_subdirectory(ascend)\n",
                "add_subdirectory(ascend)\nadd_subdirectory(nds)\nadd_subdirectory(hbm_ipc)\n",
                1,
            )
        )

    print(f"PATCH_NDS_CMAKE_OK lines_ut={len(ut.read_text().splitlines())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
