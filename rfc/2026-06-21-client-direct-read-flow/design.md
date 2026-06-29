# Client Direct Read — 模块设计与 Rich MetaClient 迁移

**Status**: Draft  
**Branch**: `feature/client-direct-read-flow`  
**Related**: [meta-redirect-refactor-progress.md](./meta-redirect-refactor-progress.md), [as-is-to-be-sequences.md](./as-is-to-be-sequences.md)

---

## 1. 背景与问题

P1 已将 QueryMeta **redirect/moving 算法**下沉到 `common/object_cache/read_access/query_meta_redirect_helper`。但 **接口契约未跟上**，导致：

- 读 `ObjectReadAccessFlow` 以为 moving 在上层处理，实际在 Client `DirectReadRpcAdapter` 内
- Client / Worker 各有一条「接线」路径（RpcAdapter vs `QueryMetaFromMasterDirect`）
- Client 存在 **双层重试**（`DirectReadFlow::ExecuteMetaPhaseWithRetry` + inner `QueryMetaWithRedirectAndMoving`）
- `ObjectReadAccessFlow` 跨组 payload 合并与 `query_meta_merge_helper` 模式重复

**症状**：Client `direct_read/` 约 14 文件 ~1200 LOC，Common `read_access/` 约 6 文件 ~550 LOC；控制面改动需跨 4 层读代码。

**目标**：在保持 ClientDirectRead ST 行为不变的前提下，把 **meta 控制面语义**收敛到 Common 可复用实现，Client / Worker 只保留 **传输 + 路由策略 + 产品行为**。

---

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| **算法在 Common** | 分组、redirect/moving、response merge 可单测、可 Worker/Client 共享 |
| **策略在 Role** | ring 刷新顺序、fallback reason、gateway cutback 属 Client；Worker deadline 属 Worker |
| **Transport 最薄** | 一次 RPC = 一次 `QueryMeta` stub 调用 + 签名/超时，不含控制面循环 |
| **单层 moving 重试** | moving/redirect 循环只在 MetaClient 内；外层只处理 route stale 与 fallback |
| **Ports 不变、实现加厚** | 保留 `IObjectReadRouteProvider` / `IObjectReadMetaClient` / `IObjectReadDataClient`；加厚 MetaClient 默认实现 |

---

## 3. 现状分层（As-Is）

### 3.1 模块地图

```
ObjectClientImpl::Get
  ├─ ShouldTryDirectRead / cutback / DirectReadFallback     [Client 产品]
  └─ DirectReadFlow::Get
       ├─ ExecuteMetaPhaseWithRetry                          [Client 外层重试]
       │    └─ ObjectReadAccessFlow::ExecuteMetaPhase        [Common 分组/合并]
       │         ├─ ClientHashRingSource                       [Client ring 策略]
       │         └─ DirectReadMetaClientAdapter::QueryMeta
       │              └─ DirectReadRpcAdapter::QueryMeta     [Client RPC + 编排接线]
       │                   └─ QueryMetaWithRedirectAndMoving  [Common 算法]
       └─ ExecuteDataPhase                                   [Client Data Phase]
            └─ DirectReadDataClientAdapter → GetObjectRemoteTcp
```

Worker 等价路径：

```
QueryMetadataFromMaster / QueryMetaDataFromMasterImpl
  └─ QueryMetaGroupUsingSharedFlow                          [Worker adapter 薄封装]
       └─ ObjectReadAccessFlow::ExecuteMetaPhase
            └─ WorkerObjectReadMetaClient::QueryMeta
                 └─ QueryMetaFromMasterDirect
                      └─ QueryMetaWithRedirectAndMoving      [Common 算法]
```

### 3.2 职责矩阵（现状）

| 模块 | 路径 | 职责 | 归属 |
|------|------|------|------|
| `ObjectReadAccessFlow` | common/read_access | 多 master 分组、跨组合并 | Common ✅ |
| `query_meta_redirect_helper` | common/read_access | moving + redirect 单组编排 | Common ✅ |
| `query_meta_merge_helper` | common/read_access | redirect 响应合并、payload 追加 | Common ✅ |
| `ReadOnlyHashRingView` | common | ring 快照只读计算 | Common ✅ |
| `ClientHashRingSource` | client/direct_read | etcd/worker 刷新 **策略** | Client ✅ |
| `DirectReadRpcAdapter` | client/direct_read | RPC + **redirect/moving 接线** | Client ⚠️ 过厚 |
| `DirectReadFlow` | client/direct_read | 外层重试 + **Data Phase** | Client（部分 ⚠️） |
| `DirectReadFallback` | client/direct_read | 控制面失败 → gateway | Client ✅ |
| `worker_object_read_access_helper` | worker/service | Worker 三 Port 适配 | Worker ✅ |
| `QueryMetaFromMasterDirect` | worker/service | RPC + **redirect/moving 接线** | Worker ⚠️ 与 Client 重复 |

### 3.3 已知结构性问题

1. **`IObjectReadMetaClient::QueryMeta` 语义不足**：接口承诺「返回已解析的单组 meta」，但 Client/Worker 各自在 RPC 层实现解析。
2. **双层重试**：`ExecuteMetaPhaseWithRetry` 与 `RetryWhileMetaIsMoving` 共用 `client_direct_read_retry_count`，职责重叠。
3. **合并重复**：`ObjectReadAccessFlow` L100–114 内联 payload offset，未调用 `MergeQueryMetaResponses` / `AppendQueryMetaPayloads`。
4. **Worker 路由未统一**：大批量仍走 `etcdCM_->GroupObjKeysByMasterHostPort`；仅部分路径用 `ObjectReadAccessFlow`。

---

## 4. 目标分层（To-Be）

### 4.1 目标调用链

```
DirectReadFlow::Get
  ├─ ExecuteMetaPhaseWithRetry          ← 仅 route stale / 非 moving 可重试失败
  │    └─ ObjectReadAccessFlow
  │         ├─ IObjectReadRouteProvider  (ClientHashRingSource / WorkerFixed / 未来 WorkerRing)
  │         └─ IObjectReadMetaClient     ← 默认实现 = QueryMetaOrchestratingMetaClient
  │              └─ IQueryMetaTransport  ← 纯一次 RPC（Client stub / WorkerMasterOCApi）
  └─ ExecuteDataPhase                   ← 仍 Client 专有
```

### 4.2 新增 Common 组件（提议）

#### 4.2.1 `IQueryMetaTransport` — 单次 RPC

```cpp
// common/object_cache/read_access/query_meta_transport.h
class IQueryMetaTransport {
 public:
  virtual ~IQueryMetaTransport() = default;
  virtual Status QueryMetaOnce(const HostPort &metaAddress,
                               const std::vector<std::string> &objectKeys,
                               int64_t subTimeoutMs, bool enableRedirect,
                               master::QueryMetaRspPb &rsp,
                               std::vector<RpcMessage> &payloads) = 0;
};
```

- **Client 实现**：`ClientQueryMetaTransport` — 从现有 `QueryMetaOnce` 匿名函数抽出（Signature、RpcChannel、MasterOCService_Stub）
- **Worker 实现**：`WorkerQueryMetaTransport` — 包装 `WorkerMasterOCApi::QueryMeta`

不含 moving 循环、不含 redirect follow。

#### 4.2.2 `QueryMetaOrchestratingMetaClient` — 默认 `IObjectReadMetaClient`

```cpp
// common/object_cache/read_access/query_meta_orchestrating_meta_client.h
class QueryMetaOrchestratingMetaClient : public IObjectReadMetaClient {
 public:
  struct Options {
    QueryMetaMovingRetryOptions moving;
    QueryMetaRedirectFollowOptions redirect;
  };

  QueryMetaOrchestratingMetaClient(std::shared_ptr<IQueryMetaTransport> transport,
                                   Options options);

  Status QueryMeta(const HostPort &metaAddress,
                   const std::vector<std::string> &objectKeys,
                   int64_t subTimeoutMs,
                   master::QueryMetaRspPb &rsp,
                   std::vector<RpcMessage> &payloads) override;

 private:
  QueryMetaAtMasterFn MakeQueryMetaFn(int64_t subTimeoutMs);
  // ...
};
```

- 内部调用现有 `QueryMetaWithRedirectAndMoving`
- **Client / Worker 只构造 `Options`**（retry budget、resolveRedirect、reject flags）

#### 4.2.3 `ObjectReadAccessFlow` 合并统一

- 跨组 merge 改为调用 `MergeQueryMetaResponses`（或新增 `MergeQueryMetaGroupIntoResult` 包装）
- 删除 Flow 内联 payload offset 逻辑
- `meta_is_moving` 守卫：MetaClient 保证 drain 后仍返回则 `K_RUNTIME_ERROR`（契约强化，非 silent pass）

### 4.3 Client 瘦身目标

| 文件 | 迁移后职责 |
|------|-----------|
| `direct_read_rpc_adapter` | 仅 `GetClusterState`、`GetObjectRemoteTcp`；**删除 `QueryMeta` 编排** |
| `direct_read_access_adapters` | Meta adapter 改为持有 `QueryMetaOrchestratingMetaClient` 或 factory |
| `direct_read_flow` | 外层重试 **仅** `K_NOT_READY`（stale route）等；moving 不再 outer retry |
| `client_hash_ring_source` | 不变 |
| `direct_read_fallback` | 不变 |

### 4.4 Worker 对齐目标

| 现状 | 目标 |
|------|------|
| `QueryMetaFromMasterDirect` 内调 `QueryMetaWithRedirectAndMoving` | 委托 `QueryMetaOrchestratingMetaClient` |
| `QueryMetadataFromRedirectMaster` 单独调 `FollowQueryMetaRedirects` | 评估合并进 Orchestrating client 或标记 legacy |
| `WorkerObjectReadMetaClient` 薄 wrapper | 构造 common MetaClient + Worker transport |

---

## 5. 模块边界（最终态）

### 5.1 留在 Common

| 组件 | 说明 |
|------|------|
| `ObjectReadAccessFlow` | Meta phase 骨架（分组 + 合并） |
| `query_meta_redirect_helper` | moving / redirect 纯算法 |
| `query_meta_merge_helper` | 响应 / payload 合并 |
| `QueryMetaOrchestratingMetaClient` | **新增**：默认 MetaClient 实现 |
| `IQueryMetaTransport` + role impls | **新增**：最薄 RPC |
| `ReadOnlyHashRingView` | ring 快照数学 |

### 5.2 留在 Client

| 组件 | 说明 |
|------|------|
| `DirectReadFlow::ExecuteDataPhase` | inline / TCP、组装 `GetRspPb` |
| `ClientHashRingSource` | etcd bootstrap、worker/etcd fallback **策略** |
| `DirectReadFallback` + `ObjectClientImpl` gate/cutback | 产品路径 |
| `ClientQueryMetaTransport` | Client stub 细节 |
| `DirectReadTestHook` | ST 注入 |

### 5.3 留在 Worker

| 组件 | 说明 |
|------|------|
| `WorkerQueryMetaTransport` | WorkerMasterOCApi |
| `WorkerFixedMetaRouteProvider` | 已知 master 地址场景 |
| 未来 `WorkerHashRingRouteProvider` | P3：复用 `ReadOnlyHashRingView` |
| Get 数据路径（cache/URMA/…） | 与 direct read data phase 无关 |

### 5.4 行为差异配置（不变）

通过 `QueryMetaOrchestratingMetaClient::Options` 注入，**不**在 Client/Worker 复制算法：

| 配置项 | Client | Worker |
|--------|--------|--------|
| `moving.maxMovingRetries` | `client_direct_read_retry_count` | deadline 推导 |
| `moving.beforeMovingRetry` | refreshRoute | 无 / no-op |
| `redirect.resolveRedirectAddress` | `HostPort::ParseString` | `GetPrimaryReplicaAddr` |
| `redirect.rejectNestedRedirectInfo` | `true` | `false` |
| `redirect.rejectMovingOnRedirect` | `true` | `false` |

---

## 6. 重试策略（单层化）

### 6.1 现状（双层）

```
Outer (DirectReadFlow):  attempt 0..N on any retriable control-plane failure
  └─ Inner (QueryMetaWithRedirectAndMoving): moving sleep/retry + redirect follow
```

### 6.2 目标（单层 + 外层 route）

```
Inner (QueryMetaOrchestratingMetaClient):  moving + redirect（完整控制面）
Outer (DirectReadFlow):  仅 K_NOT_READY (stale route) / 可选 ring refresh 后重跑 ExecuteMetaPhase
                         不再因 K_TRY_AGAIN (meta_is_moving) 做 outer loop
```

**理由**：moving 已含 ring refresh callback；outer 再 retry moving 导致预算翻倍且难推理。

**Fallback 映射**仍由 `DirectReadFallback` 在 Client 完成；Worker 无 gateway fallback。

---

## 7. 迁移步骤（P2）

建议 **小步 PR**，每步 CMake + Bazel UT/ST 绿。

### Step 1 — Transport 抽取（无行为变更）

1. 新增 `IQueryMetaTransport`、`ClientQueryMetaTransport`、`WorkerQueryMetaTransport`
2. `DirectReadRpcAdapter::QueryMeta` / `QueryMetaFromMasterDirect` 内部改用 transport，逻辑暂留原位
3. UT：transport mock 单测（可选）

**验收**：ClientDirectRead ST 19/19；Bazel worker/client build。

### Step 2 — `QueryMetaOrchestratingMetaClient`

1. 实现 common MetaClient，从 RpcAdapter / Worker get impl **复制 Options 构造**
2. Client：`DirectReadMetaClientAdapter` 持有 orchestrating client
3. Worker：`WorkerObjectReadMetaClient` 同上
4. 删除 RpcAdapter / `QueryMetaFromMasterDirect` 内直接调 `QueryMetaWithRedirectAndMoving`

**验收**：`query_meta_redirect_helper_test` + `object_read_access_flow_test` + ClientDirectRead ST。

### Step 3 — Flow 合并统一

1. `ObjectReadAccessFlow::ExecuteMetaPhase` 使用 `query_meta_merge_helper`
2. 强化 MetaClient 契约测试（moving drain 后不应返回 `meta_is_moving`）

**验收**：`object_read_access_flow_test` 扩展跨组 merge case。

### Step 4 — 外层重试单层化

1. `ExecuteMetaPhaseWithRetry` 只对 `K_NOT_READY` 等 retriable **非 moving** 状态 retry
2. `K_TRY_AGAIN` from moving → 直接 `DirectReadFallback`（与现 ST 对齐验证）

**验收**：ST `MetaMovingRefreshesRingAndSucceeds`、`RedirectLoopFallsBackOnce` 等。

### Step 5 — Client 目录整理（可选 cosmetic）

1. `direct_read_rpc_adapter` 重命名为 `direct_read_worker_rpc` 或拆 transport 文件
2. 更新 RFC progress / HTML doc

---

## 8. 测试策略

| 层级 | 覆盖 |
|------|------|
| UT | `QueryMetaOrchestratingMetaClient`：Client/Worker options 矩阵（复用 redirect_helper cases） |
| UT | `ObjectReadAccessFlow` 跨组 merge 委托 merge_helper |
| UT | Transport mock：timeout → fallback reason 映射（Client） |
| ST | 现有 `ClientDirectRead*` 19 cases **零行为变更** |
| ST | Worker meta batch 回归（gateway read 路径） |

**回归命令**（tiantiyun）：

```bash
# CMake
ctest -R 'QueryMetaRedirectHelperTest|ReadOnlyHashRingViewTest|ObjectReadAccessFlowTest'
ctest -L 'object ut'
ctest -R 'ClientDirectRead'

# Bazel
bazel test --config=release --config=test \
  //tests/ut/common/object_cache:query_meta_redirect_helper_test \
  //tests/ut/common/object_cache:object_read_access_flow_test \
  //tests/ut/common/object_cache:read_only_hash_ring_view_test
bazel build --config=release --config=test \
  //src/datasystem/worker:datasystem_worker \
  //src/datasystem/client/object_cache:object_client
```

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 单层重试改变 moving 次数 | ST 对比 + 记录 `DirectReadTestHook` 计数基线 |
| Worker tolerant redirect 回归 | 保留 Worker options 单测；不合并 `QueryMetadataFromRedirectMaster` 直到等效证明 |
| BUILD 依赖膨胀 | 新文件进现有 `common_oc_read_access` target |
| TestHook 与 transport 耦合 | TestHook 留在 Client transport 或 injectable predicate |

---

## 10. 后续（P3/P4，本文不展开）

- **P3 Worker HashRing**：`WorkerHashRingRouteProvider` + `ReadOnlyHashRingView`，与 Client 共享快照数学
- **P4 URMA direct read**：新 `IObjectReadDataClient` 实现；MetaClient 层不变
- **Rich data phase**（可选）：若 TCP/URMA 收敛，再抽 common data orchestrator；当前不阻塞 P2

---

## 11. 附录：目录对照

```
common/object_cache/read_access/
  object_read_access_flow.{h,cpp}           # 已有
  query_meta_redirect_helper.{h,cpp}        # 已有
  query_meta_merge_helper.{h,cpp}           # 已有
  query_meta_transport.h                    # 新增：IQueryMetaTransport
  query_meta_orchestrating_meta_client.{h,cpp}  # 新增

client/object_cache/direct_read/
  client_query_meta_transport.{h,cpp}       # 新增（从 rpc_adapter 抽出）
  direct_read_rpc_adapter.{h,cpp}           # 瘦身：去掉 QueryMeta 编排
  direct_read_flow.{h,cpp}                  # 外层重试收敛
  ...（ring / fallback / test_hook 不变）

worker/object_cache/service/
  worker_query_meta_transport.{h,cpp}       # 新增
  worker_object_read_access_helper.{h,cpp}  # 改用 OrchestratingMetaClient
  worker_oc_service_get_impl.cpp            # QueryMetaFromMasterDirect 瘦身
```

---

## 12. 成功标准（P2 Done）

- [x] Client `direct_read/` 无 `QueryMetaWithRedirectAndMoving` 直接调用
- [x] Worker `QueryMetaFromMasterDirect` 无重复 redirect/moving 接线
- [x] `ObjectReadAccessFlow` 跨组合并使用 merge_helper
- [x] 外层重试语义 documented + ST 全绿
- [x] CMake + Bazel 双构建通过
