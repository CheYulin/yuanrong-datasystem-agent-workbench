# [RFC] URMA mock backend for local and CI validation

关联 PR: https://gitcode.com/openeuler/yuanrong-datasystem/pull/1129

## 背景与问题

Object/KV cache 的 URMA 数据面依赖真实 URMA SDK、RNIC、驱动和跨节点部署。缺少 URMA 硬件时，普通开发机、CI 或沙箱环境只能稳定覆盖 TCP/UDS 普通路径，难以持续验证：

- `UrmaManager`、fast transport wrapper、dlopen/dlsym adapter 的 URMA 调用链；
- remote get/set、batch get、migrate fast transport 等 URMA 数据路径；
- register/import segment、Jetty/JFC、completion、async event 等资源语义；
- CQE error、wait timeout、queue full、handshake failure 等错误路径；
- URMA 失败后的 TCP fallback、disable fallback、failover、worker reconnect 和 limiter 语义。

本 RFC 建议引入 opt-in 的 URMA mock backend：在本节点内使用共享内存、memfd 和 UDS 模拟远端 DMA 行为，让业务仍走 URMA 代码路径，但底层由 mock ABI/backend 替代真实 SDK，从而在无 RNIC 环境中可重复运行 URMA 相关 UT/ST。

## 目标

1. 在无 URMA 硬件环境中运行 URMA 相关 UT/ST。
2. 覆盖 Object/KV URMA remote get/set/batch get、completion、错误注入、fallback、failover、reconnect 等路径。
3. mock 行为收敛在 dlopen/ABI/mock backend 和测试边界，避免修改 Object/KV 正常业务语义。
4. 保留真实 URMA 的关键语义约束，明确 mock 只验证语义正确性，不验证真实性能。
5. 默认/native 构建不编译、不链接 mock backend，不引入 mock artifact 或 `ds_urma_mock_*` 符号。

## 非目标

- 不支持跨机器 mock RDMA。
- 不替换真实 URMA SDK、真实 RNIC 回归或生产部署。
- 不用 mock 结果推导真实 URMA/RNIC 性能。
- 不为了 mock 改变 Object/KV 正常业务协议、wire format 或 fallback 语义。
- 不把 SDK/C ABI mirror 命名强行改成项目 C++ 命名风格。

## 如何使用

URMA mock 是显式 opt-in 能力。默认/native 构建不启用 mock backend。

### CMake mock 构建

```bash
DS_OPENSOURCE_DIR=<thirdparty-cache-dir> \
  bash build.sh -U on -X off -P off -J off -G off \
  -B <mock-build-dir> -o <mock-output-dir> -j 40 -t build
```

说明：

- `-U on` 开启 URMA/mock 相关构建入口。
- `<thirdparty-cache-dir>` 建议使用统一三方件缓存目录，避免每次验证重复下载和构建三方件。
- `<mock-build-dir>` 和 `<mock-output-dir>` 建议使用独立目录，避免和 native/mock-off 构建产物混用。

### Bazel mock 构建

```bash
bazel test --config=urma_mock //tests/ut/common/urma_mock:all
```

说明：

- `--config=urma_mock` 只在显式指定时启用 mock 相关 target。
- 普通 UT/ST 仍使用默认 gtest main；需要 ST entrypoint 的 target 通过 opt-in 参数选择 `//tests/st:test_main`。

### 运行指定 URMA 用例

```bash
cd <mock-build-dir>

./tests/ut/ds_ut \
  --gtest_filter="*Urma*:*URMA*:*urma*:*MockUrma*:*Uds*:*UDS*:*uds*"

./tests/st/ds_st_object_cache \
  --gtest_also_run_disabled_tests \
  --gtest_filter="*Urma*:*URMA*:*urma*"

./tests/st/ds_st_kv_cache \
  --gtest_also_run_disabled_tests \
  --gtest_filter="*Urma*:*URMA*:*urma*"
```

建议：

- 涉及 disabled URMA ST 时显式带 `--gtest_also_run_disabled_tests`。
- 新增 URMA SDK ABI mock 时，需要同时补 ABI dispatch、mock backend 实现和对应 UT。
- 新增 fallback case 时，应证明失败信号来自 URMA operation，而不是 mock 专属业务判断。

## 回归验证建议

### 1. mock 构建完整性

```bash
DS_OPENSOURCE_DIR=<thirdparty-cache-dir> \
  bash build.sh -U on -X off -P off -J off -G off \
  -B <mock-build-dir> -o <mock-output-dir> -j 40 -t build
```

目的：

- 验证 URMA mock backend、UT/ST binary、example 和相关 common/rdma 目标能完整构建。
- 捕获 CMake target、头文件依赖、符号导出和 mock ABI 签名不一致问题。

### 2. native/mock-off 构建隔离

```bash
DS_OPENSOURCE_DIR=<thirdparty-cache-dir> \
  bash build.sh -U off -M off -A off -X off -P off -J off -G off \
  -B <native-build-dir> -o <native-output-dir> -j 40 -t build

find <native-build-dir> <native-output-dir> \
  \( -name "*urma_mock*" -o -name "libcommon_urma_mock*" \)

nm -A <native-build-dir>/src/datasystem/common/rdma/libcommon_rdma.a \
  | grep ds_urma_mock
```

目的：

- 验证默认/native 构建不编译、不链接 mock backend。
- 确认 native 产物中没有 mock artifact 或 `ds_urma_mock_*` 符号残留。

### 3. URMA 相关 CTest sweep

```bash
cd <mock-build-dir>

ctest --output-on-failure \
  -R "Urma|URMA|Rdma|RDMA|kv_client_urma" -j 8
```

目的：

- 覆盖 mock backend UT、rdma wrapper、Object/KV URMA ST、migrate fast transport 和 KV failover。
- 捕获 URMA 数据面、fallback/failover、worker reconnect、async event 等路径的集成问题。

### 4. disabled event-mode ST 显式回归

```bash
cd <mock-build-dir>

./tests/st/ds_st_object_cache \
  --gtest_also_run_disabled_tests \
  --gtest_filter="UrmaObjectClientTestEventMode.*"
```

目的：

- 验证过去依赖真实 URMA 或人工硬件环境的 disabled event-mode URMA case 在 mock 环境中可运行。
- 避免 ctest 默认跳过 disabled case 导致覆盖面被误判。

### 5. focused debugging

```bash
cd <mock-build-dir>

./tests/ut/ds_ut --gtest_filter="<case>"
./tests/st/ds_st_object_cache --gtest_filter="<case>" --gtest_also_run_disabled_tests
./tests/st/ds_st_kv_cache --gtest_filter="<case>" --gtest_also_run_disabled_tests
```

建议按失败位置优先选择：

- mock ABI/UDS/memfd/thread pool 问题：先跑对应 `tests/ut/common/urma_mock` 用例。
- Object cache fallback/reconnect 问题：先跑 `ds_st_object_cache` 对应 `Urma*` case。
- KV data-plane failover 问题：先跑 `KVClientUrmaFailoverTest.*`。

## 源码修改目的说明

| 修改范围 | 修改目的 | 边界 |
|----------|----------|------|
| `src/datasystem/common/urma_mock` | 新增 mock backend、mock ABI、UDS、memfd、thread pool、inject 和 mock object 生命周期管理 | 只在 mock 构建中使用，不作为生产 URMA SDK 替代 |
| common/rdma dlopen/adapter/bridge | 将 `urma_*` dlsym 分派到真实 SDK 或 `ds_urma_mock_*`，让业务仍按 URMA API 形态调用 | mock 分派必须由 opt-in 构建开关控制 |
| `fast_transport_manager_wrapper` | 在 mock 构建中提供本节点 mock `UrmaWritePayload`，模拟远端 DMA write/read 语义 | native URMA 路径继续走真实 SDK/transport |
| Object/KV client/worker 少量边界代码 | 保持已有 URMA fallback、fork 后 owner 生命周期和测试可观测性，不引入 mock 专属业务语义 | 能用 `USE_URMA` 表达的语义不应改成 `USE_URMA_MOCK` |
| CMake/Bazel 构建文件 | 增加 mock target、mock opt-in config 和 ST/UT 依赖，保证 CMake/Bazel 两套构建都能发现同一类问题 | 默认构建不应携带 mock artifact 或 mock 符号 |
| `tests/ut/common/urma_mock` | 覆盖 ABI dispatch、memfd resolver、UDS fd transfer、thread pool、inject、write payload 和资源生命周期 | UT 聚焦 mock backend 自身，不依赖 Object/KV 业务细节 |
| Object/KV URMA ST | 覆盖 remote get/set/batch get、fallback、disable fallback、failover、worker reconnect、event mode 和错误注入 | ST 证明业务语义，不把 mock 内部实现当断言对象 |
| 设计/验证文档 | 记录 mock 边界、使用方法、验证矩阵、风险和 codecheck/ABI 豁免理由 | 不记录本地路径、机器地址、token 或临时验证目录 |

## 方案概述

### 1. ABI 与 dlopen 接入

- `BUILD_WITH_URMA_MOCK` 下让 URMA dlopen 进入 mock dispatch。
- `dlsym("urma_*")` 通过 mock entry table 映射到 `ds_urma_mock_*`。
- mock ABI 保持 C 接口形态，上层仍按 URMA SDK API 形态调用，避免业务代码直接依赖 C++ mock 对象。

### 2. mock 资源模型

- `MockUrmaBackend` 作为进程级 backend，管理 mock device/context/JFC/seg/jetty/tjetty。
- side table 负责 raw SDK handle 到 mock object 的映射，隔离 C ABI 与 C++ 生命周期。
- mock JFC 使用 eventfd + completion queue 模拟 poll/wait/ack。
- mock thread pool 模拟异步 post send，queue full 返回 URMA failure 信号，供业务 fallback 路径验证。

### 3. 共享内存 + UDS 数据面

- 发送端 register seg 优先复用业务 memfd，找不到时才走 mock fallback 映射。
- 接收端通过 mock exchange hook 注册 import endpoint。
- import seg 通过 UDS `SOCK_SEQPACKET` 和 `SCM_RIGHTS` 获取对端 memfd fd，并 mmap 到本进程。
- payload 不通过 socket 传输，数据写入共享物理页，接收端映射立即可见。

### 4. 错误注入与 fallback 验证

- 支持 CQE status、wait timeout、async event、queue full、handshake failure 等注入。
- mock 负责稳定制造 URMA failure 信号；是否 fallback、如何限流、错误码如何暴露仍属于 Object/KV 业务语义。
- fallback case 需要证明语义来自 URMA failure，而不是 mock 专属逻辑。

## 模块边界

| 范围 | 允许内容 |
|------|----------|
| `src/datasystem/common/urma_mock` | mock backend、mock ABI、UDS、memfd、thread pool、inject、mock object 生命周期 |
| common/rdma dlopen/adapter | mock entry table、sentinel handle、native/mock dispatch 隔离 |
| 测试 | mock UT、URMA ST、错误注入和 fallback/failover cases |
| 少量业务边界 | 仅保留真实 URMA 也需要的 fallback/owner 生命周期/测试可观测性适配 |

`USE_URMA_MOCK` 应尽量只出现在 mock 加载、mock adapter、mock backend 和测试隔离点。业务主流程优先使用既有 `USE_URMA` 语义，避免 mock 专属判断扩散到 Object/KV 逻辑。

## 当前 PR1129 验证快照

PR1129 当前远端回归已覆盖：

| 验证项 | 结果 |
|------|------|
| URMA mock CMake 完整构建 | PASS |
| native/mock-off CMake 完整构建 | PASS |
| native/mock-off 产物符号检查 | PASS，未发现 mock artifact 或 `ds_urma_mock_*` 符号残留 |
| `UrmaGetDataPlaneResultTest.*` | 3/3 PASS |
| URMA/RDMA/kv_client_urma 相关 ctest | 160/160 PASS |
| disabled event-mode URMA Object ST 显式开启运行 | 3/3 PASS |

重点覆盖：

- URMA mock same-process/cross-process read/write。
- memfd + UDS fd transfer。
- fork 后 owner/listener 生命周期刷新。
- CQE error、async event、wait timeout、queue full、handshake failure 注入。
- Object/KV URMA ST，包含 fallback、disable fallback、failover、worker reconnect、fallback limiter。
- URMA data-plane 成功率统计：业务成功但 payload 走 TCP fallback 时，不误记为 URMA data-plane 成功。
- mock-off/native 构建隔离：默认构建不引入 mock backend。

## 设计与文档

- 主设计文档：PR1129 内 `docs/source_zh_cn/design_document/urma_mock_validation_20260628.md`
- RFC/workbench 设计材料：`rfc/2026-06-29-urma-fake-backend`
- PR 描述已同步说明 mock 边界、使用方式、验证结果、风险和 ABI/codecheck 豁免理由。

## 待 maintainer 评审点

1. 是否认可 `BUILD_WITH_URMA_MOCK` / `USE_URMA_MOCK` 作为 opt-in mock 隔离方式。
2. mock ABI 与真实 URMA ABI 的兼容边界是否足够清晰。
3. `USE_URMA_MOCK` 出现位置是否已经收敛到 dlopen/adapter/mock backend/测试边界。
4. UDS + memfd + `SCM_RIGHTS` 的本节点 mock DMA 语义是否满足当前 ST 目标。
5. URMA failure 后的 fallback/failover 用例是否应作为 mock 构建下的长期最小门禁。
6. SDK mirror、opaque handle、C ABI 命名等 codecheck 事项是否接受按 mock ABI 兼容边界豁免。

## 建议结论

建议将 PR1129 作为 URMA mock backend 第一阶段合入候选：先解决无 URMA 硬件环境下 URMA 相关 UT/ST 的可验证性，并在后续迭代中继续收敛 mock 接入点、补充开发者文档、扩展错误注入和长期稳定性验证。
