# 过程耗时采集与时间线口径

## 1. 输出与复现入口

`scripts/experiments/kvc-fair/collect_timings.py` 是只读后处理工具；不启动服务、不修改压测参数、不重新构建产品。它读取当次已落盘的源证据，输出 `timings.json` 与 `timings.csv`，供 ECharts 时间线和下载使用。

```bash
python3 collect_timings.py \
  --suite /path/to/formal-001 \
  --environment /path/to/environment \
  --orchestration /path/to/orchestration-smoke \
  --operations /path/to/operation-timings.jsonl /path/to/workflow-timings.jsonl \
  --out /path/to/report
```

`--builds` 默认读取 `--suite/builds/`，也支持指定汇总文件或目录。`--environment` 支持包含 `environment-before.json/environment-cleanup.json` 的目录或单个文件；suite 下的 `environment.json` 另外保留为环境信息快照。`--orchestration` 可传烟测目录或 `summary.json`。`--operations` 可传多个本地命令计时 JSONL。`--out` 为目录时生成固定文件名，传 `.json` 路径时 CSV 使用相同 stem。

源文件正在更新时不是原子快照。JSONL 未写完的尾行、半写入 JSON 与缺少输入会进入 `issues[]`；工具不补造时间。产物 JSON/CSV 分别写临时文件后替换，避免读取到半份产物。

## 2. 数据契约

顶层字段：`schema_version, generated_utc, time_zone, duration_unit, steps, status_counts, source_timestamps, sources, issues, rules, session_summaries, overall_sessions`。

`steps[]` 每行固定包含：

| 字段 | 含义 |
| --- | --- |
| `id/name/category` | 唯一标识、显示名称与口径分类 |
| `product/cell/operation/attempt` | 产品、用例、接口与同一用例的尝试序号；不适用时为空 |
| `start_utc/end_utc/duration_s` | UTC起止与秒数；未观察到的边界/耗时为 null |
| `status` | completed、failed、running、pending、estimated、observed、skipped等 |
| `evidence` | 源文件及 JSON 字段或 JSONL 行号 |
| `scope/clock_domain` | 包含哪些工作、耗时来自哪个时钟域 |
| `estimated/additive` | 是否估计；本轮包含嵌套区间，所有行均不可直接求和 |
| `session_id/archived` | 原始执行 session 与是否来自历史归档；不把重跑前后当作一段 SDK 窗口 |
| `excluded_from_comparison/exclusion_reason_kind/original_valid` | 采集干扰排除标记及原始 valid；有效旧结果可为 superseded_interference，不能算接口失败 |

CSV中的空单元格对应 JSON null，不能当作0。单时刻环境快照标为 `observed`，耗时保持 null。尚未开始的 plan 用例保留 `pending`；正在执行但无结束事件的用例/命令保留 `running`、结束时间和耗时均为 null，不用“当前时间减开始时间”冒充完成耗时。

## 3. 区分命令墙钟与 SDK 窗口

| category | 边界与限制 |
| --- | --- |
| `suite_wall` | summary.started→ended，包含部署准备、压测、关闭与监控退出；进行中结束为空 |
| `cell_wall` | cell_start→cell_complete/cell_failed，包含前置stop/start、settle与各benchmark命令；不含finally中的关闭 |
| `launch_prepare` | cell_start→第一个measure_prepare，只能称启动准备合计区间，不能拆成独立部署/启动/2s等待耗时 |
| `command_wall` | measure_prepare→measure_complete/failure事件，含Python/cgroup启动、setup/preload/warmup、SDK窗口、验证、close与事件处理 |
| `harness_wall` | benchmark JSON的wall_start_ns→wall_end_ns，包含harness内部工作，不包含记录起点之前的Python import |
| `sdk_window` | measure_start_wall_ns作为UTC锚点，加同进程measure_end_mono_ns−measure_start_mono_ns；不包含预填/预热/后验校验/close |
| `teardown_estimate` | cell结束→下一cell开始或suite_complete，只是含关闭、空cgroup检查、summary写入和调度间隙的上界估计 |
| `local_operation` | timed.py/workflow.py记录的命令墙钟，以本地monotonic持续时间为准，UTC起止用于放到时间线上 |

**不能把目标机 monotonic 纳秒直接转换成 Unix 时间，也不能把不同主机的 monotonic 计数相减。** UTC统一由原始时区明确的ISO时间或Unix wall纳秒生成；SDK结束位置只使用该SDK自己的wall起点和monotonic差值。原始before/after时间字符串另存于 `source_timestamps`，保留原始环境快照证据。

当前 `run_suite.py` 没有精确的stop开始/结束事件。关闭耗时必须展示“估计”，不能把下一用例开始前的整个间隙标成精确stop耗时。缺少独立压缩、归档校验、复制、删除、warmup阶段时间时，仅报告已有聚合区间，不补拆分。

## 4. 本轮构建口径

| 产品 | 记录口径 | 本轮原始值 |
| --- | --- | --- |
| DataSystem | 官方源码构建墙钟，包含依赖准备/构建与打包 | 1421.745018s |
| Mooncake | 源码构建墙钟，包含首个USE_HTTP=OFF失败尝试和修复重试 | 729.545727s |
| Mooncake | 下载/依赖准备至最终wheel，包含上行区间与阶段间shell操作 | 950.545727s |
| LMCache | 源码wheel构建/安装命令墙钟，不含此前依赖下载 | 62s |
| LMCache | 原始报告的native wheel build，不含依赖；内部UTC边界未记录 | 53.86s，起止为null |

这些口径不能组成“三产品纯编译速度排名”。Mooncake失败尝试单独保留 `failed` 行，其起点已知、独立终点和耗时未知，仍包含在上层构建墙钟内，不重复相加。

环境before→after是942.861811s的归档、校验、清理与服务停止聚合区间，不能推断其中压缩独占多少秒。已完成的两产品编排烟测summary为44.365739s，只是正确性编排耗时，不是正式吞吐结果。正式矩阵进行中，本章不预填其最终耗时。

## 5. 验证记录

对当前已采集原始正式矩阵、构建、环境、编排烟测及本地操作计时快照执行成功，JSON与CSV行数一致、step id唯一、running/pending结束及耗时为null。

另用临时合成证据验证：带+08:00的时间转UTC、wall纳秒精度、SDK窗口与命令墙钟分离、失败后重试、旧失败结果保留、尚未开始的plan行、部分写入JSONL告警、关闭估计标签。测试不启动服务或构建产品；正式压测进程未受该工具改动。

## 6. 停止后的恢复证据归档

`scripts/experiments/kvc-fair/prepare_resume.py` 为独立的 legacy suite 归档工具。默认只检查并输出计划；`--apply` 才提交归档。工具不停止进程、不清理 cgroup、不启动 suite、不执行输出的恢复命令。以下命令仅供已确认停止后的人工执行，本次开发和 fixture 验证没有在远端运行它。

```bash
python3 /home/worktrees/kvc-fair-20261020/prepare_resume.py \
  --root /home/worktrees/kvc-fair-20261020 \
  --suite /home/worktrees/kvc-fair-20261020/formal-001 \
  --approved-change 'lmcache/start_server.sh=人工授权：启动前切换到自身目录，修复相对路径导入失败' \
  --invalidate-cell r0-mooncake-s65536-c8 \
  --invalidate-cell r0-mooncake-s1024-c8
```

检查通过后，同一命令增加 `--apply`。上面的两项仅为已确认的首批相交 cell；第二次采集的完整相交清单需由停止后的原始 SDK 窗口判断后补齐。未要求排除时省略 `--invalidate-cell`。`--invalidation-reason` 可记录对应采集动作、时间与判定规则。

工具全程持有原 `suite.lock` 的排他锁且不截断锁文件，要求父 cgroup 及 server/client/monitor 和所有子 cgroup 无任务、无 populated 状态。它再次检查归档期间源文件未变、cgroup 仍空。任一条件不满足即拒绝；不会终止现有任务来满足前提。

恢复参数从原始 `parameters.json` 逐项生成，并验证 root/output/manifest 位置及选中产品配置等于原始 `products.json`。`script-hashes.json` 中原已记录路径逐一验证，其中必须包含 benchmark/cgroup/monitor/run_suite；新增 helper 文件不会导致整个字典不同而误拒绝。原始 `builds/<product>.json` 中的 artifact_hashes 与 script_hashes 同时记录 expected/current 并核对，包括 native .so 和 worker。唯一可获准的差异为显式提供上述 `--approved-change` 且 LMCache start_server.sh 严格匹配既知旧 SHA `9eba24b220019fbf82cd00f278b81ef92e2a7d902908784d6b84456e6cecbc2e` →新 SHA `e6af114cac8b531179b65e6b3797b12d67aae529632cf134d5fe69c35fa1f765`。额外修改即使附带说明也拒绝。当前 adapter/runtime/config/manifest 另存哈希；未记录过的初始哈希不补造。

输出包括 `first_session_archive/current_session_archive`、事件 `start_byte/end_byte`、复制字节数、输入校验、排除计划和原参数 `resume_argv/resume_command`。命令由 `shlex` 正确引用；工具只打印，不自动执行。此工具适配本轮保留的原始 driver；将来 driver 的自动恢复校验与本轮远端执行器互不替换。

## 7. 不覆盖的 session 与重测事务

归档位于 `formal-001/history/session-0001` 起的递增编号目录。可用 `--session` 指定下一编号；已有编号、未增加事件的重复归档、改变/截断了历史事件前缀、非完整 JSONL 尾行都拒绝。复制范围为原始 summary/status/parameters/products/plan/script-hashes/events/monitor/environment，以及 resources/cells/builds 子树，包含失败 cell 的 server-logs、原生 iostat/dstat/pidstat 等日志和 samples。源码、venv、编译目录不复制。

每份归档含 `session.json` 和逐文件 SHA256/字节数 `manifest.json`，复制后重新核验，再 fsync 并使用 Linux renameat2(RENAME_NOREPLACE) 原子发布；既有空目录也不得覆盖。`history/index.json` 用原子替换记账；若目录已发布但写 index 前中断，下次由不可变归档重建索引。既有归档每次重新核验，发现字节变化即拒绝。

`--invalidate-cell` 只针对 plan 中指定 cell 已存在的四类标准结果文件 put/get/exist_hit/exist_miss。**先成功提交完整原始归档，再将当前标准文件原子改名为 `<op>.superseded.interference.<UTC>.json`**。不可变 session.json 内记录 cell/reason/原路径/目标路径/原 SHA；`history/transactions/session-NNNN.json` 记录每项重命名进度。原结果的 valid、failure.json、原日志、sample 字节均不修改；这是采集干扰排除，不是接口失败。标准文件缺失使原 driver 以相同参数重测整 cell。

若归档提交后重命名或记账中断，工具提示具体 session；在同样停止/空 cgroup 条件下恢复：

```bash
python3 /home/worktrees/kvc-fair-20261020/prepare_resume.py \
  --root /home/worktrees/kvc-fair-20261020 \
  --suite /home/worktrees/kvc-fair-20261020/formal-001 \
  --approved-change 'lmcache/start_server.sh=人工授权：启动前切换到自身目录，修复相对路径导入失败' \
  --recover-session session-0001 --apply
```

恢复按不可变原计划校验 SHA，已完成的目标原样复用，未完成项继续原子改名。源与目标同时存在、任一原 SHA 不符或路径异常均拒绝。在未完成事务恢复前，不接受新归档或最终汇总。

## 8. 多 session 总体耗时与样本边界

原生 `summary.json` 保留最后一次 driver 执行的含义。恢复工具额外输出 `session-summary.json`，每段完整保留原 summary、归档时刻、事件字节区间和 manifest SHA；另给 earliest_start/latest_end、包含暂停的墙钟跨度、不含暂停的 session 墙钟和、历史失败次数。缺失原始结束边界仍为 null，归档时刻不充当结束时刻。`recorded_session_wall_s` 仅加总已记录完整区间；`sum_session_wall_s` 在有未知区间时为 null。

全部执行停止、最后 native summary 已有 ended 后可补最终汇总，仍不覆盖原生 summary：

```bash
python3 /home/worktrees/kvc-fair-20261020/prepare_resume.py \
  --root /home/worktrees/kvc-fair-20261020 \
  --suite /home/worktrees/kvc-fair-20261020/formal-001 \
  --approved-change 'lmcache/start_server.sh=人工授权：启动前切换到自身目录，修复相对路径导入失败' \
  --finalize --apply
```

`collect_timings.py` 自动发现 history，按每段原始 events 字节范围读取，保留原始行号，用结果的 wall 起点归属 session，避免后续累计目录中的旧结果重复计时。当前已有新事件而 summary 仍等于上一段时，不复用旧边界；已归档但无原结束的区间标为 interrupted_unknown_end。尚未启动的历史 plan 行为 not_run_in_session，当前 plan 行仍为 pending。`session_summaries` 与 `overall_sessions` 均保留各原始 summary，供时间线或下载审计。

source `resources/samples.jsonl` 与 events.jsonl 继续 append；归档保留完整副本，但资源统计不能再把历史副本与当前累计 samples 叠加。原生监控命令日志下一次可能截断，因此必须先归档。collector 只收过程时长，不求资源 IO 总量，报告继续使用当前累计样本的单调时钟区间。

## 9. 恢复 fixture 验证

本地临时目录验证14项（含 generic root-relative native 路径兼容），结果记录于 scratch 的 `resume-fixture-results.json`：默认无源修改；活跃锁/非空 cgroup 拒绝；原 helper 哈希强制校验而新 helper 可添加；native 基线变更拒绝且仅接受精确授权 cwd-fix 哈希对；先归档后排除；在首个结果已改名而事务未更新时故障注入可恢复；恢复幂等；重复 session 拒绝；append offsets 与 index 中断窗口重建；归档篡改检测；最后 summary 不覆盖；30s session 墙钟和与190s含间隙跨度区分；collector 只生成8个独立 SDK 窗口、其中4个历史有效结果标 superseded_interference；原子 no-replace 拒绝既有空目标。故障注入期间原 valid、failure/server 日志与 samples 哈希均保持原值。所有 fixture 未连接远端、未发压、未重构建产品。
