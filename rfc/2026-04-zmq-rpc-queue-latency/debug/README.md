# debug/

本目录记录 ZMQ RPC Queue Latency metrics 调试过程中的现象分析与根因假设。

## 文件列表

| 文件 | 内容 |
|------|------|
| `symptoms-and-hypotheses.md` | 0428 日志快照的现象汇总、7 个假设（A1-D1）的根因分析 |
| `root-cause-analysis-and-action-plan.md` | 代码级根因分析和修复计划（含 7 个根因 + 5 个修复步骤） |

## 快速参考：现象 → 假设

| 现象 | 最可能假设 |
|------|-----------|
| `zmq_client_stub_send_latency` 缺失 | A1: `RouteToUnixSocket` 跳过 CLIENT_SEND tick |
| `zmq_rpc_network_latency ≈ zmq_rpc_e2e_latency` | B1: `FindTickTs` 返回旧的 tick → SERVER_EXEC_NS≈0 |
| `zmq_server_exec_latency` 缺失 | C1: Tick 污染使条件不满足 |
| `zmq_server_reply_latency` max 异常大 | D1: Tick 污染导致极端差值 |

## 核心结论

**所有异常现象的根因都指向 `FindTickTs` 的"第一个匹配"语义与 `MetaPb` 对象可能被重用导致的 Tick 污染。**

`FindTickTs` 线性查找返回第一个匹配的 tick name。如果 `MetaPb` 在上一次 RPC 后没有 clear ticks，旧的 tick 会残留，新的 tick 追加到末尾。`FindTickTs` 找到的是旧的、更小的 ts，导致差值计算错误（接近 0 或极端大）。

## 修复计划（详见 `root-cause-analysis-and-action-plan.md`）

| 优先级 | 修复 | 目标 |
|--------|------|------|
| P0 | `RouteToUnixSocket` 添加 `CLIENT_SEND` tick | 消除 `CLIENT_STUB_SEND` n=0 |
| P0 | `FindTickTs` 改为反向遍历（返回最后一个匹配） | 消除 tick 污染的根因 |
| P1 | `RecordServerLatencyMetrics` 覆写而非 Add `SERVER_EXEC_NS` | 消除 tick 累积 |
| P2 | `GetTotalTicksTime` 增加防御性检查 | 保护 E2E 计算 |
| P3 | 远程构建验证 + VLOG 检查 | 端到端验证 |
