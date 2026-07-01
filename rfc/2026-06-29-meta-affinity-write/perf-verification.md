# Meta-Affinity Write — 性能验证

**Status**: Done (Phase 1 gates)  
**Branch**: `feature/meta-affinity-write` · **HEAD**: `0e644bc4`

---

## 环境变量（仅测试用例）

下表变量只由 `tests/st/client/object_cache/meta_affinity_write_perf_test.cpp` 读取，用来启用 manual perf ST、选择场景和设置样本规模。它们不是生产源代码配置；产品行为开关仍是 gflag `enable_meta_affinity_replicate` 和 `enable_distributed_master`。

| 变量 | 默认 | 说明 |
|------|------|------|
| `DS_META_AFFINITY_WRITE_PERF` | off | 启用 perf ST |
| `DS_META_AFFINITY_WRITE_PERF_RPC` | off | 4KB Get RPC 门禁模式 |
| `DS_META_AFFINITY_WRITE_PERF_ASSERT` | off | 自动断言 improvement 阈值 |
| `DS_META_AFFINITY_WRITE_PERF_SIZE` | 4096 (RPC) / 256KB | payload 大小 |
| `DS_META_AFFINITY_WRITE_PERF_WARMUP` | 15 (RPC) / 20 | warmup iters |
| `DS_META_AFFINITY_WRITE_PERF_ITERS` | 60 (RPC) / 200 | 测量 iters |
| `DS_META_AFFINITY_WRITE_PERF_MODE` | all | `local` / `remote` / `all` |

---

## Put 门禁（64KB，optional）

| Scenario | primary_ready | 判定 |
|----------|--------------|------|
| 同节点 gateway + async replicate | ~150–190 ms | 基线 |
| remote-only 直写 meta owner | **Put 返回时 primary @ meta owner** | 少 replicate hop |

---

## Get RPC 门禁（4KB，`ASSERT=1`）

**用例：** `MetaAffinityWritePerfTest.GetRpcReductionSameNodeBenchmark` / `GetRpcReductionCrossNodeBenchmark`

### 同节点（Client@W0）

| Scenario | Get avg (µs) | 说明 |
|----------|-------------:|------|
| `rpc_same_node_colocated_put_get_immediate` | ~1735 | meta W0 Put+立即 Get |
| `rpc_same_node_cross_worker_cold_get_after_replicate` | ~5863 | gateway replicate + Invalidate 冷 Get |
| `rpc_same_node_cross_worker_put_get_immediate` | ~1622 | 直写 W1 + 立即 Get |

**阈值：** colocated vs cross-worker cold Get improvement **> 15%**（实测 ~**70%**）

### 跨节点（无 local worker）

| Scenario | Get avg (µs) | 说明 |
|----------|-------------:|------|
| `rpc_cross_node_w0_cold_get_after_gateway_replicate` | ~5889 | W0 reader 基线 |
| `rpc_cross_node_w0_cold_get_after_direct_remote_write` | ~1896 | W0 reader 直写后冷 Get |
| `rpc_cross_node_remote_put_get_immediate_direct` | ~4148 | remote Put+立即 Get |
| `rpc_cross_node_remote_cold_get_after_direct_write` | ~2170 | remote reader 直写冷 Get |
| `rpc_cross_node_remote_cold_get_after_gateway_replicate` | ~2041 | remote reader 基线 |

**阈值：**
- W0 reader gateway vs direct cold Get improvement **> 15%**（实测 ~**68%**）
- remote immediate Get **<** gateway cold Get

---

## 对比验证方法

性能 case 的核心是“同环境、同 payload、同 key 路由口径”下比较不同数据布局带来的 Get RPC hop 变化，而不是泛泛比较开关前后。

| 对比项 | Baseline | 优化后 | 验证点 |
|--------|----------|--------|--------|
| 同节点 Get RPC reduction | client0 写 meta owner=worker1 的 key，先 local worker0 Publish，再等待 async replicate；Invalidate worker0 local copy 后冷读 | client0 写 meta owner=worker0 的 key，Put+Get colocated | colocated immediate Get avg 低于 cross-worker cold Get；`ASSERT=1` 要求 improvement > 15% |
| 跨节点 Get RPC reduction | gateway writer 写入后等待 replicate，reader 冷读 gateway replicate 后对象 | remote-only writer 直写 meta owner，reader 读取 direct write 后对象 | direct cold Get avg 低于 gateway cold Get；remote immediate Get 低于 gateway cold Get |
| Put primary ready | gateway/local 写入后 primary 需要后台迁移 | remote-only 直写 meta owner，Put 返回即 primary@meta owner | 看 `primary_immediate_at_put_return_count` 和 `primary_ready_*`，不要把 async ready 时间算进 Put RPC |

测试证据来源：

- `META_AFFINITY_WRITE_PERF_JSON=...`: 每个 scenario 的 payload、warmup、iters、put/get avg、p99、p99.99、primary ready。
- `META_AFFINITY_GET_RPC_REDUCTION_JSON=...`: 同节点和跨节点 RPC reduction 的汇总 avg 与 improvement pct。
- `QueryMeta`/`WaitUntilPrimaryOnWorker`: 确认 baseline 和优化路径的数据布局确实不同，避免误把缓存命中当成 RPC hop 收益。

---

## 输出 JSON

ST 打印：

- `META_AFFINITY_WRITE_PERF_JSON=…`（分 scenario）
- `META_AFFINITY_GET_RPC_REDUCTION_JSON=…`（汇总）

---

## 运行命令

```bash
export DS_META_AFFINITY_WRITE_PERF=1
export DS_META_AFFINITY_WRITE_PERF_RPC=1
export DS_META_AFFINITY_WRITE_PERF_ASSERT=1

./tests/st/ds_st_object_cache --gtest_filter='MetaAffinityWritePerfTest.GetRpcReductionSameNodeBenchmark'
./tests/st/ds_st_object_cache --gtest_filter='MetaAffinityWritePerfTest.GetRpcReductionCrossNodeBenchmark'
```

---

## 与 Direct Read perf 对比

| 维度 | Direct Read (!1119) | Meta-Affinity Write (!1151) |
|------|---------------------|----------------------------|
| 关注点 | 读路径 gateway vs direct | 写后 Get RPC hop 减少 |
| 典型 payload | 256KB | 4KB（RPC 门禁） |
| 门控 | `!HasHealthyLocalWorker()` 读 | 写 + replicate 直写 |

两者可组合验证（Deferred，见 issue）。
