# 统一 TCP 测试验证结果

Status: In-Progress，尚未执行 TCP 正式矩阵，不能将原生基线数字作为 TCP 结果。

## 1. 已完成的源码与环境准备

复用 DataSystem v0.9.2.rc44、Mooncake v0.3.13.post1、LMCache v0.5.6 的实际源码构建；新环境部署时重新核对 8 个原生制品摘要。DataSystem 安装 SDK 的 160 个非 dist-info wheel 成员逐个核对一致（40 个 Python 文件、33 个动态库，0 缺失、0 差异）。产品源码及二进制保持原状。

正式入口、目标节点和 cgroup 配额见 tcp-plan.md。原生路径完整矩阵与公开报告仍在 validation.md 对应地址，两个 profile 单独统计。

## 2. 实际协议预检历史

各轮均属于正式性能测量之前的协议和编排预检。失败保持失败，不能从成功的局部步骤推断整个门禁完成。

| 尝试 | 实际观察与终止原因 | 处置与证据边界 |
| --- | --- | --- |
| 1 | DataSystem 两端点的 1 MiB 完整字节及 TCP syscall 探测通过；附加六组检查因 Python access 记录没有 `transportType` 字段失败 | 核对安装 SDK 与构建 wheel 一致；保留原失败，改用可观测证据，不修改产品添日志 |
| 2 | 六组 bytes 与 EXIST 检查均真；SDK 活着时等待 access 日志刷出超时；部分 socket 证据还误筛了客户端绑定端点 | 真实 trace 显示一次探测连续 149 次读取 access 文件均返回 0，退出后记录齐全；父进程改为 SDK 正常退出后严格校验，允许对象路由到配置内任一 Worker并记录实际载荷端点 |
| 3 | 修订的 DataSystem 六组完整探测通过；Mooncake launcher 部署模式为 `0644`，无法执行 | 定位为部署执行位问题，不能作为 Mooncake 接口或 TCP 性能失败；保留失败轮与启动日志 |
| 4 | DataSystem 六组再次通过；Mooncake 两个客户端的 1 MiB 完整字节与 TCP 探测通过，随后旧 venv console 的 `execv` 令 `argv[0]` 落到 TCP 根目录外，严格停止器拒绝清理 | 保留 PID/start ticks/exe/cgroup 防护；另行核对关联旧 master 的精确身份后安全 SIGTERM，恢复凭据存于 `formal-tcp-001/linked-master-recovery.json`；本轮整体仍失败 |
| 5 | 部署 `copy2` 再次把 launcher 权限带回 `0644`，后续执行受阻 | 修正部署顺序为复制后设置/核对执行位，增加权限变化审计及仅权限变化的受限重试；等待完整门禁补跑结果 |

截至本次更新，v3 DataSystem 三尺寸 × 两接入端点的完整探测已多次通过，但三产品整体协议门禁仍未完成，正式矩阵尚未开始。后续结果须由完整新一轮原始证据补充，不能把历史失败轮改判成功。

## 3. DataSystem 证据口径与已确认限制

固定版本 Python Set 从 `kv_client.py:353` 调用 `pybind_register_kv.cpp:276–291`，Get 从 `kv_client.py:595` 调用同文件 `459–498`。这些 pybind AccessRecorder 没有调用 C++ KVClient 中的 `.TrackedTransportType()`；不能把 C++ API 的日志字段假定为 Python API 的实际输出。安装文件与 wheel 一致，失败原因是诊断字段假设不成立。

修订门禁已逐尺寸、逐接入端点、逐 PUT/GET 时间窗口核对直接 TCP 字节及独特 payload 前缀，GET 另与同 key、同尺寸的实际 Materialize 日志、完整读回、Worker flags 绑定。SDK 进程退出后才由父进程读取日志；日志缺失、错误码、字节不足、前缀或实际物化证据不符仍使门禁失败。进程前后共享可写映射与 UNIX socket FD 快照保留，用作本次探测的补充证据，不扩大为所有时刻、所有路径的通用证明。

接入端点不等于路由后的存储端点。第二轮真实日志中，绑定 `127.0.0.1:31531` 的 1 KiB 探测实际在 `127.0.0.1:31532` 完成 PUT/GET；两者均在同一目标节点。修订解析只接受配置内的两个 Worker TCP 地址，单独记录真实携带载荷前缀的 socket 端点。不能用客户端 index 宣称该数据一定存储在对应 index 的 Worker。

第二轮隔离副本的六组原始日志/trace 已重新验证解析，210 个文件摘要与原结果记录一致；这个回归只证明工具能解析真实证据，原 child 退出码和 gate 失败状态均保持原样。后续 v3 完整新探测的通过状态与这一回归分开记录。

## 4. 归档、清理与过程耗时

失败轮保存在目标新实验根目录下 `formal-tcp-001/protocol-history/attempt-NNNN`，修订前后 helper 快照、操作者脚本快照与变更审计位于 `preflight-history`、`preflight-changes.json`。受限事务要求正式测量尚未准备/开始、suite lock 独占且 cgroup 排空；原 stage JSON 及其摘要、产品二进制、adapter、benchmark 和工作负载配置保持不变。第五轮权限修复属于部署模式修正，也应在审计链中保留，不能以内容 SHA 未变为由省略。

失败、停止器拒绝和人工恢复均保留真实退出状态。第四轮的关联 master 不符合本轮严格停止器的入口身份，因此没有放宽规则或按名字批量结束进程；主任务另核验精确 PID、start ticks、exe 与 cgroup 后进行 SIGTERM，并将凭据保存到上表所列恢复 JSON。

用户要求的操作耗时覆盖所有预检尝试及恢复，而非只保留成功步骤。每轮 `summary.json` 和 `operations.jsonl`、外层操作 ledger 保存 UTC 边界、monotonic 墙钟及返回状态；重试和归档有各自区间。它们属于工程/协议预检时间，单列在过程时间线，不进入正式 SDK 性能窗口或产品编译时间，嵌套父子耗时也不重复相加。当前仍在重试的轮次尚无最终耗时结论，完整计时表待对应原始记录闭合后更新；不按日志首尾推造未知阶段耗时。

## 5. 待完成

完成三个产品的真实协议门禁、共同正确性 smoke、81 cell/216 结果正式矩阵、九格 PUT 资源专项及独立校验；随后以实际结果更新本文件、ECharts 报告、时序与资源分析，运行公开浏览器和下载完整性验收并恢复环境。

## 6. 第六轮三产品协议门禁通过

`protocol-gate-001/summary.json` 的 `valid=true`，UTC 2026-10-19 20:38:33.764941714 至 20:39:21.148347710，同进程 monotonic 耗时 47.383406466s。DataSystem、Mooncake、LMCache 各两客户端均完成 fresh PUT、GET 完整字节及 EXIST hit/miss；逐客户端直接 TCP syscall 成功字节均超过 8 MiB 门槛，DataSystem 附加六组逐接口实际传输证据通过。结束时四个实验 cgroup 均为空。

前五轮保持原始 `valid=false`，完整保存在 `formal-tcp-001/protocol-history/attempt-0001` 至 `attempt-0005`。第五次修订只修正 staged launcher 的执行位：`changes=[]`，源码 SHA 不变；权限在全部复制后生效，原始 stage 文件未改写。本门禁含 strace，仅验证正确性和协议，不提供 QPS/P99 成绩。共同编排 smoke 与正式矩阵仍须独立通过。
