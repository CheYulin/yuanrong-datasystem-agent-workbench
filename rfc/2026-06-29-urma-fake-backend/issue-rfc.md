# [RFC]：URMA Fake Backend 与本节点 URMA 语义验证能力

## 背景与目标描述

当前 Object/KV cache 的 URMA 数据面强依赖真实 URMA 设备。没有 RNIC 的开发机、CI 或普通 tiantiyun 沙箱难以稳定覆盖 URMA read/write、completion、fallback、failover 和错误注入路径，导致大量 URMA ST 只能 disabled 或依赖人工硬件回归。

本 RFC 建议引入 URMA fake backend：在同一节点内用共享内存和 UDS 模拟远端 DMA 行为，业务仍走 URMA 代码路径，但底层由 fake ABI 替代真实 SDK。

### 目标

1. 在无 RNIC 环境运行 URMA 相关 UT/ST。
2. 覆盖 Object/KV URMA remote get/set/batch get、CQE error、async event、fallback、failover、reconnect 等路径。
3. fake 行为尽量收敛在 dlopen/ABI/fake backend 和测试边界，避免修改业务逻辑。
4. 保留真实 URMA 的关键语义约束，明确 fake 只验证语义，不验证真实性能。

### 非目标

- 不支持跨机器 fake RDMA。
- 不替换真实 URMA SDK 和生产部署。
- 不用 fake 结果推导真实 RNIC 性能。
- 不为了 fake 改 Object/KV 正常业务语义。

## 建议的方案

### Layer 1：ABI 与 dlopen 接入

- `BUILD_WITH_URMA_FAKE` 下让 URMA dlopen 返回 fake sentinel handle。
- `dlsym("urma_*")` 通过 `FAKE_ENTRY` 映射到 `ds_urma_fake_*`。
- fake ABI 保持 C 接口形态，避免上层直接依赖 C++ fake 对象。

### Layer 2：fake 资源模型

- `FakeUrmaBackend` 作为进程级单例管理 fake device/context/JFC/seg/jetty/tjetty。
- `SideTables` 管理 raw SDK handle 到 fake 对象的映射。
- `FakeJfc` 使用 eventfd + completion queue 模拟 poll/wait/ack。
- `FakeThreadPool` 模拟异步 PostSendWr，queue full 返回 `URMA_E_AGAIN`。

### Layer 3：共享内存 + UDS 数据面

- 发送端 `RegisterSeg` 优先复用业务 memfd，找不到时才走 POSIX shm fallback。
- 接收端通过 `ds_urma_fake_exchange_jfr_info` 注册 token -> endpoint。
- `ImportSeg` 通过 UDS `SOCK_SEQPACKET` 和 `SCM_RIGHTS` 获取对端 memfd fd，并 mmap 到本进程。
- payload 不通过 socket 传输，数据写入共享物理页。

### Layer 4：错误注入与 fallback 验证

- 支持 CQE status、wait timeout、async event、queue full、handshake 等注入。
- Object/KV 业务根据 URMA failure 决定是否走 TCP fallback。
- fallback limiter、disable fallback、worker restart reconnect 等应在 ST 中可重复验证。

## 涉及到的变更

### 新增/主要模块

- `src/datasystem/common/urma_fake/*`
- fake CMake/Bazel 目标
- fake UT：ABI、UDS、memfd、thread pool、inject、write payload
- Object/KV URMA ST 补充和 disabled case 打开

### 少量业务边界

- dlopen/URMA dispatch 层接入 fake entry。
- fast transport 边界提供 fake `UrmaWritePayload`。
- P0 hook `ds_urma_fake_exchange_jfr_info` 用于接收端注册 import endpoint。
- fallback pre-request 标记用于区分 UB buffer 预申请失败后的 TCP fallback。

### 不变项

- 对外 Client API 不变。
- 真实 URMA 模式行为不变。
- Object/KV 业务语义不因为 fake 改变。

## 测试验证

### UT

- `ds_ut --gtest_filter="*Urma*:*URMA*:*urma*"` 覆盖 URMA fake backend 与相关 URMA 单测。
- 建议新增 ABI 时同步补 UT。

### ST

- `ds_st_object_cache --gtest_filter="*Urma*:*URMA*:*urma*" --gtest_also_run_disabled_tests`
- `ds_st_kv_cache --gtest_filter="*Urma*:*URMA*:*urma*" --gtest_also_run_disabled_tests`

当前 PR1129 远端验证快照：

| 项 | 结果 |
|----|------|
| URMA UT sweep | 75/75 PASS |
| Object URMA ST sweep | 68/68 PASS |
| KV URMA ST sweep | 11/11 PASS |

## 遗留事项

1. Codecheck 中 SDK/C ABI mirror 命名与项目 C++ 命名规范冲突，需要保留原因说明。
2. fake `USE_URMA_FAKE` 宏出现位置需要持续 review，避免扩散到业务逻辑。
3. 长期运行下 fake JFC completion 队列、UDS fd 生命周期、fork 后 endpoint 清理仍需压测观察。
4. 新增 fallback case 需要证明语义来自 URMA failure，而非 fake 专属逻辑。

## 期望的反馈时间

建议反馈周期：5～7 天。

重点反馈：

1. fake ABI/C SDK mirror 的 codecheck 豁免策略是否认可；
2. `USE_URMA_FAKE` 允许出现的边界是否足够清晰；
3. URMA ST/UT sweep 是否可作为后续 PR 的最小门禁；
4. 是否需要把 developer guide 整理为仓内正式文档。
