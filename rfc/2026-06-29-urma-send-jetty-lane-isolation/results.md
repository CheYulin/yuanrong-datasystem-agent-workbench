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
| 2026-06-29 | tiantiyun-80c128g | `make -j40 ds_ut` after subagent review fixes | PASS | `build_ds_ut_review_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaSendLaneTest.*` after completion-release/retiring-target fixes | PASS, 2 tests | `ut_send_lane_review_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*` after review fixes | PASS, 6 tests | `ut_fake_completion_cqe_review_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `ds_ut --gtest_filter='UrmaFake*'` after review fixes | PASS, 61 tests | `ut_urma_fake_all_review_fix_1.log` |

## Review Iteration

| 来源 | Finding | 处理 |
|------|---------|------|
| subagent review | lane 只在 `DeleteEvent` 释放，单请求 WR 数超过 lane 数时可能自我耗尽 | `CheckAndNotify` 在 CQE completion 路径释放 lane；`DeleteEvent` 通过 event 原子标记保留幂等兜底 |
| subagent review | `ReCreateJetty` 对 in-flight lane 提前 unimport 旧 targetJetty | lane 增加 retiring targetJetty，旧 send jetty/targetJetty 一起保留到旧 WR 收口 |
| subagent review | pipeline H2D 绕过 lane acquire | pipeline 发送侧 acquire lane，并创建 serverKey event；pipeline CQE hook 消费 completion 时释放 event/lane |
| subagent review | `urma_send_jetty_lane_pool_size` 语义与初始 lane 计数不完全一致 | 本轮保留为 lazy extra lane 预算，文档继续标为待评估 |
| subagent review | `UrmaGatherWrite` partial post 失败后可能遗留已提交 events | post 失败时等待并清理已提交 gather events，避免错误返回后后台 WR 悬挂 |

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
