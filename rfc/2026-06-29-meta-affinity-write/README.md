# Meta-Affinity Write

**Status**: In-Progress  
**仓库**: yuanrong-datasystem  
**分支**: `feature/meta-affinity-write`  
**MR**: [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)  
**Started**: 2026-06-25

## 目标

1. **Primary 与 metadata owner colocate**：写入后 primary 落在 hash 路由的 meta owner worker，减少后续 Get 侧 RPC hop
2. **同节点 async replicate**：有本地 worker 时 Publish 成功 → 异步 `DataMigrator` + `ReplacePrimary(remove_location=false)`，origin 保留 local copy
3. **跨节点直写 meta owner**：无 healthy local worker 时 `GetWriteWorkerApi()` 直连 meta owner（复用 `ReadOnlyHashRingView`），跳过 gateway + replicate
4. **读侧对齐**：`SelectObjectLocation` 多副本优先 primary；本地 hit 仍 serve local copy

## 文档

| 文档 | 说明 |
|------|------|
| [design.md](./design.md) | **模块设计**：As-Is/To-Be、组件职责、与 direct-read 共享边界 |
| [as-is-to-be-sequences.md](./as-is-to-be-sequences.md) | 时序：同节点 replicate、remote-only 直写、Get RPC 路径 |
| [perf-verification.md](./perf-verification.md) | **性能门禁**：Put primary_ready、Get RPC reduction（4KB） |
| [worktree-verify.md](./worktree-verify.md) | 远端 tiantiyun worktree 验证命令与目录布局 |
| [results.md](./results.md) | UT/ST/perf/CI 实测记录 |
| [issue-rfc.md](./issue-rfc.md) | 遗留事项与 GitCode Issue 跟踪 |
| [pr-description.md](./pr-description.md) | MR !1151 描述（同步 CI 验证摘要） |

## 阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | Worker async replicate + gflag | **Done** |
| P1 | Remote-only 直写 meta owner + client ring | **Done** |
| P1 | 读侧 primary 优先 + colocate ST | **Done** |
| P1 | Get RPC perf 门禁 ST | **Done** |
| P2 | 同节点 client 直写 meta owner（省 replicate 延迟） | Deferred |
| P2 | 与 direct-read 统一 ring source | Deferred |
| P2 | Scale 后 full migration 路由 ST | Deferred |
| P3 | 非 binary Publish 路径挂 replicate | Deferred |

## 与 Client Direct Read 关系

| 能力 | Direct Read (!1119) | Meta-Affinity Write (!1151) |
|------|---------------------|----------------------------|
| 共享组件 | `ReadOnlyHashRingView` | 同左（client 侧 ring 计算） |
| 触发条件 | `!HasHealthyLocalWorker()` 读 | 写：`!HasHealthyLocalWorker()` + flag |
| Ring 刷新 | `ClientHashRingSource` | `MetaAffinityClientRingSource`（待统一，见 issue） |
| 目标 | 减少 Get meta/data RPC | 减少 Write replicate + Get RPC |

详见 [design.md §6](./design.md#6-与-client-direct-read-的边界).
