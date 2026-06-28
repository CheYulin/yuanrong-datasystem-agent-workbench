# Verification Results

**Status**: Draft

| 时间 | 环境 | 命令/用例 | 结果 | 日志 |
|------|------|-----------|------|------|
| 2026-06-29 | tiantiyun-80c128g | `ds_ut --gtest_filter=UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes` RED | Failed as expected: `local_id=0`, jetty id non-zero | `red_urma_fake_local_id.log` |
| 2026-06-29 | tiantiyun-80c128g | `cmake --build ... --target ds_ut -j40` after fake `local_id` fix | PASS | `build_ds_ut_green.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes` | PASS | `green_urma_fake_local_id.log` |
| 2026-06-29 | tiantiyun-80c128g | `cmake --build ... --target ds_ut -j40` after lane implementation | PASS | `build_ds_ut_lane_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*` | PASS, 6 tests | `ut_fake_completion_cqe_lane_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `ds_ut --gtest_filter='UrmaFake*'` | PASS, 61 tests | `ut_urma_fake_all_lane_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaSendLaneTest.*` | PASS, 2 tests | `ut_send_lane_3.log` |

## Pending

| 类别 | 状态 |
|------|------|
| fake URMA 全量 UT | Done |
| lane 分配 UT | Done |
| lane pool backpressure/timeout | Partially done: direct `AcquireSendLane` returns `K_TRY_AGAIN`; manager timeout path pending |
| CQE status 9 lane 恢复 | Partially done: `ReCreateJetty(failedLane)` UT; poll-thread CQE injection pending |
| AE JETTY_ERR lane 恢复 | Pending |
| AE + CQE 幂等 | Partially done: repeated failed-jetty recreate is idempotent; AE+CQE integration pending |
| NUMA affinity targeted ST | Pending |
| worker-worker remote get/write | Pending |
