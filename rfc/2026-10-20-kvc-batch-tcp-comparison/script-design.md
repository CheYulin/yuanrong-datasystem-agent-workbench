# 4. 脚本设计与可复现入口

入口：`scripts/experiments/kvc-fair/batch_workflow.py`，所有外层命令（包括失败）写入 `operation-timings.jsonl`；远端每条 lifecycle / gate / benchmark 命令另写 event，记录 wall 与同进程 monotonic 耗时。两个层级嵌套，不能相加当总耗时。

```sh
python3 scripts/experiments/kvc-fair/batch_workflow.py stage
python3 scripts/experiments/kvc-fair/batch_workflow.py prepare
python3 scripts/experiments/kvc-fair/batch_workflow.py gate
# 带 strace 的门禁若如本次在 Mooncake B4096 失败，保留原失败，不覆盖。
python3 scripts/experiments/kvc-fair/batch_workflow.py diagnose
python3 scripts/experiments/kvc-fair/batch_workflow.py compose
python3 scripts/experiments/kvc-fair/batch_workflow.py smoke
python3 scripts/experiments/kvc-fair/batch_workflow.py run
python3 scripts/experiments/kvc-fair/batch_workflow.py restore
python3 scripts/experiments/kvc-fair/batch_workflow.py collect
python3 scripts/experiments/kvc-fair/batch_workflow.py report
python3 scripts/experiments/kvc-fair/batch_workflow.py publish
```

重跑须显式设置新的 `--root`、`--environment-root`、`--local-output`、`--report-path`，新输出目录拒绝覆盖；本入口默认目录为本次执行的固定命名，不等于可以覆盖重放。源码构建目录由只读链接复用；如需从零构建，先按旧 build.md 生成相同版本、记录构建产物，再传新的 `--base`。

| 脚本 | 职责 |
|---|---|
| batch_stage.py | 持共享锁、验证原构建 SHA、复制新脚本、创建独立 namespace 和不可变输入清单 |
| batch_*adapter 三方文件 | 同步原生 batch 合同，严格字节/向量检查，不重试，不隐藏网络循环 |
| batch_gate.py | 两端点四档真实正确性、独立 key 顺序及 insert/upsert 语义；每步耗时 |
| batch_runner.py | 全程 flock；按轮换顺序串行启停三产品；显式 N/A；拒绝 gate 未通过的正式执行 |
| batch_benchmark.py | spawn 8 私有客户端；同步 warmup/测量/后验验证；原始直方图与全批延迟 |
| monitor.py / batch_interference.py | cgroup/系统资源与外部进程 CPU 审计；外部进程仅观察，绝不广泛 kill |
| finish_environment.py | terminal gate + main、四 cgroup 排空后，只恢复 preparation 记录过的 setroubleshootd；第二 summary 是 gate，不是额外资源压测 |
| batch_analyze.py / batch_report.py | 从原结果重算、区分 N/A/缺失/短窗口，生成相对资产网页与 SHA 绑定的复现包 |

共享状态：每客户端私有 key，无共享 key 写竞争。服务端两服务合计同一固定配额；每轮后由旧版 PID/start-ticks/cmdline 保护的 lifecycle 只停止本任务进程，并确认所有实验 cgroup 排空。异常中止整个组并保留原始失败文件，恢复环境需先确认 cgroup 排空。没有产品源代码改动，没有持久化/故障恢复行为修改；不宣称这些性能用例证明容灾或崩溃一致性。

## 4.1 本次门禁分层

原 `batch-gate-001` 带 strace：DS 两端点、insert/upsert 四档全通过；Mooncake client0 在 B4096 GET 返回10个空项，源日志为 TRANSFER_FAIL。该 suite 失败，完整保留。独立 `untraced-diagnostic-001` 不调参数、不启跟踪，以相同冻结 adapter/config/binaries 对 Mooncake 四个端点/模式组合和 LMCache 两端点组合重新验证，6/6实际通过，耗时73.304707536s。源码有每peer有界TCP queue，可能受跟踪开销影响，但当前未观察到确切QUEUE_FULL原因，不当作已定位产品缺陷。

`batch_compose_gate.py` 只组合已完成的正确性证据：严格核对全部冻结输入/构建物、10份组件JSON及原生能力/完整步骤，生成新 `batch-gate-002`，保留组件路径/SHA/实际起止/原失败summary；组合本身耗时2.118175008s，不伪称重跑。后续 runner 显式使用 `--gate batch-gate-002`；正式性能测量无 strace。原 gate 与诊断的成功均不充当正式重复。

本次 composer 真实执行源码 SHA 为 `4d0bba088f8de1cc0039ada8d997af9d0528b65b37774c1e9b5a095d03721bbf`；后续 canonical 版本仅补 helper 快照归档，不能把新版哈希标为旧执行源码。诊断真实源码 SHA 为 `393b16235fd633658feaa7f10df7e91c155f893ea8f4f46124b371a2169dda3a`。收集阶段需另取这两个外部 helper 原件，和 stage 文件列表分开校验。
