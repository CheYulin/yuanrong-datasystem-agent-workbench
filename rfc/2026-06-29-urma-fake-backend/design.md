# URMA Fake Backend - 设计

**Status**: In-Progress  
**Branch**: `feat/urma-fake-r11-rebase`  
**Related**: [as-is-to-be-sequences.md](./as-is-to-be-sequences.md), [worktree-verify.md](./worktree-verify.md), [results.md](./results.md)

---

## 1. 背景与问题

Object/KV cache 的 URMA 数据面依赖真实 URMA SDK、RNIC、驱动和跨节点部署。缺少硬件时，普通 CI 和本地开发只能覆盖 TCP/UDS 普通路径，无法稳定验证：

- `UrmaManager`、`fast_transport_manager_wrapper` 的真实调用链；
- remote get/set、batch get、migrate fast transport 等 URMA 数据路径；
- CQE error、async event、wait timeout、queue full 等失败路径；
- URMA 失败后的 TCP fallback、limiter、reconnect、failover 行为；
- disabled URMA ST 用例打开后的语义。

URMA fake 的核心目标是把「跨机器 RNIC DMA」压缩为「同机多进程共享 memfd 物理页 + UDS 交换 fd/元信息」，让上层业务仍走 URMA 代码路径，但不需要真实 RNIC。

## 2. 设计原则

| 原则 | 含义 |
|------|------|
| fake 边界内聚 | fake 逻辑集中在 dlopen、`ds_urma_fake_*` ABI、`src/datasystem/common/urma_fake` 和测试适配 |
| 业务逻辑不 fake 化 | Object/KV/Worker 尽量继续判断 `USE_URMA`，少量 `USE_URMA_FAKE` 只能用于 fake 隔离点 |
| 语义优先于性能 | fake 只验证控制面/数据面语义，不作为真实 URMA 性能依据 |
| 同机共享内存模型 | 数据 payload 不走 TCP，UDS 仅交换 token、va、len 和 memfd fd |
| 失败可注入、可观测 | queue full、CQE status、wait timeout、async event、handshake 失败均能被 UT/ST 覆盖 |
| fallback 与 fake 解耦 | URMA 失败后的 TCP fallback 是业务语义，不应被 fake 特化或绕过 |

## 3. As-Is

### 3.1 真实 URMA 路径

```text
Object/KV request
  -> fast_transport_manager_wrapper / UrmaManager
      -> dlopen liburma / dlsym urma_*
      -> create context / JFC / Jetty / register seg
      -> RNIC DMA write/read remote memory
      -> poll/wait completion
```

真实路径依赖硬件和驱动，完成队列、Jetty、target segment、byte count 等语义由 SDK 和 RNIC 保证。

### 3.2 fake 前的验证缺口

| 缺口 | 影响 |
|------|------|
| 无 RNIC 环境不能运行 URMA ST | 大量 URMA Object/KV cases disabled 或只能手工跑 |
| 失败注入难稳定复现 | CQE/AE/wait timeout/reconnect 依赖硬件或时序 |
| fallback 语义缺少闭环 | URMA 失败后 TCP fallback、payload limiter、pre-request fallback 难持续回归 |
| 本地开发反馈慢 | 只能远端硬件排障，PR review 周期长 |

## 4. To-Be

### 4.1 目标调用链

```text
Object/KV request
  -> fast_transport_manager_wrapper / UrmaManager
      -> dlopen fake sentinel handle
      -> FAKE_ENTRY: urma_* -> ds_urma_fake_*
      -> FakeUrmaBackend singleton
          -> FakeContext / FakeJfc / FakeSeg / FakeJetty / FakeTjetty
          -> memfd resolver + UDS SCM_RIGHTS fd transfer
          -> memcpy into shared mapped page
          -> push fake completion record
```

上层仍通过 URMA API 形态调用；fake 替换的是 SDK ABI 层和后端资源模型。

### 4.2 核心组件

| 组件 | 职责 |
|------|------|
| dlopen fake sentinel | `BUILD_WITH_URMA_FAKE` 下让 `dlsym` 走 fake entry 表 |
| `ds_urma_fake_*` ABI | 模拟 URMA SDK C ABI，避免上层直接依赖 C++ fake 对象 |
| `FakeUrmaBackend` | 进程级单例，管理 fake device/context/JFC/seg/jetty/tjetty |
| `SideTables` | 用 raw handle 关联 shared fake 对象，隔离 C ABI 与 C++ 生命周期 |
| `MemfdResolver` | 从业务 `memfd_create("datasystem")` 映射反查 backing fd |
| UDS transport | `SOCK_SEQPACKET` + `SCM_RIGHTS` 传递 memfd fd 和 import 元信息 |
| `FakeThreadPool` | 异步执行 PostSendWr lambda，queue full 返回 `URMA_E_AGAIN` |
| inject helpers | 注入 CQE status、wait timeout、async event、handshake 等失败 |

### 4.3 数据面模型

fake 模式下存在三层对象：

| 层次 | 真实内容 | 归属 |
|------|----------|------|
| 业务层 | 业务 mmap 出来的 memfd 虚拟地址 | Object/KV/Worker |
| fake 层 | `FakeSeg`、token、wire va、size、remote host/port | `FakeUrmaBackend` |
| 内核层 | memfd inode 对应的共享物理页 | Linux kernel |

发送端 `RegisterSeg` 记录本地 memfd，接收端 `ImportSeg` 通过 UDS 拿到对端 fd 后 mmap 同一 inode。后续 fake write 本质是向共享物理页写入，接收端映射立即可见。

### 4.4 UDS import endpoint

接收端通过 `ds_urma_fake_exchange_jfr_info` 注册 import endpoint：

| 字段 | 用途 |
|------|------|
| token | segment/jfr import 的关联键 |
| `host:port` | 优先路由，来自 wire 信息，跨进程更明确 |
| `instanceId` | 兼容旧路径和 UT fallback |
| clientId | fake 不用于权限判断，仅保留业务连接标识 |

当存在注册 endpoint 时，`ImportSeg` 必须走 UDS 路径；UDS 失败不应静默降级到本地 POSIX shm，以免误把跨进程失败伪装成成功。

### 4.5 失败与 fallback 模型

| 场景 | fake 行为 | 业务期望 |
|------|-----------|----------|
| fake queue full | `PostSendWr` 返回 `URMA_E_AGAIN` | 触发 pre-request fallback / TCP fallback |
| CQE status 注入 | fake completion 带错误 status | waiter 返回 URMA operation failed，相关 fallback case 可断言 |
| wait timeout 注入 | `WaitJfc`/waiter 返回 timeout | disable fallback 场景返回 URMA wait timeout |
| async event 注入 | 模拟 JFS/JFC/Jetty async event | 触发 reconnect/recreate 或 error path |
| payload 超限 | fallback limiter 拒绝 >= 1 MiB payload | 证明 fallback 语义与 fake 本身解耦 |

### 4.6 与真实 URMA 的差异

| 维度 | 真实 URMA | fake |
|------|-----------|------|
| 物理数据通路 | RNIC DMA / PCIe / network | 同机 memfd 共享物理页 |
| 跨机能力 | 支持 | 不支持 |
| CPU 消耗 | 主要由 RNIC offload | fake memcpy 消耗 sender CPU |
| 性能解释 | 可用于性能评估 | 只验证语义，不评估真实性能 |
| JFC 深度 | 硬件/SDK 约束 | fake 队列需业务及时 poll/ack，长期堆积有 OOM 风险 |
| 生产使用 | 是 | 否，仅 CI/本地/沙箱 |

2 GiB 限制不是 fake 强加，而是为了贴合真实 completion byte count / signed 32-bit 语义。

## 5. 模块边界

| 范围 | 允许内容 |
|------|----------|
| `src/datasystem/common/urma_fake` | fake backend、ABI、UDS、memfd、thread pool、inject |
| dlopen/URMA dispatch | fake entry table 和 sentinel handle |
| 测试 | fake UT、URMA ST、failure injection cases |
| 少量业务边界 | P0 exchange hook、pre-request fallback 标记、fake UDS/imported-seg 重建隔离 |

不应把 fake 细节扩散到 Object/KV 正常业务逻辑。新增业务判断优先问：真实 URMA 是否也需要该语义？如果是，用 `USE_URMA`；如果只有 fake backend 加载/导入/测试隔离需要，才使用 `USE_URMA_FAKE`。

## 6. 测试矩阵

| 层级 | 覆盖 |
|------|------|
| fake UT | ABI dispatch、memfd resolver、UDS transport、thread pool、inject、write payload |
| Object ST | remote get/set、batch get、event mode、worker disconnect、CQE error、fallback、disable fallback、heartbeat reconnect、NUMA affinity、eviction |
| KV ST | failover tracker、local/remote discovery、threshold/min sample、switch back |
| 迁移/fast transport | migrate direct、spill disabled、rate limit、fast transport retry |

## 7. 迁移与维护建议

1. 新 ABI 必须同时补 `ds_urma_fake_*` 声明、实现、dlopen entry 和 UT。
2. 新 fake 行为必须说明与真实 URMA 的语义对应关系。
3. 新 fallback case 应证明是 URMA 失败后的业务降级，而不是 fake 专属语义。
4. 新 codecheck 豁免优先记录原因：C ABI mirror、SDK 命名、opaque handle、address-to-pointer fake DMA 边界。
5. 每次关键变更至少跑 URMA UT sweep、Object URMA ST、KV URMA ST。

## 8. Non-goals

- 不用 fake 评估真实 URMA/RNIC 性能。
- 不支持跨机器 fake DMA。
- 不替换真实 URMA SDK 或生产部署。
- 不为了 fake 修改 Object/KV 正常业务语义。
- 不把 SDK/C ABI mirror 命名改成项目 C++ 命名风格。

## 9. 风险与开放问题

| 风险 | 说明 | 缓解 |
|------|------|------|
| JFC CR 堆积 | fake completion 队列不由硬件强约束 | ST/UT 保证 poll/ack，长期压测观察 RSS |
| UDS/fd 泄漏 | import 失败路径容易泄漏 fd/connection | RAII + targeted UDS tests + codecheck |
| fake 宏扩散 | review 时容易为了测试稳定改业务逻辑 | RFC 明确边界，review 按 `USE_URMA_FAKE` grep |
| 性能误读 | fake 可能比真实 URMA 快或慢 | PR/文档强调仅验证语义 |
| fallback 混淆 | URMA 失败后的 fallback 被误认为 fake 逻辑 | fallback cases 独立说明，并保持真实 URMA 语义 |
