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
| 2026-06-29 | tiantiyun-80c128g | `make -j40 ds_ut` after timeout-retire fix | PASS | `build_ds_ut_timeout_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaSendLaneTest.*` after timeout-retire fix | PASS, 3 tests | `ut_send_lane_timeout_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*` after timeout-retire fix | PASS, 6 tests | `ut_fake_completion_cqe_timeout_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `ds_ut --gtest_filter='UrmaFake*'` after timeout-retire fix | PASS, 61 tests | `ut_urma_fake_all_timeout_fix_1.log` |
| 2026-06-29 | tiantiyun-80c128g | `make -j40 ds_ut` after retiring-list fix | PASS | `build_ds_ut_retiring_list_fix_2.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaSendLaneTest.*` after retiring-list fix | PASS, 3 tests | `ut_send_lane_retiring_list_fix_2.log` |
| 2026-06-29 | tiantiyun-80c128g | `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*` after retiring-list fix | PASS, 6 tests | `ut_fake_completion_cqe_retiring_list_fix_2.log` |
| 2026-06-29 | tiantiyun-80c128g | `ds_ut --gtest_filter='UrmaFake*'` after retiring-list fix | PASS, 61 tests | `ut_urma_fake_all_retiring_list_fix_2.log` |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `cmake -DWITH_TESTS=ON -DBUILD_WITH_URMA_FAKE=ON ...` with third-party cache and HEAD `e5976074` | PASS | clean configure confirmed fake URMA and reused cached third-party libs |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `make -j40 ds_ut` | PASS | clean build initially failed when fake URMA was OFF; reconfigured with `BUILD_WITH_URMA_FAKE=ON` and rebuilt successfully |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `UrmaSendLaneTest.*:UrmaFakeInjectCqeTest.*:UrmaFakeInjectEventTest.*:UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeBackendTest.DeleteJettyInflightPostSend:UrmaFakeBackendTest.CleanupWaitsForInflight:UrmaFakeR10Test.PostSendWrQueueFullReturnsEAGAIN:UrmaFakeR10Test.PostSendWrQueueFullDrainThenAccept:NumaUtilTest.*` | PASS, 27 tests | Covers lane acquire/release/recreate/retire, CQE injection, AE injection, fake post-send queue full, NUMA utility |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `ds_ut --gtest_filter='UrmaFake*'` | PASS, 61 tests | fake URMA full UT suite |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `make -j40 ds_st ds_st_object_cache datasystem_worker_bin` | PASS | `ds_st_object_cache` needs `datasystem_worker_bin`; otherwise worker exec fails before logs are created |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `ds_st_object_cache --gtest_filter='UrmaNumaAffinityTest.WorkerToWorker'` | PASS, 1 test | NUMA affinity write path; worker/client inject counts reach expected threshold |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `ds_st_object_cache --gtest_filter='UrmaObjectClientTest.UrmaRemoteGetSmall:UrmaObjectClientTest.UrmaPutAndRemoteGetTest:UrmaObjectClientTest.UrmaParallelWrite:UrmaCqeErrorTest.RemoteWorkerGetCqeError:UrmaAsyncEventTest.RemoteWorkerGetJfsAsyncEvent'` | PASS, 5 tests | Ordinary worker-worker remote get/write plus CQE status 9 and AE JETTY_ERR paths |
| 2026-06-29 | tiantiyun-80c128g clean verify worktree | `ds_st_object_cache --gtest_filter='UrmaClientHeartbeatReconnectTest.ClientHeartbeatTimeoutReconnectThenUbSetGetSuccess'` | PASS, 1 test | UB set/get reconnect smoke |

## Review Iteration

| 来源 | Finding | 处理 |
|------|---------|------|
| subagent review | lane 只在 `DeleteEvent` 释放，单请求 WR 数超过 lane 数时可能自我耗尽 | `CheckAndNotify` 在 CQE completion 路径释放 lane；`DeleteEvent` 通过 event 原子标记保留幂等兜底 |
| subagent review | `ReCreateJetty` 对 in-flight lane 提前 unimport 旧 targetJetty | lane 增加 retiring targetJetty，旧 send jetty/targetJetty 一起保留到旧 WR 收口 |
| subagent review | pipeline H2D 绕过 lane acquire | pipeline 发送侧 acquire lane，并创建 serverKey event；pipeline CQE hook 消费 completion 时释放 event/lane |
| subagent review | `urma_send_jetty_lane_pool_size` 语义与初始 lane 计数不完全一致 | 本轮保留为 lazy extra lane 预算，文档继续标为待评估 |
| subagent review | `UrmaGatherWrite` partial post 失败后可能遗留已提交 events | post 失败时等待并清理已提交 gather events，避免错误返回后后台 WR 悬挂 |
| subagent re-review | timeout `DeleteEvent` 可能过早释放 still in-flight lane | `DeleteEvent` 改为只删 map；timeout 通过 `RetireEventLane` 替换新 lane，旧 jetty/target 保留 retiring |
| subagent re-review | lane pool flag 文案像进程总量但实现是 extra lazy lanes | flag help 改为 extra lazy lane pool；总预算语义保留后续评估 |
| subagent final review | 连续 timeout 会覆盖单槽 retiring target | retiring 资源改为列表；新增连续 retire UT，避免更早 in-flight target 被提前析构 |

## Pending

| 类别 | 状态 |
|------|------|
| fake URMA 全量 UT | Done |
| lane 分配 UT | Done |
| lane pool backpressure/timeout | Done for direct acquire pressure and timeout retire replacement; manager timeout path covered by lane retire UT |
| CQE status 9 lane 恢复 | Done: lane recreate UT plus `UrmaCqeErrorTest.RemoteWorkerGetCqeError` ST |
| AE JETTY_ERR lane 恢复 | Done: inject UT plus `UrmaAsyncEventTest.RemoteWorkerGetJfsAsyncEvent` ST |
| AE + CQE 幂等 | Done for repeated failed-jetty recreate idempotence; combined race remains best-effort by shared `MarkInvalid` guard |
| NUMA affinity targeted ST | Done: `UrmaNumaAffinityTest.WorkerToWorker` |
| worker-worker remote get/write | Done: remote get, put+remote get, parallel write ST |
| UB set/get | Done: heartbeat reconnect UB set/get ST |
