# 统一 TCP 测试验证结果

Status: In-Progress，尚未执行 TCP 正式矩阵，不能将原生基线数字作为 TCP 结果。

## 1. 已完成的源码与环境准备

复用 DataSystem v0.9.2.rc44、Mooncake v0.3.13.post1、LMCache v0.5.6 的实际源码构建；新环境部署时重新核对 8 个原生制品摘要。DataSystem 安装 SDK 的 160 个非 dist-info wheel 成员逐个核对一致（40 个 Python 文件、33 个动态库，0 缺失、0 差异）。产品源码及二进制保持原状。

正式入口、目标节点和 cgroup 配额见 tcp-plan.md。原生路径完整矩阵与公开报告仍在 validation.md 对应地址，两个 profile 单独统计。

## 2. 第一轮协议门禁与日志限制

两个 DataSystem 端点的 1 MiB 完整字节探测与直接 TCP syscall payload 计数均通过。随后附加三尺寸检查因缺少 `transportType` 日志字段失败，尚未进入正式性能窗口。

固定版本 Python Set 从 `kv_client.py:353` 调用 `pybind_register_kv.cpp:276–291`，Get 从 `kv_client.py:595` 调用同文件 `459–498`。这些 pybind AccessRecorder 没有调用 C++ KVClient 中的 `.TrackedTransportType()`；不能把 C++ API 的日志字段假定为 Python API 的实际输出。安装文件与 wheel 一致，失败原因是诊断字段假设不成立。

原失败证据保留；修订门禁将逐尺寸、逐端点、逐 PUT/GET 时间窗口核对直接 TCP 字节及独特 payload 前缀，GET 另与实际 Materialize 日志、完整读回、Worker flags 绑定。缺失证据仍作为失败，不能改造产品或补造日志来满足门禁。

## 3. 待完成

完成三个产品的真实协议门禁、共同正确性 smoke、81 cell/216 结果正式矩阵、九格 PUT 资源专项及独立校验；随后以实际结果更新本文件、ECharts 报告、时序与资源分析，运行公开浏览器和下载完整性验收并恢复环境。
