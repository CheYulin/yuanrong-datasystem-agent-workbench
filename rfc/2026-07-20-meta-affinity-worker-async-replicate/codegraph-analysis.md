# CodeGraph 分析 — Worker Async Replicate

**基线 ref**: `main/master` @ `8e09318ef`  
**实现 ref**: `feature/meta-affinity-write` @ `3f4a93c9`  
**索引路径**: `.worktrees/main-master-latest`、`.worktrees/meta-affinity-write`

---

## 1. master 现状（As-Is）

### 1.1 符号搜索

```text
codegraph query "MetaAffinity" --path .worktrees/main-master-latest
→ 0 hits（master 无 meta-affinity 模块）
```

### 1.2 Publish 入口链（master）

CodeGraph `callers` / `callees` 确认 **唯一写入口**：

```text
Publish (443)
  └─ PublishImpl (390)
       └─ PublishObjectWithLock (326)
            └─ PublishObject (280)
```

- `PublishObject` **仅 1 个 caller**：`PublishObjectWithLock`
- master 上 `PublishObject` **末尾无** async replicate 调度

### 1.3 可复用基础设施（master 已有）

| 符号 | 文件 | 用途 |
|------|------|------|
| `DataMigrator::MigrateToTargetNode` | `data_migrator.cpp:158` | 单对象/批量迁到目标 worker |
| `WorkerOcServiceMigrateImpl::ReplacePrimaryImpl` | `worker_oc_service_migrate_impl.cpp` | master 侧 primary 切换 |
| `ClusterManager::GetMetaAddress` | worker get_impl | meta owner 解析 |

`MigrateToTargetNode` 现有 callers（master，4 处）：

- `SubmitL2CacheTasksBySlot` / `TrySubmitSameNodeRetryForL2Slot` / `TrySubmitRedirectRetryForL2Slot`（L2 迁移）
- `RebalanceExecutor::MigrateToTarget`（rebalance）

**结论**：async replicate **无需新建迁移 RPC**，复用 `DataMigrator` + `ReplacePrimary` 即可；feature 分支已验证此路径。

---

## 2. feature 分支实现（To-Be 已编码）

### 2.1 新增模块（git diff stat）

```text
git diff main/master...feature/meta-affinity-write --stat -- 'src/datasystem/worker/**'

 meta_affinity_replicate_executor.cpp   +159
 meta_affinity_replicate_executor.h     +49
 meta_affinity_replicate_manager.cpp   +91
 meta_affinity_replicate_manager.h     +71
 meta_affinity_replicate_param.h         +53
 worker_oc_service_publish_impl.cpp    +50/-9
 worker_oc_service_publish_impl.h      +10/-4
 data_migrator.cpp/h                     +12/-11  (L2 slot 参数传递修正)
 worker_oc_server.cpp                    +10/-13  (gflag DECLARE 去重)

 生产代码合计: +552 / -37 行（worker 目录）
 UT: meta_affinity_replicate_test.cpp    +103
 ST: meta_affinity_replicate_st_test.cpp +111 (+ fixtures/perf 另计)
```

### 2.2 挂接点（CodeGraph impact）

```text
codegraph callers ScheduleMetaAffinityReplicateIfNeeded
→ PublishObject (publish_impl.cpp:355)  [唯一 caller]

codegraph impact ExecuteMetaAffinityReplicate
→ MetaAffinityReplicateFunc, WorkerOcServicePublishImpl ctor  [5 symbols]
```

### 2.3 运行时调用链（feature）

```text
PublishObject 成功返回前
  ScheduleMetaAffinityReplicateIfNeeded(objectKV)
    ├─ gate: FLAGS_enable_meta_affinity_replicate
    ├─ gate: DataFormat == BINARY          ← 非 BINARY 直接 return
    └─ MetaAffinityReplicateManager::AddTask (hash 分片队列)

MetaAffinityReplicateManager::Execute (N 线程)
  MetaAffinityReplicateFunc
    ExecuteMetaAffinityReplicate
      ├─ ClusterManager::GetMetaAddress
      ├─ ShouldScheduleMetaAffinityReplicate (local != meta owner)
      ├─ DataMigrator(MigrateType::SCALE_DOWN)::MigrateToTargetNode
      ├─ ReplacePrimaryWithRetry (remove_location=false, maxRetry=3)
      └─ MarkLocalCopyOnOriginWorker (SetPrimaryCopy(false))
```

### 2.4 CodeGraph callees（ExecuteMetaAffinityReplicate）

已索引确认依赖：

- `GetMetaAddress`
- `DataMigrator::Init` / `MigrateToTargetNode`
- `WorkerMasterOCApi::ReplacePrimary`（经 `ReplacePrimaryWithRetry`）
- `SafeTable::Get` + `SetPrimaryCopy`（origin local copy 标记）

---

## 3. 缺口分析（Gap）

### 3.1 路径覆盖

| 路径 | master | feature | 说明 |
|------|--------|---------|------|
| RPC Publish（binary payload） | 无 replicate | **已挂接** | `payloads` 非空或非 shm 内存路径 |
| RPC Publish（SHM attach） | 无 replicate | **已挂接** | 仍走 `PublishObject`，`SetNewObjectEntry` 默认 `DataFormat::BINARY` |
| `DataFormat::HETERO` | 无 | **跳过** | `ScheduleMetaAffinityReplicateIfNeeded` 显式过滤 |
| `DataFormat::HASH_MAP` | 无 | **跳过** | hset 场景，**不应** replicate（设计正确） |
| Write-back L2 异步落盘 | 无 | **已调度** | replicate 在 `PublishObject` 内、async L2 之前触发 |

### 3.2 生产化缺口（feature 有实现、缺 polish）

| 项 | CodeGraph / 代码证据 | 风险 |
|----|---------------------|------|
| 失败无重入队 | `ExecuteMetaAffinityReplicate` 失败仅 `LOG(WARNING)` | 静默丢 replicate |
| 无 metric | 无 `PerfKey` / histogram | 线上不可观测 |
| Stop 仅析构 | `~MetaAffinityReplicateManager()` → `Stop()`；PublishImpl dtor 触发 | worker 关停时 in-flight 任务可能被截断 |
| `MigrateType::SCALE_DOWN` 语义 | executor 硬编码 SCALE_DOWN | 与 spill/rebalance 日志/策略混用，可维护性 |
| 无 queue depth 背压 | `AddTask` 无上限 | 突发写入可能堆积 |

### 3.3 测试覆盖（feature）

| 类型 | 用例 | 覆盖 |
|------|------|------|
| UT ×4 | `MetaAffinityReplicateTest.*` | 调度条件、gflag、队列 |
| ST | `ColocatePrimaryWithMetaOwnerAndReadLocalCopy` | 端到端 replicate + 双 location 读 |
| ST perf | `GetRpcReduction*Benchmark` | Get RPC（非 replicate 延迟门禁） |

**缺失 ST**：

- replicate 失败 / ReplacePrimary 重试耗尽
- worker 关停 drain
- SHM-only 大对象 replicate
- HETERO device object（若 P2 开启）

---

## 4. 工作量量化

### 4.1 已完成（feature 分支，可随 MR 合入）

| 包 | LOC | 人日（已实现） |
|----|-----|----------------|
| Manager + Executor + Param | ~423 | ~3 |
| Publish 挂接 + gflag | ~60 | ~0.5 |
| UT + 核心 ST | ~214 | ~1 |
| **小计** | **~697** | **~4.5**（已 sunk） |

### 4.2 剩余（到「binary 生产可用」）

| 任务 | 估时 | 文件触达（估） |
|------|------|----------------|
| MR !1151 rebase + CI | 0.5–1d | 冲突面小（master 无同名符号） |
| Metric（success/fail/latency/queue） | 1–1.5d | executor + `kv_metrics` |
| Worker shutdown 显式 drain | 0.5–1d | publish_impl dtor / server Stop |
| 失败策略（至少 counter + 可选重试队列） | 1d | manager + executor |
| replicate 失败 ST + inject | 0.5–1d | st_test |
| **小计** | **3–5d** | ~6–8 文件 |

### 4.3 剩余（到「全格式覆盖」）

| 任务 | 估时 | 说明 |
|------|------|------|
| HETERO migrate 路径调研 + 挂接 | 2–3d | `DataMigrator` / device handler 可能需扩展 |
| HETERO ST | 1–2d | device fixture |
| **小计** | **3–5d** | Issue #9 |

---

## 5. CodeGraph 复现命令

```bash
WT_MAIN=yuanrong-datasystem/.worktrees/main-master-latest
WT_FEAT=yuanrong-datasystem/.worktrees/meta-affinity-write

# master：Publish 链
codegraph callers WorkerOcServicePublishImpl::PublishObject --path $WT_MAIN
codegraph callees  WorkerOcServicePublishImpl::PublishObject --path $WT_MAIN

# feature：replicate 影响面
codegraph callers ScheduleMetaAffinityReplicateIfNeeded --path $WT_FEAT
codegraph impact  ExecuteMetaAffinityReplicate --path $WT_FEAT
codegraph callees ExecuteMetaAffinityReplicate --path $WT_FEAT

# diff 量化
git diff main/master...feature/meta-affinity-write --numstat -- 'src/datasystem/worker/**'
```
