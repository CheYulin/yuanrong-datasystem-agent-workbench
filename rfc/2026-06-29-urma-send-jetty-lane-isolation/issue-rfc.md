# [RFC] URMA send jetty lane isolation

**关联 PR**: [!1192](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1192)

## 背景与目标描述

异步写入 meta/data colocate 后，client/worker 会直接写入元数据所在远端节点。当前 `UrmaConnection` 逻辑上是 `1 send jetty + 1 recv jetty`，错误处理简单，但多个 WR 共用同一个 send queue 时，一个 CTP/send jetty 故障会影响队列里的其它 work request，故障影响面被放大。

本 RFC 建议在不拆 connection 模型的前提下，将 URMA 发送侧 lane 化：每个 WR 独占一个 send lane，send queue depth=1；目的端 recv jetty 模型保持不变。

### 目标

1. 最小化改造 `UrmaConnection`，不拆连接边界。
2. recv jetty 与目的端接收队列模型保持不变。
3. send side lane 化，默认 lazy extra lane pool 为 200。
4. 每个 WR acquire 一个 send lane，queue depth=1，completion/error/timeout 后 release 或 retire。
5. CQE/AE 故障按 failed jetty 定位 lane，只恢复对应 lane。
6. 普通路径保持 NUMA affinity 发送语义。
7. fake URMA 可在无真实 URMA 设备的远端环境验证普通路径和故障路径。

### 非目标

- 不改变对外 Client SDK/API。
- 不改变目的端 recv jetty 和接收队列模型。
- 不在本 RFC 中重做 connection 生命周期或服务发现模型。
- 不用 fake URMA 结果替代真实 URMA 性能结论。

## 建议的方案

### send lane 模型

在 `UrmaConnection` 内部维护 send lanes：

```text
lane = local send jetty + imported targetJetty + state + in-flight event
```

发送流程：

1. WR 发送前 acquire lane。
2. 该 lane 的 send queue depth=1。
3. post 成功后 event 记录 lane/jetty id。
4. completion/error/timeout 根据 event 或 completion local id 找回 lane。
5. 正常 completion 释放 lane。
6. 失败或 timeout retire lane，并补充新 lane。

### 资源预算

- `urma_send_jetty_lane_pool_size` 默认 200。
- 当前建议语义为 lazy extra lane pool，上线后可根据容器 jetty 总量再评估是否收敛成严格进程级总预算。

### 故障处理

#### CQE error

- fake/真实 completion 中的 `local_id` 对应 local send jetty id。
- CQE error 按 failed jetty 定位 lane。
- 当前 request 返回失败。
- lane 标记 invalid，并幂等触发重建。

#### AE `URMA_EVENT_JETTY_ERR`

- AE 按 jetty id 定位 failed lane。
- 若 lane 有 in-flight WR，不提前释放旧 targetJetty。
- 通过 retiring list 等 CQE/timeout 收口。

#### AE + CQE 双触发

- lane 使用 shared invalid guard，重复故障通知只触发一次恢复。
- 目标是幂等恢复，而不是新增恢复入口。

#### timeout

- timeout 不直接复用 still in-flight lane。
- timeout 删除 event map，retire 旧 lane，补新 lane。
- 连续 timeout 使用 retiring list 保存多个旧 send jetty/targetJetty，避免覆盖更早未收口资源。

#### partial post

- `UrmaGatherWrite` 已提交部分 WR 后遇到 post 失败，需要等待并清理已提交 events，避免后台 completion 访问已释放上下文。

## 涉及到的变更

### 主要模块

| 模块 | 变更 |
|------|------|
| `src/datasystem/common/rdma/urma_resource.*` | `UrmaConnection` send lanes、lane acquire/release/recreate/retire |
| `src/datasystem/common/rdma/urma_manager.*` | write/read/gather/pipeline 发送前 acquire lane，completion/error/timeout 收口 |
| `src/datasystem/common/rdma/urma_async_event_handler.*` | AE jetty error 按 failed jetty 定位 lane |
| `src/datasystem/common/urma_fake/*` | completion `local_id` 贯通 local send jetty id，支持 CQE lane 定位 |
| `src/datasystem/client/client_worker_api/*` | remote get retry 判断识别 `payload_info` 成功路径 |
| `tests/ut/client/urma_send_lane_test.cpp` | lane acquire/reuse/recreate/timeout retire UT |
| URMA fake/object/kv ST | 普通路径、故障路径、NUMA、fallback/reconnect 回归 |

### 不变项

- 对外 API 不变。
- recv jetty 不变。
- connection 粒度的入口保持不变。
- NUMA affinity 的 `srcChipId/dstChipId` 传递不变。

## 正确性约束

| 约束 | 说明 |
|------|------|
| lane 独占 | in-flight lane 不被其它 WR 复用 |
| completion 释放 | 正常 CQE 后 lane 被释放，可被后续 WR 复用 |
| 故障隔离 | CQE/AE 只恢复 failed lane，不整体牵连 connection |
| timeout 安全 | timeout 不复用 still in-flight lane |
| 连续 timeout 安全 | retiring list 保留多个旧 targetJetty，避免资源被覆盖 |
| queue depth=1 | 同一 send jetty 不堆多个未完成 WR |
| NUMA 正确性 | lane 选择不改变 affinity 决策 |
| mixed payload 正确性 | `payload_info` 成功路径不被误判 all-failed |

## 测试验证

远端环境：`<validation-host>`，复用已有三方库缓存，构建并行度 `-j40`。

### Build

- `cmake -DWITH_TESTS=ON -DBUILD_WITH_URMA_FAKE=ON ...`
- `make -j40 ds_ut`
- `make -j40 ds_st_object_cache ds_ut`
- `make -j40 ds_st_kv_cache`

### UT

- `tests/ut/ds_ut --gtest_filter='*Urma*'`
  - 76/76 PASS。
- `tests/ut/ds_ut --gtest_filter='UrmaFake*'`
  - 61/61 PASS。
- `UrmaSendLaneTest.*`
  - 3/3 PASS。
- fake CQE/AE/queue full/NUMA utility focused UT
  - 27/27 PASS。

### ST

- `tests/st/ds_st_object_cache --gtest_filter='*Urma*:*URMA*:*urma*' --gtest_also_run_disabled_tests`
  - 68/68 PASS。
  - XML 已在验证环境生成并确认 `tests="68" failures="0"`。
- `tests/st/ds_st_kv_cache --gtest_filter='*Urma*:*URMA*:*urma*' --gtest_also_run_disabled_tests`
  - 11/11 PASS。
  - XML 已在验证环境生成并确认 `tests="11" failures="0"`。
- `UrmaNumaAffinityTest.WorkerToWorker`
  - PASS，覆盖普通路径 NUMA affinity 发送。
- `UrmaCqeErrorTest.RemoteWorkerGetCqeError`
  - PASS，覆盖 CQE status 9 lane 故障路径。
- `UrmaAsyncEventTest.RemoteWorkerGetJfsAsyncEvent`
  - PASS，覆盖 AE JETTY_ERR lane 故障路径。
- `UrmaObjectClientTest.TestBatchRemoteGetErrorCode2`
  - PASS，覆盖 injected OOM + partial successful URMA payload mixed batch。
- `UrmaClientHeartbeatReconnectTest.ClientHeartbeatTimeoutReconnectThenUbSetGetSuccess`
  - PASS，覆盖 UB set/get reconnect smoke。

## 兼容性 / 运维说明

- 默认增加 lazy extra send lane pool，可能增加 send jetty/targetJetty 资源占用。
- 若容器 jetty 总量上限为 400，建议初始默认 extra pool 200，并在真实部署容量模型中继续评估总预算是否需要严格化。
- 该变更主要改善故障影响面，不承诺提升真实 URMA 性能。

## 遗留与后续

1. `urma_send_jetty_lane_pool_size` 是否改为严格进程级总 send jetty 预算，需要结合生产容器资源模型确认。
2. fake URMA 已覆盖语义正确性，真实 URMA 长稳/压测仍建议作为上线前补充。
3. AE + CQE 真实硬件组合 race 已通过 shared invalid guard 做幂等保护，但仍建议在真实设备环境观察。

## 期望的反馈

1. send lane pool 默认值 200 是否符合部署资源预算。
2. timeout retire 而不是立即 release 的处理是否符合错误收口预期。
3. AE/CQE 恢复仍复用原入口、只调整恢复对象为 failed lane 的边界是否认可。
4. object/kv URMA full filter 是否可作为本 PR 最小门禁。
