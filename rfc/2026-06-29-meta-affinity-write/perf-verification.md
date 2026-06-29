# Meta-Affinity Write — 性能验证

**Status**: Done (Phase 1 gates)  
**Branch**: `feature/meta-affinity-write` · **HEAD**: `0e644bc4`

---

## 环境变量

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
