**这是什么类型的PR？**

/kind feat
/kind test

----

**这个PR是做什么的/我们为什么需要它**

本 PR 为 URMA 发送侧引入 send jetty lane isolation，用于异步写入 meta/data colocate 后 client/worker 直接写远端节点的场景。

原 `UrmaConnection` 是 `1 send jetty + 1 recv jetty`。错误处理简单，但多个 WR 共用同一个 send queue 时，一个 CTP/jetty 故障会影响同队列里的其它 request。本 PR 保持 connection 边界和目的端 recv jetty 不变，只把发送侧最小化 lane 化：每个 WR acquire 一个 send lane，send queue depth=1，completion/error/timeout 后 release 或 retire lane。

主要变化：
- `UrmaConnection` 内部维护 send lanes：`send jetty + targetJetty + state + in-flight event`。
- `UrmaWritePayload` / `UrmaRead` / pipeline H2D / `UrmaGatherWrite` 发送前 acquire lane，完成后释放 lane。
- lane 不够时 lazy 创建，受 `urma_send_jetty_lane_pool_size` 控制，默认 200；当前语义是 extra lazy lane pool。
- AE/CQE 仍复用现有恢复入口，`ReCreateJetty` 改为按 failed jetty 定位 lane，只重建该 lane 的 send jetty 和 targetJetty。
- timeout 不直接复用可能 still in-flight 的旧 lane，而是 retire 旧 lane 并补新 lane；旧 send jetty/targetJetty 进入 retiring list，等待旧 WR 收口。
- fake URMA completion `local_id` 贯通 local send jetty id，支持 CQE error 按 failed jetty 定位 lane。
- `UrmaGatherWrite` partial post 失败后等待并清理已提交 events，避免错误返回后后台 WR 悬挂。
- 修复 client remote get all-failed 判断：URMA payload 成功路径可能只体现在 `payload_info`，需要同时检查 `objects` 和 `payload_info`，避免 mixed batch 被误判全失败并掩盖业务错误码。

----

**此PR修复了哪些问题**:

Fixes #666

----

**PR对程序接口进行了哪些修改？**

不涉及对外 SDK/API 变更。

新增/调整内部运行参数：
- `urma_send_jetty_lane_pool_size`：lazy extra send jetty lane pool 上限，默认 200。

----

**特性影响与兼容性**

- 正常路径：单 WR 独占一个 send lane，完成后归还；调用方接口和目的端 recv jetty 模型不变。
- NUMA affinity：保留原有 `srcChipId/dstChipId` 传递，lane 选择不改变 affinity 决策。
- 资源使用：每个额外 lane 会持有一组 send jetty/targetJetty；并发压力从单 jetty queue 排队转为 lane acquire/backpressure。
- 故障隔离：单个 send jetty/CTP 故障只 retire/recreate 对应 lane，不直接牵连其它 lane 上的 request。

----

**故障与极端case是否覆盖**

- CQE error：completion `local_id` 对应 local send jetty id；CQE error 按 failed jetty 定位 lane，当前 request 返回失败，该 lane 标记 invalid 并幂等重建。
- AE `URMA_EVENT_JETTY_ERR`：AE 按 jetty id 定位 failed lane；若 lane 有 in-flight WR，不提前 unimport old targetJetty，通过 retiring list 等 CQE/timeout 收口。
- AE + CQE 双触发：共享 `MarkInvalid` guard，重复故障通知不会重复重建同一 lane。
- timeout：timeout 只删除 event map，不直接释放/复用 still in-flight lane；旧 lane retire 后补新 lane。
- 连续 timeout：retiring 资源使用 list 保存多个旧 send jetty/targetJetty，避免覆盖更早未收口资源。
- lane pool/backpressure：fake URMA 覆盖 post-send queue full (`EAGAIN`)；send lane UT 覆盖 acquire/reuse、failed lane recreate、timeout retire replacement。
- partial post：`UrmaGatherWrite` 已提交部分 WR 后失败时会等待并清理已提交 events。
- mixed URMA payload：覆盖 injected OOM + partial successful `payload_info` 的 batch get，避免错误重试到 RPC deadline。

----

**正确性验证点**

- lane 独占不变量：每个 WR acquire 一个 lane，lane 在 in-flight 状态不会被其它 WR 复用；`UrmaSendLaneTest.*` 覆盖 acquire/reuse、lane recreate 和 timeout retire replacement。
- completion 释放不变量：正常 CQE completion 后 event 记录的 lane 被释放，后续 WR 可以复用；fake completion CQE UT 覆盖 `local_id` 贯通和 completion path。
- 故障隔离不变量：CQE/AE 指向 failed jetty 时只 invalidate/recreate 对应 lane，其它 lane 不被整体 connection 重建牵连；`UrmaCqeErrorTest.RemoteWorkerGetCqeError` 和 `UrmaAsyncEventTest.RemoteWorkerGetJfsAsyncEvent` 覆盖 ST 路径。
- timeout 安全不变量：timeout 不直接释放 still in-flight lane，而是 retire 旧 lane 并补新 lane；连续 timeout 使用 retiring list 保留多个旧 send jetty/targetJetty，防止更早未收口资源被覆盖。
- queue depth=1 不变量：fake URMA 覆盖 post-send queue full (`EAGAIN`) 和 drain 后重新接受，验证不会在同一 send jetty 上堆多个未完成 WR。
- NUMA 正确性：lane 选择不改变 `srcChipId/dstChipId` 传递；`UrmaNumaAffinityTest.WorkerToWorker` 覆盖普通路径 affinity 发送。
- remote get 结果正确性：`IsAllGetFailed` 同时识别 `objects` 和 `payload_info` 成功路径；`UrmaObjectClientTest.TestBatchRemoteGetErrorCode2` 覆盖 injected OOM + partial successful URMA payload，不再把 mixed response 误判为全失败。
- full-filter 回归：object/kv URMA ST full filter clean PASS，覆盖普通路径、故障路径、reconnect/fallback/eviction 等组合场景。

----

**Self-checklist**:

- [x] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
- [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
- [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
- [x] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
- [ ] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓

----

**验证结果**

远端环境：`tiantiyun-80c128g`，复用已有三方库缓存，构建并行度 `-j40`。

Build:
- `cmake -DWITH_TESTS=ON -DBUILD_WITH_URMA_FAKE=ON ...`
  - PASS，确认 fake URMA 开启并复用 cached third-party libs。
- `make -j40 ds_ut`
  - PASS。
- `make -j40 ds_st_object_cache ds_ut`
  - PASS。
- `make -j40 ds_st_kv_cache`
  - PASS。

UT:
- `tests/ut/ds_ut --gtest_filter='*Urma*'`
  - PASS，76 tests。
- `tests/ut/ds_ut --gtest_filter='UrmaFake*'`
  - PASS，61 tests。
- `UrmaSendLaneTest.*`
  - PASS，3 tests。
- `UrmaFakeBackendTest.PostSendTransfersBytesAndCompletes:UrmaFakeInjectCqeTest.*:UrmaFakeInjectEventTest.*:UrmaFakeBackendTest.DeleteJettyInflightPostSend:UrmaFakeBackendTest.CleanupWaitsForInflight:UrmaFakeR10Test.PostSendWrQueueFullReturnsEAGAIN:UrmaFakeR10Test.PostSendWrQueueFullDrainThenAccept:NumaUtilTest.*`
  - PASS，27 tests。

ST:
- `tests/st/ds_st_object_cache --gtest_filter='*Urma*:*URMA*:*urma*' --gtest_also_run_disabled_tests`
  - PASS，68 tests。
  - XML: `/tmp/urma_send_lane_object_full_clean.xml`。
- `tests/st/ds_st_kv_cache --gtest_filter='*Urma*:*URMA*:*urma*' --gtest_also_run_disabled_tests`
  - PASS，11 tests。
  - XML: `/tmp/urma_send_lane_kv_full_clean.xml`。
- `tests/st/ds_st_object_cache --gtest_filter='UrmaNumaAffinityTest.WorkerToWorker'`
  - PASS，覆盖普通路径 NUMA affinity 发送。
- `tests/st/ds_st_object_cache --gtest_filter='UrmaObjectClientTest.UrmaRemoteGetSmall:UrmaObjectClientTest.UrmaPutAndRemoteGetTest:UrmaObjectClientTest.UrmaParallelWrite:UrmaCqeErrorTest.RemoteWorkerGetCqeError:UrmaAsyncEventTest.RemoteWorkerGetJfsAsyncEvent'`
  - PASS，覆盖 worker-worker remote get/write、CQE status 9、AE JETTY_ERR。
- `tests/st/ds_st_object_cache --gtest_filter='UrmaObjectClientTest.TestBatchRemoteGetErrorCode2'`
  - PASS，覆盖 injected OOM + partial successful URMA payload mixed batch。
- `tests/st/ds_st_object_cache --gtest_filter='UrmaClientHeartbeatReconnectTest.ClientHeartbeatTimeoutReconnectThenUbSetGetSuccess'`
  - PASS，覆盖 UB set/get reconnect smoke。

说明：曾误跑 broad scale-down/scale-up ST sweep，其中包含非 URMA case，首个失败为 `OCScaleDownTest.TestRefsScaleDownWithoutL2`，并留下 worker/etcd residual processes；该结果不作为本 PR URMA gate。清理进程组后，上述 object/kv URMA full filter 均 clean PASS。

----

**风险与后续**

- `urma_send_jetty_lane_pool_size` 当前表示 extra lazy lane pool；是否改成严格的进程级全量 send jetty 总预算，可在后续按部署资源模型再收口。
- 本 PR 优先保证最小化改造和故障隔离，不改变目的端 recv jetty 模型。
- AE + CQE 组合 race 通过 shared invalid guard 做幂等保护；极端真实硬件 race 仍建议在真实 URMA 环境继续补充长稳/压测。
