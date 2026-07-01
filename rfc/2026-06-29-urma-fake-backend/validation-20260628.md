# URMA Mock 验证记录

## 背景

URMA mock 用共享内存和 UDS 模拟本节点上的远端 DMA 行为，使缺少真实 URMA 设备的环境也能验证 Object/KV cache 的 URMA 数据面、失败注入、fallback、failover、异步事件和迁移 fast transport 路径。

本记录用于说明 PR 1129 的验证范围和结果，不包含本地路径、服务器地址、账号或 token 等环境敏感信息。

## 变更边界

- mock 构建同时定义 `USE_URMA` 和 `USE_URMA_MOCK`，业务判断尽量沿用 `USE_URMA`。
- `USE_URMA_MOCK` 主要用于 mock ABI、dlopen、mock 后端和测试适配；少量 client/worker/rpc 边界仅用于隔离 mock 的 fork owner 刷新、UDS/imported-segment 映射重建和聚合写能力差异。
- 对外 Client API 无变更。
- 不新增 worker RPC proto 字段；mock 验证只复用现有 URMA/UB 请求语义。

## 验证范围

在远端独立构建目录完成验证，使用 `-j40` 构建相关目标，并运行 URMA 相关 ST/UT，包含 disabled 用例。

### ST 全量 URMA Sweep

枚举全部已构建 ST 二进制：

- `ds_st_embedded_client`
- `ds_st_kv_cache`
- `ds_st_object_cache`
- `ds_st_stream_cache`

统一执行：

```bash
--gtest_also_run_disabled_tests --gtest_filter="*Urma*:*URMA*:*urma*"
```

结果：

- `ds_st_embedded_client`: 0 tests, RC=0
- `ds_st_kv_cache`: 11 tests / 1 suite passed, RC=0
- `ds_st_object_cache`: 68 tests / 17 suites passed, RC=0
- `ds_st_stream_cache`: 0 tests, RC=0
- Aggregate RC=0

### UT

- `ds_ut --gtest_also_run_disabled_tests --gtest_filter="*Urma*:*URMA*:*urma*:*MockUrma*:*Uds*:*UDS*:*uds*"`: 79 tests / 13 suites passed。
- `ds_ut_object --gtest_also_run_disabled_tests` 覆盖 URMA fallback limiter、migrate direct、migrate fast transport、spill disabled cases、migrate service rate limit、NotifyRemoteGet migration rate-limit，共 27 tests / 6 suites passed。

## 覆盖场景

- URMA mock same-process/cross-process read/write、memfd/UDS fd transfer、fork 后 listener rebind、queue full、async post-send。
- Object/KV URMA remote get/set/batch get、CQE error injection、fallback limiter、failover tracker、client heartbeat reconnect、async event、NUMA affinity。
- Disabled URMA event-mode cases、worker disconnect async event、spill fast-transport retry case、remote get size-change retry 已纳入运行并通过。
