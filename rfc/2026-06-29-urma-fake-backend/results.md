# URMA Mock Backend - Results

**Status**: Done  
**Last updated**: 2026-07-01 CST  
**HEAD**: `95009cc5bfe15b5432d282091e22ade97a9c7f82`

---

## 1. 构建验证

| 命令 | 结果 | 耗时 |
|------|------|------|
| URMA mock CMake 完整构建 | PASS | 556s |
| native/mock-off CMake 完整构建 | PASS | 561s |
| native/mock-off 产物符号检查 | PASS，未发现 `urma_mock` artifact 或 `ds_urma_mock` 符号残留 | 秒级 |

## 2. UT / CTest

| 项 | 结果 |
|----|------|
| `UrmaGetDataPlaneResultTest.*` | 3/3 PASS，0.05s |
| URMA/RDMA/kv_client_urma 相关 ctest | 160/160 PASS，99.29s；另有 3 个 disabled event-mode case 被 ctest 正常跳过 |

## 3. Object URMA ST

| 项 | 结果 |
|----|------|
| `UrmaObjectClientTestEventMode.*` disabled 用例显式开启运行 | 3/3 PASS，23.04s |

覆盖观察：

- `UrmaObjectClientTest.*`
- disabled event mode cases
- `UrmaClientWorkerDisableUDS.*`
- worker disconnect / CQE error injection
- heartbeat reconnect
- async event
- NUMA affinity
- fallback / disable fallback
- eviction manager end-to-end

## 4. KV URMA ST

| 项 | 结果 |
|----|------|
| KV URMA failover 相关用例 | PASS，已包含在 URMA/RDMA/kv_client_urma 相关 ctest sweep |

覆盖观察：

- local discovery uses UDS
- remote UB/TCP/URMA error switch by discovery
- switch failure keeps current worker available
- healthy window reset
- min samples / threshold update
- local fail + remote fail + switch back

## 5. Codecheck-oriented cleanup

| 项 | 处理 |
|----|------|
| `mock_endpoint.h` 私有 `FdGuard` | 移除 header helper，`.cpp` 复用公共 `Raii` |
| UDS fd collection `const_cast` | `CollectReceivedFds(msghdr &)`，直接遍历 cmsg |
| SCM_RIGHTS fd `reinterpret_cast` | 改为 `std::memcpy` 逐个读取 fd |
| `ImportSeg` 复杂度 | 拆 `HasRegisteredImportEndpoint` / `OpenImportEndpointMemfd` / `MmapImportedMemfd` |
| fake -> mock 命名 | 目录、构建选项、测试目标和文档统一改为 `URMA_MOCK` / `urma_mock` |
| mock opt-in 构建 | 默认/native 构建不编译、不链接 mock backend |
| fallback 统计 | 业务成功但 payload 走 TCP fallback 时，不再误记为 URMA data-plane 成功 |

## 6. 不修改/豁免理由

| 类别 | 理由 |
|------|------|
| `urma_api_compat.h` SDK/C ABI mirror | 必须匹配 URMA SDK 命名、结构、宏和回调形态 |
| `ds_urma_mock_*` C ABI | mock 对外导出符号，需保持 C ABI 和 dlopen entry 一致 |
| mock object opaque handle | 模拟 SDK opaque handle，raw pointer/forward declaration 有边界意义 |
| address-to-pointer mock DMA | mock 模拟远端地址访问的核心边界，不能机械替换 |
| 非 mock 业务文件 | PR1129 目标是本节点 URMA mock，不扩大业务逻辑改动 |

## 7. 待跟踪

- 继续观察 GitCode 门禁，如后续 master 变更导致冲突或构建失败，需要重新 rebase 并回归。
- 如剩余 SDK mirror 问题仍出现，按 ABI mirror 豁免说明处理。
- 后续新增 URMA SDK API 时，同步补充 mock ABI mirror 和 UT/ST 模板。
