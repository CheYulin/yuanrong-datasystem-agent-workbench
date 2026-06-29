# Meta-Affinity Write — MR 1151

**Branch:** `feature/meta-affinity-write` · **HEAD:** `0e644bc4`  
**MR:** https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151  
**RFC:** [rfc/2026-06-29-meta-affinity-write/](./)

---

## Summary

Phase 1 **meta-affinity write**：让 object **primary 与 metadata owner colocate**，减少后续 Get 侧 RPC hop。

- **同节点写入**：Publish 成功后异步 replicate 到 meta owner worker，`ReplacePrimary(remove_location=false)` — origin worker 保留 local copy。
- **无本地 worker（跨节点）写入**：`GetWriteWorkerApi()` 直写 hash 路由的 meta owner worker，跳过 gateway + 异步 replicate。
- **读侧**：`SelectObjectLocation` 多副本时优先 primary；本地 hit 仍走 local copy。
- **开关**：gflag `-enable_meta_affinity_replicate`（默认 **false**）。

**RFC 文档：** `yuanrong-datasystem-agent-workbench/rfc/2026-06-29-meta-affinity-write/`

---

## 验证（tiantiyun-80c128g）

| 项 | 结果 |
|----|------|
| UT `MetaAffinityReplicateTest.*` × 4 | **4/4** ✅ |
| ST colocate + remote-only 直写 | **2/2** ✅ |
| Get RPC 门禁（4KB，`ASSERT=1`） | **2/2** ✅ |
| Embedded ST（gflag fix 后） | `KVClientCoprocessTest.TestInitEmbeddedWithInvalidParam` ✅ |

### Get RPC（4KB，同节点 ~70% / 跨节点 W0 ~68% 提升）

详见 [perf-verification.md](./perf-verification.md) 与 [results.md](./results.md)。

---

## CI 修复（`0e644bc4`）

- Bazel：`cluster_manager_header` 依赖名修正
- CMake：`master_address` gflag 统一到 `cluster_master_flags.cpp`

---

## Test plan

```bash
ctest --test-dir <build> -R MetaAffinityReplicate -j20
DS_META_AFFINITY_WRITE_PERF=1 DS_META_AFFINITY_WRITE_PERF_RPC=1 DS_META_AFFINITY_WRITE_PERF_ASSERT=1 \
  ./tests/st/ds_st_object_cache --gtest_filter='MetaAffinityWritePerfTest.GetRpcReduction*Benchmark'
```

---

## Deferred（Issues）

见 [issue-rfc.md](./issue-rfc.md) — Phase 2 直写、ring source 统一、scale ST、direct-read 组合 ST。
