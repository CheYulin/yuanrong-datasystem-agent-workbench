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

`validation/freeze_evidence.py` 将这一步脚本化：输入终态 live-driver JSON、既有 SSH alias 与新输出目录，单次 SSH 只读传输无压缩 tar。远端固定读取 `/etc/kvc-lab/runner.json` 并要求 state root 为 `/home/cache/kvc-lab`，job ID 只能是 32 位小写 hex。只选择 completion 清单中的 request/build/coverage/manifest/commands、每条 SDK raw、各 deployment 资源、qualification/one-hop 和有效配置；不枚举全目录、不抓取全量日志、不写远端文件。

本轮固定目标为 80 logical CPU 主机，读取进程绑定预留的 38、39 物理核，避免与正在执行的后续实验 server/client CPU 集合竞争；该脚本不调整其他进程的亲和性或主机配置。

remote completion 本身以 job ID、终态和完整原生结果与网页证据匹配；其余原件必须与该 completion 中的 SHA/size 绑定。远端先核验常规文件与 SHA，本地再次拒绝额外、缺失、重复、链接、越界或超限 tar 成员并复核 SHA/size。`freeze-manifest.json` 记录脚本与输入 SHA、固定源包 pin、精确成员清单、传输 tar SHA 和每份原件身份；失败传输保留 `valid=false` 及错误，重新执行必须用新目录。默认每份文件不超过 16MiB、总原件不超过 512MiB。

## 4. 执行示例

```bash
python3 -B validation/freeze_evidence.py \
  --run recovery-probe-004-result.json --target tiantiyun-80c128g \
  --out audit-inputs/recovery-probe-004

python3 -B validation/audit_study_004.py \
  --run recovery=recovery-probe-004-result.json \
  --artifact-root recovery=audit-inputs/recovery-probe-004 \
  --out audit-recovery-probe-004-final.json

python3 -B -m unittest discover -s tests -p test_study_audit.py -v
```

多个 `--run NAME=file` 与相应 `--artifact-root NAME=dir` 可以合并独立运行。汇总键包含 product/profile/op/B/value size/nodes/workers/clients、server/client/master CPU 预算、memory、构建 SHA、安装源包 SHA；预算或源码不同不会混池。同一物理 job 被换两个名字重复输入会拒绝。只有整个独立运行通过时，其 rows 才进入中位数/min/max 汇总。

## 5. 输出与失败处理

输出新 JSON 文件，拒绝覆盖旧审核证据。包含每轮输入 SHA、完整矩阵覆盖、逐项问题、每条审核结果、分产品/接口/B/规格的性能与资源中位数/min/max、CPU 配额利用率、throttling、采样覆盖和短窗口/P99 样本标记。所有指标缺失保留缺失；没有补零。退出码 1 代表已终态证据未通过，waiting 退出码 0 且 `passed=null`，明确不构成完成验收。

## 6. 采样区间 IO 与标准工具原件审核

`validation/audit_observability.py` 对已冻结的终态目录做额外的纯本地审核。复用前述审核器的安全路径、SHA 和 case identity helper；从 completion 清单逐个核对 SDK raw 与 `resources.jsonl[.gz]` 的大小和 SHA。每条结果使用 raw 的 `measure_start_mono_ns` / `measure_end_mono_ns`，并核对 completion 声明的窗口与接口、批量、大小、clients 参数；不会依靠 service 附加的 missing 行推算观测。

```bash
python3 -B validation/audit_observability.py \
  --artifact-root formal=audit-inputs/formal-baseline-004-attempt001 \
  --out audit-io-formal-baseline-004-attempt001.json

python3 -B -m unittest discover -s tests -p test_observability_audit.py -v
```

### 6.1 IO 数字的范围

输出按 job ID、variant/product/repeat/value size/batch/operation 标识每条 case，分别记录 server、server/master、server/workers、client、monitor 的窗口内采样首尾时间、样本数、采样跨度、最大采样间隙、SDK 窗口两端未覆盖间隙及覆盖率。覆盖率为“窗口内最后样本减首个样本”除以“原生 SDK 窗口时长”。

对每个设备分别校验 `io.stat` 的 rbytes/wbytes/rios/wios 等 counters，计算首末样本差值，以及按实际采样跨度计算的 bytes/s；保留原始首末 counters。数字表示 **SDK 窗口内已采样区间的差值**，不是整个 SDK 窗口的精确 IO 总量。异步写回的记账也不能直接归因到某一个 SDK 调用。

设备号为 cgroup 的块设备记账对象。跨设备汇总仍保留设备级明细；未确认 device mapper 与底层设备拓扑时，不能将其和解释为物理介质总流量。server/master、server/workers 是 server 的子角色，也不能与 server 相加。

### 6.2 缺失、空值和 counter reset

| 状态 | 数值处理 |
| --- | --- |
| 所有采样均存在同一设备 counters 且单调不减 | 返回已验证差值；明确存在且不变的 counters 才返回数值 0 |
| 字段缺失、格式无效、角色缺失 | 保留 unknown/null，单列原因 |
| `io.stat` 读取成功但始终为空、没有设备 counters | `empty_no_device_counters`，保留 unknown/null，不能填 0 |
| 某设备只出现在窗口部分样本 | `partial_device_counters`，不给该角色汇总伪造完整差值 |
| 任一设备 counter 倒退 | `counter_reset`，独立统计，不用截断到 0 的方式掩盖 |
| 窗口内不足两个样本 | `insufficient_samples`，不能给出速率 |

`binding_passed` 只表示原件与窗口关联审核通过，不能解读为所有 IO 字段都有数值，也不是整个性能研究的最终验收。原件绑定失败、窗口不匹配或非单调采样时间线会留下逐 case 的 input failure；不会生成替代数值。输出拒绝覆盖旧证据，同一物理 job 也不能换名重复输入。gzip 解压读取有 64MiB 上限。

### 6.3 标准工具审核的独立边界

标准工具不属于最小正式冻结集合。仅在已经本地保存且 SHA 绑定的日志上显式加 `--include-tools`，才审核 dstat/iostat/mpstat/pidstat 的实际数值采样、常见权限/参数/启动错误、采样末尾与资源记录的覆盖关系；缺少本地日志时标记 `not_in_frozen_input`，不会再连接目标主机或压缩日志。

mpstat/dstat 的 host-global steal/iowait 是整份工具日志的观测，不能用 cancel/recovery 探针结果代替正式 96 条性能窗口的环境证据。显式 `--log-timezone` 决定日志时间解释，并检查时钟偏差；日期换日或时钟倒退需单独复核。未加时间戳的 `iostat -x -y 1` 只能用样本块数与资源记录跨度核对大致持续时间。工具 stdout 没有独立退出码回执，因此“存在连续采样、无可见早退错误”不等于“工具退出码为 0”，global steal 打印为 0 也不证明完全没有调度干扰。

9 项小型变异测试覆盖：真实 delta/rate 与覆盖率、明确 0 对比 unknown、缺失及空 counters、设备部分出现、counter reset、SHA 篡改、SDK 窗口错配、重复采样时间、符号链/路径越界、重复 job 与输出覆盖保护、标准工具只有 header/错误时不能冒充采样。测试不调用 SDK、SSH 或任何现场进程。

## 7. 从验收结果生成研究网页

`validation/build_study.py` 是报告打包模块，只接收全通过的组合审核 JSON。六个固定组名 baseline / worker / client / metadata / nodes2 / nodes4 必须对应六个独立任务；它按 `configs/*.json` 经真实 `spec.preview_plan` 重建组契约，核对每条 case 身份、接口、批量、重复、拓扑和资源预算，读/EXIST实际窗口至少为正式组约定时长的99%，拒绝用小探针换名代替正式组。源包与产品构建继续使用 study004 pin。

脚本复用审核器的纯统计函数，要求 summaries 与已通过 rows 的分组、轮次身份和所有数值完全一致，不从原生 SDK 原件重新计算测量。输出 `study.json`、原字节 `audit.json`、可选 `observability.json` 和 SHA/size 清单；原始账户会话与 live-driver envelope 不发布。结论文字显式通过 `--findings` 提供，并附来源 job ID；IO 概述使用前节已绑定原件的审核汇总，未知值仍为 null。

```bash
python3 -B validation/build_study.py \
  --audit audit-six-formal-runs-004.json --findings findings.json \
  --observability observability-overview.json --out /new/frozen-report
```

安装器 `deploy/install.py --study-dir /new/frozen-report` 在任何远端变更前复核精确文件集、SHA/size、原审核与显示数据的绑定，之后发布受保护的 `ui/data/`。图表库和 Mermaid 随站点安装到受保护的 vendor 目录，不依赖旧报告资源路径。独立 `ui/study.html` 只发 GET 请求，包含基础四接口、worker/client、中心配额和逻辑节点曲线；尚未验收点不显示为零。页面按任务子组再次验证完整轮次后才绘图。

`tests/test_study_package.py` 的 11 项变异测试覆盖错误组、缺组、重复任务、未验收行、汇总及轮次身份篡改、报告文件增删改/符号链、显示数据与审核错绑及 IO 显示与原件不一致；合成 fixture 仅用于本地门禁，不进入正式报告。

## 8. IO 概述的可复现组合

`validation/compose_observability.py --audit <六组性能审核> --io baseline=<IO审核> ... --out <新文件>` 消费六份单任务IO审核，不连接目标机、不重新扫描原件。它要求性能组、IO组的job ID及每case原件/测量窗口完全一致，所有输入由同一冻结IO审核器和helper产生；记录每份输入文件SHA及组合性能审核原字节SHA。

概述只累加server角色，不将master/workers子角色再次相加。unknown、counter reset和已知区间分别保留；有unknown时完整读写合计为null，已知小计另列。`build_study.py` 再检查概述的 `performance_audit_sha256`、通过状态、完整六任务身份和每组窗口数，避免把其它研究的IO概述误配给当前图表。6项组合测试覆盖错任务/缺case/窗口错配、unknown处理、子角色去重、SHA/版本与确定性输出；不调用产品SDK。

## 9. 文档表格的响应式输出

`deploy/render_docs.py` 在共用阅读器输出上追加本实验的 `docs-tables.css/js`，复制到受保护的 `_reader/` 后由HTML引用；不修改共用模板。JS从实际thead建立列名、headers关联及table/row/cell角色，容器宽度不足880px时逐行卡片化，520px以内单列。卡片保留所有列值，避免靠压窄列或隐藏溢出来通过检查。

真实5篇RFC在1440/900/390/320四宽本地共20页验收，R04每页9张表，其余各1张表；所有wrapper及表格均无横向溢出、标签完整，实际2张Mermaid渲染通过。站点注入的共享布局脚本与原生reader依赖分别识别，统计beacon在只读验收中持续阻断，不将其算作产品错误。最终线上回执另见发布记录。

指南直接引用同一套表格资产；本地四宽验证12个表格视图，全部内外零横向溢出，Mermaid和导航通过，用时3.889秒。此修改仅在指南增加CSS/JS引用，性能数据与运行模块保持其冻结身份。
