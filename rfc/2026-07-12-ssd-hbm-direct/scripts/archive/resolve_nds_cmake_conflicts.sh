#!/usr/bin/env bash
# Apply known-good CMake/content after cherry-pick conflicts (strip markers / junk).
set -euo pipefail
WD="${DS_WORKTREE:-/home/t14s/workspace/git-repos/yuanrong-datasystem/.worktrees/ssd-hbm-direct}"
cd "$WD"

rm -f .agent-fmt-chmod.log .agent_git_status_snapshot.txt 2>/dev/null || true

DEVICE_CMAKE="$WD/src/datasystem/common/device/CMakeLists.txt"
if grep -q '^<<<<<<<' "$DEVICE_CMAKE" 2>/dev/null || ! grep -q 'add_subdirectory(nds)' "$DEVICE_CMAKE"; then
  cat >"$DEVICE_CMAKE" <<'EOF'
add_subdirectory(ascend)
add_subdirectory(nds)
add_subdirectory(hbm_ipc)
if (BUILD_HETERO_GPU)
    add_subdirectory(nvidia)
endif()

add_library(common_device STATIC
    comm_wrapper.cpp
    comm_wrapper_base.cpp
    device_resource_manager.cpp
    acl_pipeline_p2p_task.cpp
)

target_link_libraries(common_device PRIVATE common_acl_device)
if (BUILD_HETERO_GPU)
    target_link_libraries(common_device PRIVATE common_cuda_device)
endif()
target_link_libraries(common_device PRIVATE common_util common_inject)
EOF
  echo "fixed $DEVICE_CMAKE"
fi

# Fail if any conflict markers remain in tracked cmake/md paths.
if git grep -l '^<<<<<<<' -- '*.cmake' '*.txt' '*.md' '*.bazel' 2>/dev/null; then
  echo "ERROR: unresolved conflict markers remain"
  exit 1
fi

echo "RESOLVE_NDS_CMAKE_OK"
