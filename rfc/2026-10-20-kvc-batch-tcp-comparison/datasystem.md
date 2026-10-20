# DataSystem rc44 原生批量接口与 TCP 证据

## 1. 固定版本与审查范围

产品固定为 **v0.9.2.rc44**、commit **`2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6`**，复用此前源码构建及其制品校验和。本文产品源码锚点均指向这一 revision，不以当前开发分支替代。构建来源见 [build.md](build.md)。本轮不修改产品代码。

下面分别记录原生接口能力、已采集 r2 正确性/通信证据，以及 kvtest 的设计参考。r2 门禁结果不等于 r3 门禁或正式性能结果；r3 容量配置及后续状态以 [verification.md](verification.md) 为准。

## 2. 原生接口与批次上限

| 统一操作 | 一次公开 Python 调用 | 本实验约束与返回 |
|---|---|---|
| fresh PUT / UPSERT | `KVClient.mset(keys, values, write_mode=NONE_L2_CACHE, ttl_second=0)` | 默认 `ExistenceOpt.NONE`；允许插入和更新。成功时返回失败 key 列表，列表必须为空 |
| GET | `KVClient.get(keys, convert_to_str=False, sub_timeout_ms=0)` | 返回按输入位置排列的 Python 自有 `bytes`；缺项、空项或长度不符均使整批失败 |
| EXIST | `KVClient.exist(keys)` | 按输入位置排列的布尔向量；RPC 错误不能当作 `false` |

Python 包装层各调用一次 native 入口：[mset，473–511 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/python/yr/datasystem/kv_client.py#L473-L511)、[get，575–603 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/python/yr/datasystem/kv_client.py#L575-L603)、[exist，689–718 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/python/yr/datasystem/kv_client.py#L689-L718)。对应 pybind 分别调用一次 C++ `MSet`、`Get`、`Exist`，并非 Python 单 key 请求循环；GET 在绑定层加读锁并复制成 Python bytes。[pybind MSet，310–332 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/pybind_api/pybind_register_kv.cpp#L310-L332) · [GET，459–498 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/pybind_api/pybind_register_kv.cpp#L459-L498) · [EXIST，555–563 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/pybind_api/pybind_register_kv.cpp#L555-L563)。

| 限制 | rc44 实现与适配器策略 |
|---|---|
| GET key 数 | 原生入口限制不超过 10,000；适配器使用同一上限 |
| MSet key 数 | routed 输入入口未实现 Python docstring 所写的 2,000 限制；每 Worker 的批请求校验不超过 10,000。适配器保守限制整次调用不超过 10,000 |
| EXIST key 数 | 原生入口限制不超过 100,000；不是 Python docstring 所写的 10,000 |
| 本轮 payload | 每 key 固定 1,024 bytes，B=1/16/256/4096，即每批最大 4 MiB；未据此证明其它对象尺寸或无限批次可用 |
| 重复 key | 适配器拒绝重复 key，保证请求 B 等于不同 key 数，避免原生去重改变比较口径 |

限制证据：[validator 常量，43–44 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/common/util/validator.h#L43-L44)、[上限校验，1054–1061 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/common/util/validator.h#L1054-L1061)、[GET 入口，3754–3796 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/object_client_impl.cpp#L3754-L3796)、[EXIST 入口，4909–4950 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/object_client_impl.cpp#L4909-L4950)、[MSet routed 输入校验，807–839 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/routed_mode.cpp#L807-L839)、[批请求校验，34–63 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/rpc/mset_request_builder.cpp#L34-L63)。Python mset docstring 另写有单值小于 500 KiB 的描述；本文未用它推断实现上限，本轮仅验证 1 KiB。

## 3. 完成、部分失败与原生内部分块

**MSet 是非事务 upsert。** 默认 `ExistenceOpt.NONE` 允许锁定已有对象后更新；只有 NX 分支禁止覆盖。fresh PUT 使用全新 namespace，UPSERT 使用已存在 key 和不同版本的真实 payload；不能将返回成功但未重写的其它产品 insert 语义算作更新吞吐。[Worker 已有对象锁定，911–938 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/worker/object_cache/service/worker_oc_service_multi_publish_impl.cpp#L911-L938)。

关闭 local cache 后，MSet 进入 transport 路径，按 Worker 分组，分配/复制 payload，再同步调用原生 MSet。TCP 路径构造 `MultiPublishReqPb` 并等待 `InvokeMultiSet` 返回。[入口，4132–4155 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/object_client_impl.cpp#L4132-L4155) · [路由分组与传输，884–960 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/routed_mode.cpp#L884-L960) · [TCP MSet，196–221 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/data_plane/tcp_transporter.cpp#L196-L221)。这提供内存发布完成边界，不是持久化、事务或故障恢复承诺。

部分 key 成功时，整体 status 可以是 OK，但 `failed_keys` 非空；已经成功的 key 不会因整批报错而自动撤销。适配器检查失败列表并抛出包含失败 key 的 `BatchWriteError`，不追加删除、单 key 回退或适配器重试。原生内部已有条件受限的重路由/重试，这部分成本仍在 API 时间内。[routed 部分结果，1016–1086 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/routed_mode.cpp#L1016-L1086) · [响应失败列表，192–215 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/rpc/mset_request_builder.cpp#L192-L215)。废弃 `msettx` 在该 Python 包装中直接报错，不作为事务替代。[513–543 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/python/yr/datasystem/kv_client.py#L513-L543)。

**GET 的一次公开调用不等于一次 RPC。** 原生读路径先按 Worker 聚合，每个 Worker 的 chunk 上限为 **1,024 对象或预计 100 MiB**；同 Worker 的 chunks 顺序处理，不同 Worker 的任务可并行。B4096 因此会在内部拆分，但实际元数据、路由和重试会影响 RPC 数，不能写成“恰好 4 次网络请求”。[常量，48–49 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/object_read/replica_reader.cpp#L48-L49) · [分块与执行，494–552 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/object_read/replica_reader.cpp#L494-L552)。TCP transporter 的单元素 batch 还可在 C++ 内部走单对象优化；适配器对 B1 仍调用相同的公开 batch API。[TCP BatchGet，61–122 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/transport/data_plane/tcp_transporter.cpp#L61-L122)。

混合成功的 GET 可返回含 `None` 的结果；不能把整体 OK 当作每项命中。适配器要求整批长度、每项 bytes 类型和长度均正确。EXIST 按原始索引恢复布尔顺序，查询错误继续抛出；缺失 key 则保留对应 `false` 位置。[GET 部分结果，706–725 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/routed_mode.cpp#L706-L725) · [EXIST 顺序，120–134 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/exist_handler.cpp#L120-L134) · [EXIST 查询与错误，371–456 行](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/client/object_cache/exist_handler.cpp#L371-L456)。

## 4. TCP 选择与 r2 实际 payload 证据

客户端复用已固定初始化：`enable_local_cache=False`、`enable_cross_node_connection=False`、`REQUIRED_SAME_NODE`；两个 `client_index` 分别选择两个初始连接端点。placement 仍可在同节点 Workers 间按 key 路由，初始连接地址不等于全部对象的唯一存放位置。Worker 显式关闭 `ipc_through_shared_memory`、RDMA、URMA。该 IPC 能力由 Worker 初始化/注册路径告知客户端：[worker_oc_server.cpp:134](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/worker/object_cache/worker_oc_server.cpp#L134)、[worker_service_impl.cpp:491](https://gitcode.com/openeuler/yuanrong-datasystem/blob/2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6/src/datasystem/worker/worker_service_impl.cpp#L491)。Worker 内部数据池仍可使用共享内存分配；这不代表客户端通过 SHM 传输 payload。

r2 保存的 `tcp-configuration.json` 将 Worker flags、进程参数/PID 启动 tick 与对应原件 SHA 关联。另对 gate 客户端使用 `strace -ff -ttt -yy -xx -s2048`，仅在正确性阶段采集。离线验证将 `put_initial` / `get_full` 的实际步骤时窗、成功 syscall 返回值、TCP fd 的真实 Worker endpoint 与同批确定性 payload 绑定，不依据不存在的 Python access-log `transportType` 字段推断传输介质。

| r2 审计文件 | 实际结果与解释 |
|---|---|
| `datasystem-batch-tcp-verification.json` | 旧规则要求 key0 的 32-byte prefix；12/16 通过。4 个 B4096 GET 均达到至少 4 MiB TCP 接收量，但 key0 prefix 未出现在所截取文本，原始未通过记录保留 |
| `datasystem-batch-tcp-verification-any-key.json` | 新的独立离线规则 16/16 通过，即 2 个初始端点 × insert/upsert 两种 gate 模式 × 4 个 B；每例 PUT/GET 两个方向，共 32 项方向证据。解析耗时 15.667086592 秒，不是产品性能窗口 |

新规则同时要求：该操作方向成功 TCP syscall 字节总量至少为 `B×1024`（总量包含协议开销），并在同 case、version=0、同操作时窗内找到**至少一个 key 的完整 1,024-byte 真实 payload**。完整 payload 必须落在 syscall 实际成功返回范围内；只出现 32-byte prefix、其它 namespace、其它方向或 UNIX fd 均不通过。结果保留 sample key/index/version、payload SHA、输入文件与行 SHA、segment byte offset、时间和 TCP endpoint。原始 trace 与输入 SHA 在解析前后核对。

新派生文件 SHA256 为 `0fa183e20cec830a0b1b0b54378786379864ad28e65b130c97a5ffcb49e84a12`。完整 payload 的存在和方向总量**不证明整批每个 payload 的全部字节都在 trace 文本中可见**，也不证明 RPC 数、所有 Worker 间传输或正式压测全程逐请求都被跟踪。旧 key0 未观测不能直接归为产品错误，也不能把截断/顺序的合理解释写成已确认根因。r3 必须以其独立采集证据重新验收。

## 5. 适配器与正确性门禁

`batch_datasystem.py` 对四档 B 均调用一次公开 native batch 入口，不自行拆批。输入唯一性、固定 bytes 长度和返回向量检查计入 API 延迟；这部分 O(B) 开销由 metadata 披露，与其它产品保持同一边界。配置字段 `write_mode=insert` 用于统一 fresh/重复写门禁分类；DataSystem 的实际原生写语义始终为 upsert，不伪装成 NX。

`batch_gate.py` 在每个端点/模式/批次中使用按 key 和版本派生的不同 bytes，完整验证首次写读、逆序和子集 GET、EXIST 全命中/稀疏混合/全缺失向量，以及重复写后的新值。SDK/adapter close 失败也使门禁失败，不通过重试消除错误。11 项适配器 fixture 与 20 项 TCP 解析 fixture 是本地逻辑回归；真实调用正确性由 gate 原件证明，不能把 fixture 数量当成产品请求或性能结果。

## 6. kvtest 中可参考的原生批量测法

本地 kvtest 参考版本是 **`c0bbb1582e49ecc2d2d925836d1288633d322012`**，相关工具文件无本地修改。它与上述产品 rc44 revision 分开记录；本轮未构建或运行原生 C++ kvtest，也未将其吞吐并入 Python 产品比较。

| kvtest 能力 | 固定工具源码锚点 | 对本实验的意义 |
|---|---|---|
| 原生批量参数 | `tests/kvtest/src/common/config.h:90–92`、`config.cpp:220–225,571–581` | `mset_batch_size`、`mget_batch_size` 默认为 8；另有 Pipeline 的 `batch_keys_count`。可以配置四档 B，但仍受产品上限约束 |
| MSet 批量 benchmark | `tests/kvtest/src/benchmark/benchmark_runner.h:260–281`、`kv_client_adapter.h:114–125` | 每批真实调用一次 C++ `MSet`，要求 status OK 且失败列表为空；最后不足 B 的批次会缩短 |
| MGet 批量 benchmark | `benchmark_runner.h:285–306`、`kv_client_adapter.h:128–136` | 每批真实调用一次 C++ `Get`；只检查 status/可选 buffer，不进行本实验要求的逐 key Python bytes 物化和完整内容比对 |
| Pipeline 批量 buffer 路径 | `tests/kvtest/src/pipeline/pipeline.cpp:149–211` | 可调用 `MCreate → MSet(buffers) → Get(keys)`，并按配置核对内容；它与本轮 `mset(keys, bytes)` 的计费范围不同 |
| 已有同步单接口引擎 | `tests/kvtest/src/benchmark/interface_benchmark.cpp:574–578` | 该 revision 仅纳入单 key SET/GET 模式，不能宣称原生 batch 自动继承其全部同步测量机制 |

参考 [kvtest 批量模式说明](https://gitcode.com/openeuler/yuanrong-datasystem/blob/c0bbb1582e49ecc2d2d925836d1288633d322012/tests/kvtest/docs/benchmark-guide.md#L187-L202) 和 [参数/单位说明](https://gitcode.com/openeuler/yuanrong-datasystem/blob/c0bbb1582e49ecc2d2d925836d1288633d322012/tests/kvtest/docs/benchmark-guide.md#L243-L246)：其延迟样本按 batch 计，QPS 按 key 数计。本轮同样区分 **batch/s、key/s、每批 P50/P99**，不把批延迟除以 B 后称为单 key 尾延迟。kvtest runner 的成功数累加 key 数，但失败数累加失败批次数；统一 harness 分开记录两种单位，不直接照搬该计数汇总。

kvtest `mget_*` 默认是写 child 与读 child 并发，`mset_*` 是纯写；本轮 GET/EXIST/UPSERT 使用预置后独立测量窗口，fresh PUT 使用固定新 key 总量。kvtest 默认 local cache 与发现策略也不能代替本轮显式 TCP 配置。其 benchmark adapter 的未初始化客户端分支会直接返回成功，故仅有该工具退出码不能证明真实服务路径。这里借鉴的是原生批入口、准备与测量分离和批次计量方式；跨产品排名始终来自本轮统一 harness 的真实结果。
