# 6. 结果验证与最终验收

状态：**In Progress**。r3 原生批量门禁、C8 smoke 和 DataSystem 批量 TCP 载荷审计通过，但随后正式尝试失败，并发现双Worker未隔离RocksDB目录。原件保留；修正目录后的新门禁、正式三重复、性能结论与公开交付均待验收。本文件不把 smoke 数值作为正式性能结果。

## 6.1 实际输入与比较边界

本次主配置为 `/home/worktrees/kvc-batch-tcp-20261020-r3`；本地已采集证据根为 `/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r3`。产品复用固定源码构建：DataSystem rc44、Mooncake v0.3.13.post1、LMCache v0.5.6；不是本轮重新编译。r3 Mooncake queued/pending 均为 8192，属于容量适配 profile，不能标为默认配置。

| 检查项 | 已确认事实与限制 |
|---|---|
| 每次调用的 key 数 | B=1/16/256/4096；DataSystem/Mooncake 调用原生 batch；一次公开 API 不等于一次内部 RPC |
| 相同实际数据 | 每 key 1,024 bytes、C8；正式 harness 的相同 `run_id/client_index/phase/index` 产生相同 key，payload 由固定 SHAKE256 规则产生，不因产品或 B 改变 |
| payload 区分度 | 性能工作集每客户端使用四种基础 payload，UPSERT 使用另一组四种 payload；不是每个性能 key 都有独一无二内容。独立 gate 另使用按 key/版本派生的独立 bytes 验证映射和顺序 |
| 固定热工作集 | 总计 65,536 keys / 64 MiB，每客户端私有 8,192 keys，跨 B 保持同一组 key；GET、UPSERT、EXIST hit 使用该池，EXIST miss 使用独立 absent namespace |
| fresh PUT | 正式每 B 共 262,144 新 key / 256 MiB；smoke 为 32,768 新 key / 32 MiB。不能把旧 key ACK 当首次插入，也没有隐藏 delete 循环 |
| 更新与后续读 | UPSERT 交替两组真实内容，计时后验证最后版本，并在窗口外完整恢复/读回基础工作集；GET 不应继承未知更新版本 |
| 资源 | server/client 各 8 CPU、12 GiB，monitor 独立 2 CPU、1 GiB，swap=0；两个服务共用服务配额。原始 cgroup/配置与采样归属仍需随正式结果核验 |
| 计量 | 吞吐包含同步起点至最晚客户端结束；API 延迟为整个 batch 完成时间，含适配器 O(B) 校验，不能除以 B 后称单 key P99 |
| LMCache N/A | 仅 B1 支持本次等价 PUT/GET；所有 B 的 UPSERT 和 B>1 的 PUT/GET 明确 N/A。EXIST 为原生 P2P 独立 key 向量，含命中锁释放 ACK 的成本，不是零成本布尔缓存 |

工作负载依据冻结 `batch_benchmark.py` 与 `parameters.json`；能力依据三个 adapter 的 metadata 和 [接口设计](test-design.md)。同 key、同字节、同配额减少输入差异，但不保证各产品内部 RPC 数、锁、分配和复制相同。

## 6.2 r3 原生正确性门禁

`batch-gate-001/summary.json`：2026-10-20 **04:20:51.766565–04:23:19.454293 UTC**，147.687730193 秒，`valid=true`、`failed=[]`，三个产品组均完成。结束时 parent/server/client/monitor 四个 cgroup 成员均为空。

本地对10份真实客户端 JSON 重新执行 component 合同校验，覆盖如下；这次校验读取已执行原件，不重新调用产品。

| 产品 | 初始端点 × 写模式执行 | B 组合 | 步骤失败 |
|---|---:|---:|---:|
| DataSystem | 2 × insert/upsert = 4 | 16 | 0 |
| Mooncake | 2 × insert/upsert = 4 | 16 | 0 |
| LMCache | 2 × insert = 2 | 8 | 0 |
| 合计 | 10 | 40 | 0 |

原件共478个 passed 步骤和34个明确 not_applicable 步骤。检查包括完整 bytes、反序/子集 GET、EXIST 全命中/稀疏混合/反序/全缺失向量、已有 key 重写后的旧值或新值语义、初始化和 close。LM 不支持的 B>1 PUT/GET 仅用标明的 scalar setup/spotcheck 完成正确性准备，不改写成原生批量性能。

门禁 `summary.json` SHA256：`49868fd9e557ae6ee2d79f69301a5e850075223b05c5170b0bf2e9420e81036b`。门禁与 smoke 的 `batch-stage.json` 字节 SHA 均为 `2cc67095ed2e9e1f0319ff60190d30eccdbbef544ecd3ba527171e5d73e28c3d`；它们使用同一冻结 stage。

## 6.3 r3 C8 编排 smoke

`batch-smoke-001/summary.json`：2026-10-20 **04:25:23.437817–04:30:02.123699 UTC**，278.685888789 秒，`valid=true`、`failed=[]`；monitor 与 interference 退出码均0，结束四个 cgroup 排空。其 SHA256 为 `7dfad7d4b5ce98026fbc76143b8bc7618561cea2eb99a992235a6c1284430545`。

本地读取全部60份操作 JSON，确认50个不同的有效身份、10个 N/A：DataSystem20、Mooncake20、LMCache10个有效结果；失败 batch 为0。所有有效结果均为C8、1 KiB、65,536总热 key、每客户端8,192 key；客户端身份0–7齐全、400个客户端进程退出码均0，close 确认齐全。合并 histogram 样本数等于成功 batch 数，`success_keys=success×B`；每个 fresh PUT 均完成32,768个新 key。两个支持 UPSERT 的产品在四档 B 后各累计完整恢复/验证262,144个 key（同一65,536池重复四次），不是额外扩大工作集。

smoke 的时间型操作仅1秒、预热0.1秒，fresh PUT 固定32 MiB；正式合同为20秒、预热3秒和256 MiB fresh PUT。B4096 在短窗口内可能只有少量 batch，smoke 只证明接口编排可执行，不提供正式中位数、P99或资源排名。

## 6.4 r3 批量 TCP 载荷证明

新的 `datasystem-batch-tcp-verification.json` 是本配置 gate 的独立离线审计：`valid=true`，16个 case、32个 PUT/GET 方向通过，解析10.107045058秒。文件11,623,370 bytes，SHA256 `922c976705d143e57b62726dd18a86fdfbfaecff171e243d03320e5fdb378427`；记录144份输入身份和3份 parser 源码 SHA。

规则同时要求同 case/version0 的任一完整1,024-byte payload、实际成功 syscall 返回范围、正确 PUT/GET 方向、操作时窗及真实 Worker TCP endpoint，且方向总成功 TCP 字节量至少 `B×1024`。保留的 live 配置证明关闭 client/worker SHM 传输及 RDMA/URMA；EXIST 为不传 value 的元数据查询，不套用 payload 字节下限。

方向总量含协议开销；一批内找到完整确定性 payload 不证明每个 key 的全部字节都被 `-s2048` 文本打印，也不证明所有 Worker 间字节、确切 RPC 数或正式窗口持续 strace。正式压测不携带该跟踪开销。更完整的源码链和旧审计限制见 [datasystem.md](datasystem.md)。

## 6.5 默认配置失败历史不能省略

`history/default-r2` 保留原始 gate1 失败50.821004702秒、独立无跟踪诊断通过73.304707536秒、证据组合2.118175008秒、默认 C8 smoke 失败199.051957721秒，以及容量机制诊断96.501955708秒。组合是选择并核对既有正确性证据，不是重新执行 gate；默认 C8 B4096 UPSERT 的 `TRANSFER_FAIL(-800)` 仍真实存在，不能用组合抹去。

r2 的旧 key0-prefix TCP 审计12/16与后续完整 any-key payload 审计16/16分别保留。容量机制诊断观察到 default 失败、8192配置成功，但未捕获明确 queue-full/rejection counter；只允许写配置观测差异，不能把队列已满当成已确认根因。r3 是新根目录和新冻结配置，其通过结果不覆盖默认配置的失败。

## 6.6 正式结果与最终验收清单

| 验收项 | 当前状态 / 待填证据 |
|---|---|
| 正式三重复完整身份 | r3尝试失败：8个有效操作、1个无效操作，整产品组完成数0；需修正目录后重跑。完整设计仍为9产品/重复组、180操作 JSON，其中150支持结果和30 N/A，不是已完成数量 |
| 各操作成功、失败、部分提交未知数 | 待正式原件重算；不将 smoke 的0失败外推至正式结果 |
| batch/s、key/s、MiB/s与三重复中位数 | 待正式结果；无等价能力不参与排名，不填零 |
| P50/P99合并桶与低样本提示 | 待核对每客户端 histogram、成功数和时间边界；不能平均客户端 P99 |
| CPU/内存/anon+SHM/IO/throttling | 待正式 cgroup 样本和窗口覆盖审计；缺样、counter reset、短PUT不足采样保持 unknown/null |
| 干扰与生命周期 | 待核 interference 原件、监控退出、所有进程结束与环境恢复；不能因独占授权就宣称没有任何未记录负载 |
| 结果与源码/配置身份 | 待正式 stage/products/native 制品 SHA 与结果引用的一致性验收 |
| 报告/原始证据包/复现ZIP | 待生成、逐文件 SHA/成员/安全解包验证；尚无最终包 hash |
| 本地与公开浏览器验收 | 待运行；不得借用此前单 key 报告的验收计数 |
| 最终公开地址与交付提交 | 预定 `https://yche.me/perf/kvc-batch-tcp-20261020/index.html`；尚未发布验收，不预填提交 hash |

正式r3尝试213.962156825秒后失败；DataSystem B16 EXIST hit的8客户端各1个deadline错误，两个Worker此前已出现同一默认RocksDB目录的文件缺失/大小不符错误。门禁和smoke的成功只对其执行步骤成立，不能认证长窗口或目录隔离。默认目录问题、启动脚本最小修复及旧单key基线的影响详见 [verification.md](verification.md) 第5.6–5.7节；旧DS值不得作为修正后batch收益的公平基准，修正后的B1需随新矩阵重测。

即使后续全部通过，结论也只覆盖固定版本、CPU TCP、单节点双服务、1 KiB/key、C8、四档 B 和所披露的容量配置。未验证RDMA/UB、GPU、跨节点、故障恢复、持久化或其它对象尺寸，不据此泛化。
