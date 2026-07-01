# URMA Mock Backend - PR1129

**Title:** `feat(urma): add opt-in mock backend for local validation`
**Branch:** `feat/urma-fake-r11-rebase`
**HEAD:** `95009cc5bfe15b5432d282091e22ade97a9c7f82`
**PR:** https://gitcode.com/openeuler/yuanrong-datasystem/pull/1129
**RFC:** `rfc/2026-06-29-urma-fake-backend`

---

## PR Body

**这是什么类型的PR？**

/kind feat
/kind test
/kind build

----

**这个PR是做什么的/我们为什么需要它**

本 PR 引入 URMA mock 本节点验证能力，用共享内存和 UDS 模拟本节点跨进程的远端 DMA 行为，使缺少真实 URMA 设备和 SDK 的开发/CI 环境也能覆盖 Object/KV cache 的 URMA 数据面、错误注入、fallback 与 failover 语义。

主要改动：

- 新增 `src/datasystem/common/urma_mock`，提供 opt-in 的 URMA mock backend、mock ABI、memfd/UDS fd transfer、completion/event/error injection、thread pool 与 mock object 生命周期管理。
- 在 `common/rdma` 内收敛 native URMA 与 mock 的边界：native 路径继续使用官方 URMA SDK 头文件，mock 仅在 `BUILD_WITH_URMA_MOCK` / `--config=urma_mock` 显式开启时进入构建和符号分派。
- 通过 `urma_dlopen_util` / `urma_mock_bridge` / `urma_api_adapter` 隐藏 mock dispatch table 和 ABI 兼容细节，业务代码仍尽量使用 `USE_URMA` 语义，避免把 mock 专属逻辑扩散到 client/worker/rpc 主流程。
- 支持 CQE error、async event、wait timeout、queue full、worker reconnect、fallback limiter、disable fallback 等路径，补齐 URMA 失败后 TCP fallback 的可重复验证能力。
- 将 URMA 相关 ST/UT 调整为 mock 构建下可运行，并新增/补充 URMA mock、fallback、data-plane 统计等测试。
- 新增设计和验证记录：`docs/source_zh_cn/design_document/urma_mock_validation_20260628.md`。

边界说明：

- URMA mock 只验证语义正确性，不作为真实 URMA 性能依据。
- 默认/native 构建不编译、不链接 mock backend，不携带 `ds_urma_mock_*` 符号或 UDS/mock thread pool 代码。
- `urma_api_compat.h` 仅用于 mock 构建和 mock 实现，native URMA 实现仍以官方 SDK 头文件为准。
- 少量 client/worker/common 改动仅用于维持已有 URMA fallback 语义、fork 后 owner 生命周期、mock-off 构建隔离和测试可观测性；不改变原生 URMA 预热主流程。

----

**此PR修复了哪些问题**:

Fixes #

----

**PR对程序接口进行了哪些修改？**

- 不涉及公开 SDK/API 接口变更。
- 新增内部构建选项和测试配置：`BUILD_WITH_URMA_MOCK`、`--config=urma_mock`、`USE_URMA_MOCK`。
- 新增内部 mock ABI/bridge/adapter，仅用于 URMA mock 构建和测试验证，不作为对外接口。
- 不新增 worker RPC proto 字段；mock 验证复用现有 URMA/UB 请求和 fallback 语义。

----

**如何使用和验证**

CMake mock 构建：

```bash
DS_OPENSOURCE_DIR=<thirdparty-cache-dir> \
  bash build.sh -U on -X off -P off -J off -G off \
  -B <mock-build-dir> -o <mock-output-dir> -j 80 -t build
```

CMake native/mock-off 构建隔离验证：

```bash
DS_OPENSOURCE_DIR=<thirdparty-cache-dir> \
  bash build.sh -U off -M off -A off -X off -P off -J off -G off \
  -B <native-build-dir> -o <native-output-dir> -j 80 -t build

find <native-build-dir> <native-output-dir> \
  \( -name "*urma_mock*" -o -name "libcommon_urma_mock*" \)
nm -A <native-build-dir>/src/datasystem/common/rdma/libcommon_rdma.a \
  | grep ds_urma_mock
```

URMA 相关回归：

```bash
cd <mock-build-dir>

./tests/ut/ds_ut \
  --gtest_filter="UrmaGetDataPlaneResultTest.*"

ctest --output-on-failure \
  -R "Urma|URMA|Rdma|RDMA|kv_client_urma" -j 8

./tests/st/ds_st_object_cache \
  --gtest_also_run_disabled_tests \
  --gtest_filter="UrmaObjectClientTestEventMode.*"
```

本次验证规模和耗时：

| 验证项 | 覆盖 cases | 本次耗时 |
|------|------------|----------|
| URMA mock CMake 完整构建 | 构建全部 mock-enabled targets、UT/ST binary、example | 556s |
| native/mock-off CMake 完整构建 | 构建默认/native targets，并检查 mock artifact/symbol 不存在 | 561s |
| `UrmaGetDataPlaneResultTest.*` | 3/3 PASS | 0.05s |
| URMA/RDMA/kv_client_urma 相关 ctest | 160/160 PASS；另有 3 个 disabled event-mode case 被 ctest 正常跳过 | 99.29s |
| `UrmaObjectClientTestEventMode.*` disabled 用例显式开启运行 | 3/3 PASS | 23.04s |

ctest sweep 重点包含：

- `UrmaMockBackendTest.*`、`UrmaMockDispatchTest.*`、`UrmaMockInject*`、`UrmaMockUdsMemfdTransportTest.*`、`UrmaMockWritePayloadTest.*`
- `UrmaSuccessRateTrackerTest.*`、`UrmaFallbackTcpLimiterTest.*`
- `MigrateDataDirectTest.*Urma*`、`MigrateDataHandlerTest.*Urma*`
- `UrmaConnectionWarmup*`、`UrmaObjectClientTest.*`、`UrmaClientWorkerDisableUDS.*`
- `UrmaCqeErrorTest.*`、`UrmaAsyncEventTest.*`、`UrmaFallbackTest.*`、`UrmaDisableFallbackTest.*`
- `KVClientUrmaFailoverTest.*`

----

**Self-checklist**:（**请自检，在[ ]内打上x，我们将检视你的完成情况，否则会导致pr无法合入**）

+ - [x] **设计**：PR对应的方案是否已经经过Maintainer评审，方案检视意见是否均已答复并完成方案修改
+ - [x] **测试**：PR中的代码是否已有UT/ST测试用例进行充分的覆盖，新增测试用例是否随本PR一并上库或已经上库
+ - [x] **验证**：PR描述信息中是否已包含对该PR对应的Feature、Refactor、Bugfix的预期目标达成情况的详细验证结果描述
+ - [x] **接口**：是否涉及对外接口变更，相应变更已得到接口评审组织的通过，API对应的注释信息已经刷新正确
+ - [x] **文档**：是否涉及官网文档修改，如果涉及请及时提交资料到Doc仓

----

**验证结果**

远端验证环境已完成以下回归，构建均复用统一三方件缓存，mock 构建使用高并行度完成。

| 验证项 | 结果 |
|------|------|
| URMA mock CMake 完整构建 | PASS |
| native/mock-off CMake 完整构建 | PASS |
| native/mock-off 产物符号检查 | PASS，未发现 `urma_mock` artifact 或 `ds_urma_mock` 符号残留 |
| `UrmaGetDataPlaneResultTest.*` | 3/3 PASS |
| URMA/RDMA/kv_client_urma 相关 ctest | 160/160 PASS |
| `UrmaObjectClientTestEventMode.*` disabled 用例显式开启运行 | 3/3 PASS |

重点覆盖：

- URMA mock same-process/cross-process read/write。
- memfd + UDS fd transfer。
- fork 后 owner/listener 生命周期刷新。
- CQE error、async event、wait timeout、queue full、handshake failure 注入。
- Object/KV URMA ST，包含 fallback、disable fallback、failover、worker reconnect、fallback limiter。
- URMA data-plane 成功率统计：业务成功但 payload 走 TCP fallback 时，不再误记为 URMA data-plane 成功。
- mock-off/native 构建隔离：默认构建不引入 mock backend。

**风险与说明**

- mock 只模拟本节点远端 DMA 语义，不覆盖真实 RNIC 性能、跨节点硬件拓扑和 SDK 实现细节。
- SDK mirror/opaque handle/部分 C ABI 命名保留 URMA SDK 风格，是 mock ABI 兼容边界需要，不按项目 C++ 命名风格强制改名。
- 后续若新增 URMA SDK API，需要同步补充 mock ABI mirror 和相应 UT/ST。
