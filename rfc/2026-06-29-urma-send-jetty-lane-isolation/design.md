# URMA Send Jetty Lane Isolation — Design

**Status**: Draft  
**Branch**: `feature/urma-send-jetty-lane-isolation`

## 1. 背景问题

当前 `UrmaConnection` 是 `1 send jetty + 1 targetJetty + 1 recv/local jetty` 的模型，错误处理简单：AE `URMA_EVENT_JETTY_ERR` 和 CQE error 都收敛到 `connection->ReCreateJetty`。

新的异步写入场景希望 client/worker 直接把数据写到元数据所在节点，达到 meta/data colocate。client 没有本地 worker 时，会直接写远端节点。这个路径下 jetty 是稀缺资源，估算每容器最多约 400 个 jetty，因此发送侧需要池化复用，默认建议 200 个。

问题在于单 send jetty 上如果排了多个 WR，某个 CTP/jetty 故障会影响同 jetty 内所有已 post 的 work request。为了避免一个 CTP 连接故障影响其他请求，需要把发送侧拆成 lane，并保证每个 WR 独占一个 lane。

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| connection 边界不拆 | `urmaConnectionMap_`、握手、segment import 仍以 connection 为单位 |
| recv jetty 不动 | 目的端当前是单边写，未使用目的端 jetty 队列，不引入 recv lane |
| send side 最小 lane 化 | 只把 `(send jetty, targetJetty)` 做 lane；remote segment cache 仍共享 |
| 每个 WR 独占 lane | acquire lane -> post one WR -> completion/error/timeout delete event -> release lane |
| 故障入口不新增 | AE/CQE 仍调用 `ReCreateJetty`，只把恢复对象从 connection 单 jetty 改为 failed lane |
| NUMA affinity 不改语义 | `UrmaWriteImpl` 仍按 `srcChipId/dstChipId` 调用 `PostJettyRw(..., true, ...)` |

## 3. As-Is

### 3.1 连接建立

- `ImportRemoteJetty` / `FinalizeOutboundConnection`
  - `UrmaResource::CreateJetty`
  - `UrmaManager::ImportTargetJetty(remoteInfo, targetJetty, jetty->Raw())`
  - `UrmaConnection(jetty, targetJetty, remoteInfo)`

### 3.2 发送路径

- `UrmaWritePayload`
  - `GetJettyFromConnection(connection, jetty)`
  - `connection->GetTargetJetty()`
  - `UrmaWriteImpl` 对所有 chunk 复用同一个 `jetty/targetJetty`
- `UrmaRead`
  - 同一个 read 循环复用同一个 `jetty/targetJetty`
- `UrmaGatherWrite`
  - 构造 WR 链表，一次 `ds_urma_post_jetty_send_wr(jetty, &wrList[0], ...)`

### 3.3 故障路径

- CQE:
  - `CheckCompletionRecordStatus`
  - `completeRecords[i].local_id`
  - `TryRecoverFailedJettyFromCompletion`
  - `connection->ReCreateJetty(*urmaResource_, failedJetty)`
- AE:
  - `UrmaAsyncEventHandler::HandleJettyErrAsyncEvent`
  - raw jetty id -> `UrmaResource::GetJettyById`
  - `connection->ReCreateJetty(*urmaResource_, failedJetty)`

## 4. To-Be

### 4.1 Send lane

`UrmaConnection` 内部持有 `sendLanes_`：

```cpp
struct SendLane {
    std::shared_ptr<UrmaJetty> jetty;
    std::shared_ptr<UrmaJetty> retiringJetty;
    std::unique_ptr<UrmaTargetJetty> targetJetty;
    bool inUse = false;
};
```

lane 生命周期：

| 阶段 | 状态 |
|------|------|
| connection init | 第一个 lane 来自原连接建立路径 |
| acquire | 找 idle+valid lane；没有 idle 时 lazy 创建新 lane |
| post | `UrmaEvent` 保存提交时的 jetty snapshot |
| complete/error | poll thread notify；waiter `DeleteEvent` |
| release | `DeleteEvent` 用 event jetty snapshot 回到 connection release lane |
| fault recreate | failed jetty 所属 lane 创建新 `(jetty,targetJetty)`；如果旧 WR 仍 in-flight，新 lane 保持 busy，等旧 event 收口后 release |

### 4.2 进程级预算

新增 flag：

```text
--urma_send_jetty_lane_pool_size=200
```

当前实现采用 lazy 扩池：初始 lane 沿用连接创建；额外 lane 通过 `UrmaResource::TryAcquireSendLaneSlot()` 占用进程级预算。连接清理时释放 lazy lane slot。

### 4.3 发送路径修改

| 模块 | 修改 |
|------|------|
| `UrmaConnection` | 新增 `AcquireSendLane` / `ReleaseSendLane` / lane 级 `ReCreateJetty` |
| `UrmaResource` | 下沉 `ImportTargetJetty`，用于故障恢复时 reimport targetJetty |
| `UrmaManager::DeleteEvent` | 删除 event 前释放 event 对应 lane |
| `UrmaWriteImpl` | 每个 chunk acquire lane；NUMA 分支不变 |
| `UrmaRead` | 每个 read chunk acquire lane |
| `UrmaGatherWrite` | 从 WR 链表一次 post 改为每个 dst chunk 单独 post |
| fake URMA | completion `local_id` 贯通发送 jetty id，支持 CQE lane 定位测试 |

## 5. 故障处理

### 5.1 CQE error

1. fake/real CQE 提供 `local_id`。
2. `TryRecoverFailedJettyFromCompletion` 按 `local_id` 查 `UrmaResource` jetty registry。
3. 从 failed jetty 找 owning connection。
4. `ReCreateJetty` 在 connection 内按 failed jetty 找 lane。
5. `MarkInvalid()` 保证同一个 failed jetty 只恢复一次。
6. lane 创建新 send jetty，并重新 import targetJetty。
7. 当前 request 仍按 CQE error 返回失败；event 删除时释放 lane。

### 5.2 AE `URMA_EVENT_JETTY_ERR`

1. AE raw jetty id 查 registry。
2. 找 owning connection。
3. `ReCreateJetty` 按 lane 替换。
4. 如果 lane 有 in-flight request，新 lane 暂不释放给其他请求，直到旧 request 的 CQE/timeout 经 `DeleteEvent` 收口。

### 5.3 AE + CQE 双触发

`UrmaJetty::MarkInvalid()` 是幂等门闩。AE 先触发后，CQE 再触发时会看到 failed jetty 已 invalid，跳过重复 recreate；反向顺序同理。

## 6. NUMA affinity

`UrmaWriteImpl` 仍在每个 WR 上计算：

```cpp
const bool useNumaAffinity =
    IsUbNumaAffinityEnabled() && srcChipId != INVALID_CHIP_ID && dstChipId != INVALID_CHIP_ID;
```

lane 只选择 `(jetty,targetJetty)`，不改写 `srcChipId/dstChipId`。因此 `PostJettyRw(... true, srcChipId, dstChipId)` 仍触发 `UrmaManager.UrmaWriteNumaAffinity` 注入点。

## 7. 测试矩阵

### 普通场景

| 场景 | 目标 |
|------|------|
| fake URMA UT 全量 | fake ABI 与 post-send completion 不回退 |
| UB set/get | 普通写路径成功 |
| worker-worker remote get/write | direct worker data path 成功 |
| 大对象多 WR | 不同 WR 可分配不同 send lane |
| `UrmaNumaAffinityTest.WorkerToWorker` | NUMA affinity 分支仍触发 |

### 故障场景

| 场景 | 目标 |
|------|------|
| CQE status 9 | 按 `local_id` 定位并恢复单 lane |
| AE `JETTY_ERR` | 按 jetty id 恢复单 lane |
| AE + CQE | `MarkInvalid()` 幂等，只恢复一次 |
| lane 重建 | 新 send jetty 重新 import targetJetty |
| 小池资源上限 | 无 idle lane 时 backpressure/timeout |

## 8. 当前实现状态

已完成：

- fake completion `local_id` 从 post-send snapshot 贯通到 `urma_cr_t`。
- `UrmaConnection` 初版 send lane、lazy pool、event release。
- `UrmaWriteImpl` / `UrmaRead` / `UrmaGatherWrite` 切为每 WR acquire lane。
- `ReCreateJetty` 初版 lane 级替换与 targetJetty reimport。

待补强：

- manager/fake 层 UT：多 WR lane id 分配、backpressure、AE/CQE 幂等。
- targeted ST：UB set/get、worker-worker remote get/write、NUMA affinity。
- 评估初始 lane 是否也纳入 200 进程预算，目前 lazy extra lane 纳入预算。
