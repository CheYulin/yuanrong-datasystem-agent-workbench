# 详细设计：URMA 源芯片亲和优先与 RR 消融策略

## 1. 背景与问题

典型单节点业务为 8 个 Client、每 Client 16 线程；每轮写入一个 8 MiB key，并对同一 key 发起十个
并发读取。写 QPS 约 315，读 QPS 约 3150。8 MiB 请求按默认 4 MiB 最大 WR 拆成两个 WR；瞬时并发
带宽显著高于均值。

当前主干已有以下能力：

1. 目的端芯片按内存 NUMA 位置保持亲和；
2. `UrmaEvent` 在 WR 生命周期内维护两个源芯片的 relaxed inflight 计数；
3. 源端先以 RR 生成候选；深度差 `> ub_numa_inflight_wr_diff_threshold` 时选择较空芯片；
4. `ResolveNumaPostConfig` 同时进入 Client 普通写、Worker 普通写和 Worker GatherWrite 路径；
5. Worker 通过注册响应把 RR 粒度和阈值下发给 Client，Client 固化首个 Worker 的进程级配置。

问题是：当深度差没有越过阈值时，RR 会主动把 WR 发到非亲和 UB Die。这样虽然能使用两个芯片，
却可能消耗 CPU 间 HCCS 带宽。需求希望先保留本地亲和，只有本地芯片明显排队时才跨芯片溢出；同时
保留 RR 作为同负载下的消融对照。

## 2. 目标与非目标

| 编号 | 目标 | 可验证结果 |
|---|---|---|
| G1 | 亲和优先 | 深度差不超过阈值时，候选始终为传入的亲和源芯片 |
| G2 | 避免饿死 | 亲和芯片比另一芯片多至少 `threshold + 1` 个 inflight WR 时，新 WR 选择另一芯片 |
| G3 | 可消融与可回滚 | 配置 `0` 精确保留 PR 2095 的 RR 候选与深度覆盖语义 |
| G4 | Client/Worker 一致 | Worker 下发策略，Client 初始化 URMA Arena 前应用；后续 Worker 配置冲突只告警 |
| G5 | 热点开销受控 | O(1)，无分配、无锁、无 CAS 预占、无时间读取、无高频成功日志 |
| G6 | 业务闭环 | URMA Mock 下多 Worker、多 Client、多线程完成 8 MiB 一写十并发读及 GatherWrite |

非目标：

- 不精确保证任一时刻深度差都小于阈值；并发 relaxed 快照允许短时超调；
- 不按字节、Jetty 数、时间或估算排队时延做选择；
- 不改变目的端芯片选择、Jetty 生命周期、WR 拆分或完成队列；
- 不用 URMA Mock 代替真实 HCCS 带宽与时延验收；
- 不在本期引入自适应阈值或三芯片以上的通用调度器。

## 3. 配置契约

新增 Worker gflag：

`ub_numa_src_chip_policy`，类型 `uint32`，默认 `1`。

| 值 | 名称 | 候选芯片 | 深度差超过阈值后的行为 |
|---|---|---|---|
| 0 | `ROUND_ROBIN` | 两芯片按原子序列轮询 | 选择低 inflight 芯片 |
| 1 | `AFFINITY_FIRST` | 传入的内存亲和源芯片 | 若亲和芯片较忙，则选择另一芯片；若亲和芯片本来较空，则保持亲和 |

独立配置的理由：

- `ub_numa_rr_type` 表示选择粒度：`0=disabled`、`1=per logical write`、`2=per post`；它不是候选策略；
- `ub_numa_inflight_wr_diff_threshold` 表示是否及何时启用深度反馈；它不是候选来源；
- 三个维度正交后，RR 与亲和优先能在相同粒度、相同阈值下做可信消融。

阈值语义保持严格边界：

- `difference <= threshold`：保留策略候选；
- `difference > threshold`：选择低 inflight 芯片；
- `threshold == 0`：关闭深度反馈，仅使用候选策略。因此 RR 模式为纯 RR，亲和模式为纯亲和；
- `ub_numa_rr_type == 0` 或 NUMA affinity 未生效：直接返回传入芯片，不执行候选策略和深度比较。

Worker 启动时校验策略仅允许 `[0, 1]`。Client 对 Worker 返回的未知策略记录告警并归一化为新版本默认
`AFFINITY_FIRST`，避免将未来未知值直接带入热点路径。

## 4. 选择算法

```text
Select(transmitted_chip, affinity_enabled):
    if !affinity_enabled or rr_type == disabled:
        return transmitted_chip

    if policy == affinity_first:
        candidate = transmitted_chip
    else:
        candidate = next_round_robin_chip()

    if threshold == 0:
        return candidate

    chip1 = relaxed_load(chip1_inflight)
    chip2 = relaxed_load(chip2_inflight)
    if abs(chip1 - chip2) <= threshold:
        return candidate
    return chip1 < chip2 ? chip1_id : chip2_id
```

对于亲和优先模式，正常路径不再执行 RR 序列的 `fetch_add`。只有两个 relaxed load、整数比较和分支；
RR 模式保持现有一次 relaxed `fetch_add`。不对“选择后但 WR 尚未构造”的请求做预占，因此多个并发线程
可能同时观察旧快照并溢出到同一芯片。这是为避免热点 CAS/锁争用而接受的近似控制。

### 4.1 共享状态与并发

| 状态 | Owner | 读者/写者 | 保护与语义 |
|---|---|---|---|
| 策略、粒度、阈值 gflag | Worker 启动配置；Client 首个 Worker 注册结果 | 所有发送线程读取；启动/初始化线程写入 | 沿用现有启动前配置和 Client `call_once`；请求期只读 |
| RR sequence | 进程级 `UrmaManager` | RR 模式发送线程 `fetch_add` | relaxed 原子；只要求分散，不要求全序 |
| per-chip inflight | 进程级 `UrmaManager`，生命周期由 `UrmaEvent` 增减 | 发送线程读取；Event 构造/析构写入 | cache-line 隔离的 relaxed 原子；快照只作反馈信号 |

没有新增锁，也没有锁顺序。无后台线程、无新队列、无新分配、无数据复制、无持久化状态，关闭和恢复
流程不变。

## 5. 配置传播与滚动升级

在 `RegisterClientRspPb` 末尾新增字段 `uint32 ub_numa_src_chip_policy = 33`，不改变签名字段位置。

| Worker | Client | 生效行为 | 原因 |
|---|---|---|---|
| 旧 | 旧 | RR | 原行为 |
| 旧 | 新 | RR | proto3 缺失字段读取为 `0`，保留旧端语义 |
| 新 | 旧 | RR | 旧 Client 忽略未知字段并继续原选择逻辑 |
| 新 | 新 | 默认亲和优先 | Worker 默认 `1` 下发，Client 在 Arena/URMA 初始化前固化 |

集群内 Worker 必须使用相同配置。Client 延续首个 Worker `call_once` 规则：首值设置 affinity、粒度、阈值、
策略；后续 Worker 任一值不同只记录限量的配置冲突告警，不在运行期切换全局策略。

回滚无需代码回滚：全体 Worker 配置 `ub_numa_src_chip_policy=0`，新 Client 即恢复 RR；滚动升级期间旧
Client 自然保持 RR。

## 6. Client 与 Worker 数据路径

唯一策略实现点仍为 `UrmaManager::GetAffinitySrcChipId`：

1. Client 发往 Worker 的普通 URMA WRITE 通过 `ResolveNumaPostConfig` 选择；
2. Worker 发往 Client/Worker 的普通 URMA WRITE 通过同一入口选择；
3. Worker-to-Worker Batch Get 的 GatherWrite 也通过同一入口构造 `bondp_jfs_wr_t.src_chip_id`；
4. `ub_numa_rr_type=1` 时首个 Post 选择一次并在逻辑写内复用；`2` 时每个 Post 重新选择；
5. `UrmaEvent` 继续把选定芯片的 inflight 计数从构造持有到完成/清理析构。

所以没有 Client/Worker 两套算法，协议传播后两端复用相同实现，避免行为漂移。

## 7. 可观测性

生产路径不增加每 WR 成功日志。已有累计源/目的芯片写入计数、跨芯片计数、per-chip inflight 快照和
阈值覆盖日志继续使用；覆盖日志从“RR candidate”泛化为“policy candidate”，并带策略数值、候选、选择、
两芯片深度、差值和阈值。

仅在 `WITH_TESTS` 下增加候选分支注入点，使 ST 能证明策略字段已经同时传播到 Client 与每个 Worker：

- `UrmaManager.SrcChipPolicy.RoundRobin`
- `UrmaManager.SrcChipPolicy.AffinityFirst`

`UrmaManager.OverrideSrcChipPolicyDecision` 在一次同步注入中同时覆盖候选、chip1 深度和 chip2 深度，避免
后台 WR 分别消耗候选注入与深度快照注入而产生窗口竞态。该注入只存在于测试构建。

已有 `SrcChipSelected.1/2`、`SrcChipInflightBalanceOverride`、Gather 选择与 counter drained 注入点继续用于
验证两芯片选择、深度溢出和生命周期闭环。

## 8. 性能评估

| 维度 | RR | 亲和优先 |
|---|---|---|
| 候选开销 | 一次 relaxed `fetch_add`、取模 | 一次分支和传入值复制，不推进 RR 原子 |
| 深度反馈 | 阈值非零时两个 relaxed load | 相同 |
| 锁/分配/复制 | 无新增 | 无新增 |
| HCCS 倾向 | 平衡时仍可能跨 CPU | 平衡时优先不跨 CPU；拥塞才溢出 |
| 短时超调 | 可能 | 可能；不做同步预占 |

硬件消融必须在完全相同的请求、粒度、阈值和拓扑下只切换 policy。固定 8 MiB、8 Client × 16 线程、
315 写 QPS、3150 同 key 并发读 QPS，记录每芯片 inflight/WR、源到目的芯片矩阵、HCCS 单双向带宽、
端口带宽、吞吐、超时率、P50/P99/PMax。验收目标为 P99 不高于 1.5 ms、PMax 不高于 5 ms，并检查
是否存在连续采样窗内一侧空闲而另一侧高排队。Mock 结果不得作为这些硬件指标的替代证据。

## 9. 风险与回退

| 风险 | 控制 |
|---|---|
| 亲和芯片在阈值内积累更多 WR | 严格 `> threshold` 后溢出；阈值沿用可配置默认 15 |
| 并发线程同时溢出产生振荡 | 接受短时超调，不做预占；通过时间序列和 override rate 观察 |
| 集群配置不一致 | Worker 文档要求一致；Client 首值冻结并告警冲突 |
| 新旧版本行为不同 | proto 缺字段映射 RR；兼容矩阵明确；policy=0 快速回滚 |
| 测试注入掩盖真实 NUMA | NUMA Arena/绑定由独立 UT 证明；Mock ST 只注入物理映射不可保留的部分 |

## 10. 代码落点

- `src/datasystem/common/rdma/urma_send_lane.h`：策略枚举；
- `src/datasystem/common/flags/*`：flag、声明、校验；
- `src/datasystem/protos/share_memory.proto`：Worker→Client 字段 33；
- `src/datasystem/worker/worker_service_impl.cpp`：注册响应下发；
- `src/datasystem/client/client_worker_common_api.cpp`：注册后应用；
- `src/datasystem/common/rdma/fast_transport_manager_wrapper.*`：非 URMA/URMA 统一包装；
- `src/datasystem/common/rdma/urma_manager.*`：归一化、首值冻结、候选与覆盖；
- `tests/ut/client/*`、`tests/st/client/object_cache/urma_numa_inflight_balance_test.cpp`：TDD 与 E2E；
- `cli/deploy/conf/worker_config.json`、中文部署文档、operation logger：运维配置面；
- `.repo_context/modules/infra/common-infra.md`、`quality/tests-and-reproduction.md`：窄范围上下文更新。
