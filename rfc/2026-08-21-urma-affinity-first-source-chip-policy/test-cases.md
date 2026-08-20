# 用例与验收矩阵

## 1. 选择算法 UT

| ID | 策略/配置 | 输入 | 期望 |
|---|---|---|---|
| UT-S1 | RR，阈值 15 | 深度 `0:0`，连续选择 | `1,2,1,2`，RR sequence 相应推进 |
| UT-S2 | RR，阈值 15 | 深度差 `15` | 保留 RR 候选 |
| UT-S3 | RR，阈值 15 | 深度差 `16`，双方向 | 选择低 inflight 芯片 |
| UT-S4 | RR，阈值 0 | 深度 `100:0` | 仍保留 RR 候选 |
| UT-S5 | 亲和优先，阈值 15 | transmitted=2，深度 `0:0`，重复选择 | 始终为 2，RR sequence 不推进 |
| UT-S6 | 亲和优先，阈值 15 | transmitted=2，深度 `0:15` | 差值等于边界，仍为 2 |
| UT-S7 | 亲和优先，阈值 15 | transmitted=2，深度 `0:16` | 溢出到 1 |
| UT-S8 | 亲和优先，阈值 15 | transmitted=2，深度 `16:0` | 亲和芯片本来较空，仍为 2 |
| UT-S9 | 亲和优先，阈值 15 | transmitted=1，深度 `16:0` | 对称地溢出到 2 |
| UT-S10 | 亲和优先，阈值 0 | 任意深度失衡 | 始终为 transmitted chip |
| UT-S11 | 任意策略，RR type 0 | affinity enabled | 返回 transmitted chip，候选状态不推进 |
| UT-S12 | 每逻辑写粒度 | 首个 Post 选择后改变深度 | 后续 Post 复用首次选择 |
| UT-S13 | 每 Post 粒度 | 两次 Post 之间改变深度 | 第二次重新按策略和深度选择 |
| UT-S14 | 策略归一化 | `0`、`1`、`2`、`UINT32_MAX` | 0/1 保留；未知值回到 1 |

## 2. 配置与协议 UT

| ID | 场景 | 期望 |
|---|---|---|
| UT-C1 | proto 未设置字段 33 | `ub_numa_src_chip_policy()==0`，兼容旧 Worker |
| UT-C2 | proto 设置 1 | Client 可读取亲和优先策略 |
| UT-C3 | 同节点 SHM endpoint 但 Worker 具备 UB runtime | 策略仍随注册响应传播，早于 Arena 初始化应用 |
| UT-C4 | Worker flag 值 2 | validator 拒绝启动配置 |
| UT-C5 | Client 收到未知远端值 | 告警并归一化为 1 |

## 3. URMA Mock E2E 消融 ST

同一份 ST 源码编译为两个独立可执行文件，分别在独立进程运行 policy 0 和 policy 1，避免进程级
`call_once`、gflag 和环境变量互相污染。

公共拓扑与负载：

- 3 Worker、8 Client、每 Client 16 线程；
- 2 个 Client UB transport Arena，逐 Client 断言 `Init()` 成功；
- 每 Client 并发写 4 个 8 MiB key；
- 每个 key 提交十个独立读取任务；等全部 128 个 Client 线程进入线程池后统一放行，保证同 key 十读并发且跨 Worker；
- 开启 Worker-to-Worker Batch Get，额外读取 128 个 8 KiB 对象覆盖 GatherWrite；
- chip1 Mock 完成延迟 100 us、chip2 为 0；使用一次性复合决策注入原子地指定亲和候选和 `16:0` 深度快照；
- 校验所有读回 payload、两个芯片选择、深度覆盖、Gather counter 归零。

| ID | 策略 | 额外断言 |
|---|---|---|
| ST-E1 | `ROUND_ROBIN=0` | Client 和每个 Worker 都执行 RR 候选分支；亲和候选分支为 0；两芯片均被选择；失衡时覆盖 |
| ST-E2 | `AFFINITY_FIRST=1` | Client 和每个 Worker 都执行亲和候选分支；RR 候选分支为 0；阈值内保持强制的 chip1 亲和；`16:0` 时溢出 chip2 |

该 ST 证明 Client/Worker 配置传播、普通写与 GatherWrite 选芯、WR inflight 生命周期、多 Client 初始化和
业务读写正确性。Mock 的 memfd 重映射不能保留真实物理 NUMA 页，因此 NUMA 分配计划、Arena 等分、
页对齐和绑定失败分别由已有 UT 回归，不把注入结果表述为硬件亲和证明。

## 4. 相邻回归

| 范围 | 目的 |
|---|---|
| `UrmaChipInflightTest.*` | 选择、计数、Arena/NUMA helper |
| `ClientWorkerCommonApiTest.*Urma*` | 注册协议和 Client 初始化时序 |
| 两个独立 URMA Mock ST 可执行文件，各连续三轮 | 两策略端到端、进程隔离与稳定性 |
| `UrmaNumaAffinityTest.*` | PR 2081 多 Arena 分配、绑定计划和地址映射回归 |
| CMake/Bazel 源码闭包检查 | 新 proto、flag、测试目标在两种构建描述中一致 |

## 5. 硬件消融验收

| 维度 | 固定条件/指标 |
|---|---|
| 固定负载 | 8 MiB，8 Client × 16 线程，315 写 QPS，3150 读 QPS，同 key 十并发读 |
| 唯一变量 | `ub_numa_src_chip_policy=0/1`；RR type、阈值、Arena、Jetty、拓扑完全相同 |
| 深度 | 每 100 ms 的 chip1/chip2 inflight、差值、连续单边饥饿窗口 |
| 路径 | src/dst WR 矩阵、跨芯片比例、override rate、HCCS 单双向带宽、端口带宽 |
| 服务质量 | 吞吐、超时率、P50/P99/PMax；目标 P99 ≤ 1.5 ms、PMax ≤ 5 ms |

Mock 通过不是硬件消融通过；若本轮没有真实 URMA/HCCS 环境，PR 必须把硬件矩阵明确列为上线前证据缺口。

## 6. 本轮结果

| 范围 | 结果 |
|---|---|
| 策略、协议和 NUMA focused UT | 15/15 PASS |
| flag validator UT | 1/1 PASS |
| 相邻 NUMA URMA Mock ST | 2/2 PASS |
| affinity-first 与 Round Robin 独立 E2E | 2/2 PASS |
| focused 总计 | 20/20 PASS |
| 双策略各重复三轮 | 6/6 PASS |

真实 URMA/HCCS 硬件消融未执行，P99、PMax、HCCS 和端口带宽仍是上线前证据缺口。
