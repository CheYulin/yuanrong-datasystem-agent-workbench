# Scripts

默认节点：`xqyun-32c32g`（隔离 worktree + build）。

## 日常

| 脚本 | 用途 |
|------|------|
| [verify_track1_xqyun.sh](./verify_track1_xqyun.sh) | sync → build → Gate0(5) + UT(14) |
| [run_existing_hetero_st_xqyun.sh](./run_existing_hetero_st_xqyun.sh) | 仅 Gate0 |
| [run_nds_ut_remote.sh](./run_nds_ut_remote.sh) | sync → build → UT |
| [run_ut_only_xqyun.sh](./run_ut_only_xqyun.sh) | 仅 UT |
| [gtest_filters.sh](./gtest_filters.sh) | 共享 `GTEST_FILTER` |
| [lib_ctest_env.sh](./lib_ctest_env.sh) | `LD_LIBRARY_PATH` / direct gtest |
| [BUILD_VERIFY.md](./BUILD_VERIFY.md) | 路径与验证约定 |

## 发布（GitCode）

| 脚本 | 用途 |
|------|------|
| [publish_gitcode_track1.sh](./publish_gitcode_track1.sh) | push fork → 可选 1 issue → `ds-create-pr`（`--head yche-huawei:feat/...`） |
| [create_tracking_issues.py](./create_tracking_issues.py) | 只建 **1** 个 issue（默认 fork `yche-huawei`） |

## L2 / 后续

| 脚本 | 用途 |
|------|------|
| [run_stage_a_npu.sh](./run_stage_a_npu.sh) / [run_stage_b_nds.sh](./run_stage_b_nds.sh) | 真机 Stage A/B |
| [env.local.sh.example](./env.local.sh.example) · [HUMAN_CHECKLIST.md](./HUMAN_CHECKLIST.md) | L2 清单 |
| [run_binmock_flow_st.sh](./run_binmock_flow_st.sh) · [run_obs_smoke.sh](./run_obs_smoke.sh) | Task 6+ |
| [check_env_device.sh](./check_env_device.sh) | 设备环境检查 |

## 归档

夜间迭代、cmake puncture、rebase/冲突临时脚本、tiantiyun fallback 等见 [archive/](./archive/README.md)。
