# RFC: Worker SHM 泄漏可观测增强（Object / Buffer / Ref / TTL 释放对账）

- **Status**: **In-Progress**（单 PR 已起，评审中）
  - 三阶段（设计层面分阶段，落地合并为单 commit / 单 PR）已 squash 到分支 [`feat/shm-leak-metrics-phase1`](https://gitcode.com/yche-huawei/yuanrong-datasystem/tree/feat/shm-leak-metrics-phase1) 的 commit `3bbcc55a`
  - MR：[gitcode #635](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/635)
  - PR 描述见 [`pr-description.md`](pr-description.md)
  - 总变更：23 files / +734 / -6 lines / 18 metric / 22 UT cases / ≤16 ms 总耗时
- **Started**: 2026-04-19
- **Owner**: 待指定
- **Depends on**: PR #584（lightweight metrics framework）/ PR #586（KvMetricId 框架）
- **关联背景**: [`vibe-coding-files/bugfix/2026-04-19-worker-shm-oom-问题定位.md`](../../bugfix/2026-04-19-worker-shm-oom-问题定位.md)

---

## 一句话目标

针对 4-19 worker `shm.memUsage` 100s 内从 3.58 GB 涨到 37.5 GB 触发 OOM、且 `OBJECT_COUNT` 反向下降的现场，**新增 18 条 metric**（10 worker + 6 master + 2 client），覆盖**对象元数据 / Buffer / ShmUnit 引用 / 缓存淘汰 / TTL 链路**的释放对账，让现场凭 `Metrics Summary` 时间序列就能定位以下 7 类问题：

| 想看什么 | 证据组合（≤ 3 条曲线） |
|---|---|
| 元数据已删但 shm 未释放（bugfix §3.2 d 主因） | `WORKER_ALLOC - FREE` 单调涨 + `WORKER_SHM_REF_TABLE_BYTES` 单调涨 + `OBJECT_COUNT` 持平 |
| TTL 完全没跑 | `MASTER_TTL_FIRE_TOTAL` delta = 0 / `MASTER_TTL_PENDING_SIZE` > 0 |
| TTL 跑了但删不掉 | `MASTER_TTL_FIRE - DELETE_SUCCESS` 差值持续涨 / `MASTER_TTL_DELETE_FAILED_TOTAL` spike |
| TTL 退避堆积 | `MASTER_TTL_RETRY_TOTAL` 持续涨 |
| Master 元数据泄漏 | `MASTER_OBJECT_META_TABLE_SIZE` 单调涨 / worker `OBJECT_COUNT` 不涨 |
| 释放靠 RemoveClient 兜底 | `WORKER_REMOVE_CLIENT_REFS_TOTAL` 持续 > 0 |
| Client 切 standby 漏 ref（bugfix §3.3） | `CLIENT_DEC_REF_SKIPPED_TOTAL` spike + `WORKER_REMOVE_CLIENT_REFS_TOTAL` 滞后 spike |

---

## 设计约束（硬约束）

- **零锁、零遍历**：每条 metric 只能是 `atomic fetch_add` / `atomic store` / `atomic load`，或对 TBB `concurrent_hash_map` 的 O(1) lock-free `.size()` 单点周期读取；任何 `for (...)` 扫表 / `map.find` / 自旋锁 / 共享锁查询都禁止。
- **单点开销 ≤ 50 ns**：典型为 1 次 `std::atomic::fetch_add`（约 15 ns）；最重的 `AddShmUnit` 也只有 2 次 atomic（≤ 30 ns）。
- **埋点位置 ≤ 12 处**：每处只加 1-2 行，集中在已有的"加 / 删 / 分配 / 释放"动作旁。
- **size Gauge 优先用周期 `.size()` 单点读取**（TBB 容器 O(1) lock-free），避免散落 add/remove 站点逐点维护造成的长期漂移；只有 **bytes Gauge** 因容器不知 `shmUnit->size`，必须在 add/remove 站点 atomic 维护。
- **不做现场快照 / dump 接口**：所有"对账"留给读图人（看时间序列趋势）。
- **不在采集侧做 join / 分类计算**：禁止 `objectTable` × `memoryRefTable` 求差集等操作。

---

## 落地位置（datasystem）

所有 metric ID 沿用 `src/datasystem/common/metrics/kv_metrics.{h,cpp}` 的 `KvMetricId` 枚举末尾追加。

| 类别 | 指标 | 采集位置（单一埋点） |
|---|---|---|
| Worker · Allocator | `WORKER_ALLOCATOR_ALLOC_BYTES_TOTAL` / `..._FREE_BYTES_TOTAL` | `common/shared_memory/allocator.cpp::AllocateMemory / FreeMemory` |
| Worker · ShmUnit 生命周期 | `WORKER_SHM_UNIT_CREATED_TOTAL` / `..._DESTROYED_TOTAL` | `common/shared_memory/shm_unit.cpp::ShmUnit() / ~ShmUnit()` |
| Worker · Ref Table | `WORKER_SHM_REF_ADD_TOTAL` / `..._REMOVE_TOTAL` / `..._TABLE_SIZE` (Gauge) / `..._TABLE_BYTES` (Gauge) | `common/object_cache/object_ref_info.cpp::AddShmUnit(s) / RemoveShmUnit / RemoveClient` |
| Worker · 释放兜底 | `WORKER_REMOVE_CLIENT_REFS_TOTAL` | `RemoveClient` 入口 |
| Worker · ObjectTable | `WORKER_OBJECT_ERASE_TOTAL` | `worker/object_cache/service/worker_oc_service_crud_common_api.cpp::ClearObject` |
| Master · 元数据 | `MASTER_OBJECT_META_TABLE_SIZE` (Gauge) | master 元数据表 Insert / Erase |
| Master · TTL 队列 | `MASTER_TTL_PENDING_SIZE` (Gauge) | `master/object_cache/expired_object_manager.cpp::InsertObject` / `Run()` |
| Master · TTL 触发 | `MASTER_TTL_FIRE_TOTAL` / `..._DELETE_SUCCESS_TOTAL` / `..._DELETE_FAILED_TOTAL` / `..._RETRY_TOTAL` | `Run()` 扫到期点 / `AsyncDelete` 成功 / 失败 / `AddFailedObject` 重排 |
| Client · 异步释放 | `CLIENT_ASYNC_RELEASE_QUEUE_SIZE` (Gauge) | `client/object_cache/object_client_impl.cpp::StartMetricsThread` 周期 lambda |
| Client · ref skip | `CLIENT_DEC_REF_SKIPPED_TOTAL` | `DecreaseReferenceCntImpl` 3 处 early return |

**不修改错误码、不改业务行为、不增加同步阻塞**。

---

## 本目录文件

| 文件 | 说明 |
|---|---|
| [README.md](README.md) | 本文件，目标 + 落点摘要 + 状态 |
| [design.md](design.md) | 完整方案：18 条 metric 详表、采集点代码定位、热路径开销分析、对照决策表、分阶段实施 |
| [issue-rfc.md](issue-rfc.md) | Issue / RFC 文案（提社区或内部评审用） |

---

## 对外文档去向

- metric 清单与运行期读取方式 → 完成后追加到 [`docs/observable/05-metrics-and-perf.md`](../../docs/observable/05-metrics-and-perf.md)
- "释放对账"场景 + 决策树 → 追加到 [`docs/observable/07-pr-metrics-fault-localization.md`](../../docs/observable/07-pr-metrics-fault-localization.md) §3 / §6
- "孤儿 ref 快速判定 SOP" → 追加到 [`docs/observable/04-triage-handbook.md`](../../docs/observable/04-triage-handbook.md)

---

## 不在本期 scope（明确隔离）

| 工作项 | 去向 |
|---|---|
| 修复方案（bugfix §5 P0/P0'/P1/P0''/P0'''/P1''/P1') | 单独 fix RFC，不进本期 |
| 业务侧限流 / 缩容窗口缩短 / `node_dead_timeout_s` 调参 | 运维侧 |
| Per-client / per-shmId / age histogram 等需要遍历的指标 | 已明确不做（违反零遍历约束） |
| 现场快照 dump 接口（HTTP / SIGUSR1） | 已明确不做（违反无快照约束） |
| Eviction Action 分类计数（DELETE / SPILL / FREE_MEMORY / END_LIFE） | 等首期 18 条上线后看是否真的需要再补 |
| 接管端 GIncreaseRef / SwitchToStandbyWorkerImpl 计数 | 通过同样 18 条 metric 在 standby worker 上观测即可 |
