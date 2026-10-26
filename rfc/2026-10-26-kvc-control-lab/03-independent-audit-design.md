# Study 004 独立离线验收脚本

## 1. 目的与输入

`scripts/experiments/kvc-lab/validation/audit_study_004.py` 只读取浏览器 live-driver 的 JSON 证据与已终态任务的本地原件目录；不连接 SSH、不导入 benchmark、不启停产品。任务尚未终态时输出 `waiting`，不会按当前缺失条目判最终失败。

脚本采用 `job.plan.variants` 的完整笛卡尔积为预期矩阵，直接消费 `job.completion.results`。HTTP service 的 `job.results` 可能附加 missing 占位，因此不能用于证明原生结果完整。

## 2. 核验规则

1. 每个 variant/product/repeat/value size/batch/operation 恰好一条；所有原生结果必须 valid，规格与提交计划一致。
2. 检查安装源包 SHA 为本轮冻结的 `61c5c2e3151b227d5cc8bc86688164e622027ce953f67ef555ca54c4e044779e`，DS/MC 构建 SHA 分别固定为 rc44 与 v0.3.13.post1；构建回执中的每个产物必须通过原先的 SHA 核验。
3. 原件 SHA/size 需匹配 remote completion 的产物清单，冻结 request 的 plan/config/job_id 需与网页证据一致。原生 SDK raw 的客户端退出、success 计数、参数、repeat namespace、吞吐与窗口时长需要一致。
4. `keys_s = completed native batch calls / SDK duration × batch`；P99 从原始 logarithmic histogram 按 ceil(0.99×N) 独立求所在桶上界，不能把对象、桶下界或缺值当数字。
5. 每条 case 至少有两个 SDK 测量窗口内的资源样本。用原始 cgroup CPU counter 与 sample 时间重算 server/client/master core usage、master throttled_usec、sampled server charged memory peak；20 秒读/EXIST 的采样首尾覆盖至少 90% SDK 窗口。
6. 新写 PUT 为有限 fresh cardinality，可能短于 20 秒；明确保留 short_window 标记。P99 原生完成调用少于 1000 次时标记 low_sample_p99，不把 keys 数当调用样本数。

源包 SHA 在这里证明已声明安装版本与冻结 pin 一致，脚本不声称重新编译源码，也不独立替代源包的下载 SHA 验收。一跳 RPC、登录密码输入、跨物理机网络、GPU、HA 不在本脚本验收范围。

本地 `tests/test_study_audit.py` 使用独立生成的小型 SDK JSON、histogram 和 cgroup samples fixture，14 项回归不依赖现场原件、SSH 或 SDK。真实恢复探针的冻结原件另外执行同一审核器；单元 fixture 的通过不替代现场验收。

## 3. 本地原件布局

保留远端 job 目录中的相对路径：`complete.json`、`request.json`、`build-verification.json`、每条 `raw_artifact`、各 deployment 的 `resources.jsonl`（或清单中绑定的压缩原件）。脚本拒绝绝对路径、`..` 与符号链。只需已终态任务的最小原件；正式压测期间不做全目录采集、压缩或原生构建库扫描。

## 4. 执行示例

```bash
python3 -B validation/audit_study_004.py \
  --run recovery=recovery-probe-004-result.json \
  --artifact-root recovery=audit-inputs/recovery-probe-004 \
  --out audit-recovery-probe-004-final.json

python3 -B -m unittest discover -s tests -p test_study_audit.py -v
```

多个 `--run NAME=file` 与相应 `--artifact-root NAME=dir` 可以合并独立运行。汇总键包含 product/profile/op/B/value size/nodes/workers/clients、server/client/master CPU 预算、memory、构建 SHA、安装源包 SHA；预算或源码不同不会混池。同一物理 job 被换两个名字重复输入会拒绝。只有整个独立运行通过时，其 rows 才进入中位数/min/max 汇总。

## 5. 输出与失败处理

输出新 JSON 文件，拒绝覆盖旧审核证据。包含每轮输入 SHA、完整矩阵覆盖、逐项问题、每条审核结果、分产品/接口/B/规格的性能与资源中位数/min/max、CPU 配额利用率、throttling、采样覆盖和短窗口/P99 样本标记。所有指标缺失保留缺失；没有补零。退出码 1 代表已终态证据未通过，waiting 退出码 0 且 `passed=null`，明确不构成完成验收。
