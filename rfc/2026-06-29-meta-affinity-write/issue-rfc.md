# Meta-Affinity Write — 遗留事项与 Issue 跟踪

**Status**: In-Progress  
**MR**: [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)

---

## Phase 1 已完成（!1151）

- Worker async replicate + `ReplacePrimary(remove_location=false)`
- Remote-only client 直写 meta owner worker
- `SelectObjectLocation` primary 优先
- UT/ST + Get RPC perf 门禁
- CI：Bazel dep + gflag 去重 fix（`0e644bc4`）

---

## GitCode Issues

| # | 标题 | 优先级 | 说明 |
|---|------|--------|------|
| [#6](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/6) | [Feature] Meta-affinity write Phase 2: 同节点 client 直写 meta owner | P2 | 有 local worker 时可选跳过 async replicate，降低 Put→primary_ready 延迟 |
| [#7](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/7) | [Tech Debt] 统一 MetaAffinityClientRingSource 与 ClientHashRingSource | P2 | 与 direct-read 共享 ring 刷新策略，减少重复 etcd/worker bootstrap |
| [#8](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/8) | [Feature] Meta-affinity write: scale 后 changed_ranges 写路由 ST | P2 | EXCLUSIVE_LEVEL2 风格；worker 扩缩容后写/读仍正确 |
| [#9](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/9) | [Feature] 非 binary Publish 路径挂 MetaAffinityReplicate | P3 | 当前仅 `PublishBinaryObject` 成功路径调度 |
| [#10](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/10) | [Feature] Meta-affinity write + Client direct read 组合 ST | P2 | remote-only 直写 + direct read 端到端；依赖 !1119 merge |

---

## check_code 待办

CI `check_code` 初轮失败，OpenLibing 明细需从 MR 门禁链接导出。高概率项（基于代码扫描）：

| 文件 | 规则 | 动作 |
|------|------|------|
| `object_client_impl.cpp` | G.INC.01 | include 顺序（已修 Phase 1） |
| `meta_affinity_write_perf_test.cpp` | G.FMT / G.FUN.01 | 长行、多参数 helper（perf 专用，可 pragma 或拆分） |
| `worker_oc_service_publish_impl.cpp` | G.INC.01 | meta_affinity include 块排序 |

---

## 与上游 MR 依赖

| MR | 关系 |
|----|------|
| !1119 Client Direct Read | 共享 `ReadOnlyHashRingView`；ring source 待统一 ([#7](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/7)) |
| !1153 Meta+Data 合并 RPC | 可选：进一步减少 Get RPC |
| !1151 本 MR | Phase 1 meta-affinity write |

---

## 已创建 Issues（yche-huawei fork）

| # | URL |
|---|-----|
| 6 | https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/6 |
| 7 | https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/7 |
| 8 | https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/8 |
| 9 | https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/9 |
| 10 | https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/10 |
