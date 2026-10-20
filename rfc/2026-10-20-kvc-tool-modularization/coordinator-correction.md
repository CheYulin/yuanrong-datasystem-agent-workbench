# 1. 最终对比切换 DataSystem Coordinator

2026-10-21 对 runtime004 的实际启动参数复核发现，它仍使用 ETCD，Worker 的 coordinator_address 为空。该轮正式矩阵执行、停止和恢复已结束（外层 returncode=0，5056.379312235 秒；W09 passed_with_findings，W10/W11 passed），但不满足用户后续统一使用 Coordinator 的要求，因此作为旧后端基线保留，不能冒充最终 Coordinator 对比。

## 2. 构建物与来源

同一个固定 v0.9.2.rc44 / 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 的源码构建，已经产生 output/datasystem/service/datasystem_coordinator 与 coordinator_config.json。原目录不改，原 metadata/回执保留。新增 Coordinator 二进制纳入明确构建产物：新验证必须同时证明原输出不变，以及该额外二进制与原始已绑定 tar.gz 内的相应成员逐字节、size/mode 匹配；另生成新的 catalog/验证记录，明确未重新编译。不能将原始失败验收改为成功。

## 3. 运行与公平口径

新运行根目录启动一个单节点、无选举的 Coordinator 与两个 KVC Workers，全部在同一个 server cgroup 内计费；两 Worker 的 etcd_address 为空，coordinator_address 指向实际 Coordinator。保留单 Coordinator 控制面成本，不把它挪到监控域或排除 CPU/内存/IO。关闭 Coordinator 可选 metrics/日志采样；线程数与显式同节点 CPU 预算对应，具体实际 argv 全部记录。原生 RocksDB 数据写入与 Stream sockets 继续关闭，Worker 保持独立状态目录。其余产品、版本、客户端/服务端 cgroup、工作负载和重复规则不变。

Coordinator 日志、argv、PID/start_ticks、二进制 SHA、独立状态目录均属于运行/观测证据；清理沿用精确进程身份保护。单节点无选举是本性能配置，不据此声称 HA/故障恢复验证通过。

## 4. 必需验收与新测量

资格验收同时检查两个 Worker 的实际参数、一个存活 Coordinator 进程身份、同 server cgroup，以及没有由本次启动的 ETCD。W14 独立核对全部 DataSystem service epoch 的启动证据和新增二进制来源，禁止只有标称配置没有实际观测。先真实 gate/smoke，再在全新根目录重新执行完整三产品三轮矩阵；不覆盖、续写或拼接 ETCD 基线数据来伪造 Coordinator 完整矩阵。最终报告与文档明确列出元数据后端、实际 tag/SHA、构建/重验/执行源码身份和关闭项。

## 5. 失败与回滚

任何构建来源、Coordinator 启动、Worker 注册、cgroup、资格或观测条件不成立时，保留新 attempt、执行停止/排空/环境恢复，并阻断正式测量。原 ETCD 基线与其冻结源码不变；回滚仅选择历史基线查看，不把它重新标为本次最终结果。
