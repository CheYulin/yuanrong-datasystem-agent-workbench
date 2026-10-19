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
| `transport_profile/process_stage/attempt_label/revision_number/reused_source_build` | TCP 预检、命令、修订/权限修正与恢复的归属，或原构建复用标记；不生成新的 SDK 样本 |

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
| `local_operation` | timed.py/workflow.py 的命令及 TCP 工程预检区间，按 `process_stage/clock_domain` 区分；已有 monotonic 持续时间与 UTC 起止分别保留，只有墙钟的恢复凭据明确标为 `target_wall_clock` |

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

环境before→after是942.861811s的归档、校验、清理与服务停止聚合区间，不能推断其中压缩独占多少秒。已完成的两产品编排烟测summary为44.365739s，只是正确性编排耗时，不是正式吞吐结果。正式首轮 UTC 2026-10-19 16:58:56.725796→18:34:56.079606，墙钟 5759.353810s；完成72个 cell，9次启动失败。补跑与资源专项另记 session，不与该区间混合。

主矩阵最终 UTC 19:00:42.486301 结束，81个 cell 完成、当前失败0。补跑 session 墙钟1344.683792s；两段已记录执行墙钟合计7104.037602s（118.400627分钟），包含201.722903s归档/检查间隙的整体跨度7305.760505s（121.762675分钟）。原始 `summary.json` 仍表示最后一次 driver；完整边界在 `session-summary.json`。独立九格 PUT 资源阶段随后执行，既不加入以上主矩阵耗时，也不增加主结果重复次数。

## 5. 验证记录

对当前已采集原始正式矩阵、构建、环境、编排烟测及本地操作计时快照执行成功，JSON与CSV行数一致、step id唯一、running/pending结束及耗时为null。

另用临时合成证据验证：带+08:00的时间转UTC、wall纳秒精度、SDK窗口与命令墙钟分离、失败后重试、旧失败结果保留、尚未开始的plan行、部分写入JSONL告警、关闭估计标签。测试不启动服务或构建产品；正式压测进程未受该工具改动。

## 6. 停止后的恢复证据归档

`scripts/experiments/kvc-fair/prepare_resume.py` 为独立的 legacy suite 归档工具。默认只检查并输出计划；`--apply` 才提交归档。工具不停止进程、不清理 cgroup、不启动 suite、不执行输出的恢复命令。本轮在首轮停止且四个 cgroup 排空后，已实际执行 dry-run 与 apply；随后另行启动原 driver 的原参数命令。

```bash
python3 /home/worktrees/kvc-fair-20261020/prepare_resume.py \
  --root /home/worktrees/kvc-fair-20261020 \
  --suite /home/worktrees/kvc-fair-20261020/formal-001 \
  --approved-change 'lmcache/start_server.sh=人工授权：启动前切换到自身目录，修复相对路径导入失败' \
  --invalidate-cell r0-mooncake-s65536-c8 \
  --invalidate-cell r0-mooncake-s1024-c8 \
  --invalidate-cell r1-lmcache-s1024-c8
```

检查通过后，同一命令增加 `--apply`。上面三项为停止后的全量原始事件确认的相交 cell；审计保存在 `interference-audit.json`。`--invalidation-reason` 记录采集动作与前后2秒缓冲规则。真实首轮归档复制418,810,898 bytes，manifest SHA256 为 `bd00a84cdb975abca3942d839736ad039f4c834700038efca9e13105d30eb69a`，10份旧有效接口结果保留原字节并标为采集干扰排除。

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

## 10. 补充 PUT 资源阶段

主矩阵补跑成功后执行九个独立资源窗；本阶段使用 `profile_put.py` 与 `light_monitor.py`，不加入三重复性能统计。`collect_timings.py --put-profile <目录>` 单独记录其 suite/command/SDK 边界；新驱动额外保存 pre_stop/start/stop 的 command_start/end，因此可得到精确生命周期命令墙钟，仍与 cgroup 排空、固定等待和 SDK 窗口分开。轻采样器保存实际采样间隔、读取计数和含 JSON/写入的循环工作耗时分位数；不能用名义 10 ms 推断实际观测频率。

## 11. 本轮结束后的实测补充

PUT专项完整阶段180.271725s，九格成功。实际采样周期p50 9.999886ms、p95 10.033072ms、最大15.581001ms；读取成本p50 2.312840ms、p95 2.493521ms，含JSON/写入循环工作p50 2.604287ms、p95 2.785132ms。观察者平均约0.2611个CPU核，不能称为零侵入；不参与主性能三重复。

结束恢复观测/命令合计0.223150s，其中systemctl start为0.019473s。首轮原生工具iostat/mpstat/pidstat/dstat均退出0，补跑也均为0；两次perf均以SIGINT收尾（-2），原始日志持续到结束。samples累计5731→7074，dstat.csv累计547551→674233 bytes，归档前缀一致；后处理不将历史副本再叠加。

大日志未压缩取证被主动停止后以rsync -az重试，两个尝试均保留UTC/单调时钟与退出码；发生在所有测量停止后，无需作为SDK干扰排除。最终耗时CSV为打包前完成步骤快照，后续发布/浏览器验收命令另保留在操作ledger与验收证据中；CSV截止于生成时刻，后续步骤由操作ledger记录。

## 12. 交付耗时

停止后的压缩取证同步34.632646s，最终报告生成25.509673s；公开浏览器完整验收143.474242s，均为本机monotonic命令墙钟。发布、提交预览和文档/ZIP刷新等步骤见operations.jsonl，第一行是delivery_ledger_snapshot元数据，后续行才是操作。每次刷新记录cutoff与各原日志SHA，不给该次刷新自身或后续操作补造耗时。最终网页的timings.csv是打包前1192项分层记录快照，与后续交付ledger分工明确。

## 13. TCP 协议预检、修订和恢复耗时

传入 TCP suite 目录即可自动识别以下记录，不新增 CLI 参数；原 `tcp_workflow.py collect` 完成取证后，`report` 阶段使用同一个采集入口。采集只读本地已收集文件，不访问目标机、不执行协议门禁、不运行压测或恢复操作。

| suite 内的源证据 | 计时与状态口径 |
| --- | --- |
| `protocol-history/attempt-NNNN/summary.json` | 每个失败/历史协议轮次独立一行；UTC 来自 `start/end_wall_ns`，若同一原进程的 `start/end_mono_ns` 齐全，耗时用两者差值，否则保留原 `duration_s`；无结束时不补耗时 |
| `protocol-evidence/summary.json` | 当前已收集协议门禁的整体区间，口径同上；结束后按原 `valid/fatal` 标注 completed/failed/unknown，不因后续重试成功而改写历史失败 |
| 上述各目录的 `operations.jsonl` | 每条真实命令分别保留 argv、开始/结束、已记录的 monotonic 墙钟和退出码；有结束但退出码缺失为 unknown，无结束时 duration/end 为 null；不完整 JSONL 行进入 `issues[]` |
| `preflight-changes.json#/revisions` | 包含修订前校验、失败轮归档、helper 复制和部署权限修正的实际修订区间，结束点早于最终 audit JSON 发布；`changes=[]` 但有权限变化的修订仍保留；不把外层命令与本区间相加 |
| `failed-preflight-history/*/failure.json` | 失败/回滚凭据单列；只有结束时刻而缺少开始/耗时的记录保持相应 null，不从目录名或归档时间推断 |
| `linked-master-recovery.json` | 本轮凭据只记录 wall 起止，使用二者差值并标 `target_wall_clock`，不冒称 monotonic；精确 PID 检查、SIGTERM 和 cgroup 核验观察保留，不补造逐 PID 耗时或退出码 |

这些行使用 `local_operation` 以兼容现有 ECharts 过程时间线，并分别带 `process_stage=protocol_gate/protocol_command/preflight_correction/preflight_correction_failure/guarded_recovery`。每个源路径、attempt 和 revision 的 ID 独立，显式 `--operations` 与自动发现同一个 JSONL 时仅收一次。CSV 同样保留上述归属字段；全部行的 `additive=false`，协议整体、内部命令、修订与外层操作可能嵌套，不能直接求和。它们不进入 `sdk_window`、接口样本数量、QPS/P99 或产品编译时间。

归档轮次中没有结束记录的命令标为 `interrupted_unknown_end`，当前轮次无结束的命令标为 `running`；两者的未知结束和耗时都为 null。已有明确错误/非零退出码时保留 failed，不因缺少结束时间而覆盖失败事实。

若 suite 的 `tcp-stage.json` 明确声明 `source_builds_reused=true`，`builds/` 中的原始 UTC 与耗时继续保留，并标为 `reused_build/reused_build_total/reused_native_build/reused_build_attempt`、`reused_source_build=true`。这些是先前固定源码构建的来源证据；本次 TCP 运行没有重新编译，不能把旧构建起止挪到 TCP 阶段或统计成 TCP 编译耗时。原生 suite 没有此声明时维持原有构建分类和数值。

本节说明新增采集口径；TCP 正式矩阵已完成，实测阶段耗时见第 14 节。离线回归命令：

```bash
cd scripts/experiments/kvc-fair
python3 -m unittest -v test_collect_timings_tcp.py
```

六个用例覆盖历史失败与当前成功、monotonic 与 wall 不同的区间、权限变化而内容不变的修订、缺少结束/退出码/开始的记录、损坏 JSONL 尾行、按源去重、wall-only 恢复、构建复用、CLI 自动识别及 JSON/CSV 一致性；临时 fixture 源文件前后 SHA 相同，未使用产品进程或目标节点。

另在已保存的本地原生证据上与修改前采集器逐项对照，1213 个 step 字典完全一致，涵盖 ID、UTC、耗时、状态、分类、时钟和 session 元数据。该数值属于这次本地回归快照，包含后续操作 ledger 条目，不改写第 12 节交付打包时的 1192 行截止口径，也不表示新增接口性能样本。

## 14. TCP 已完成阶段的实测耗时

以下只属于独立 TCP 实验；第 11、12 节保留原生基线交付时的记录，不能相加为 TCP 耗时。

| 阶段 | 实测耗时 | 证据及口径 |
| --- | ---: | --- |
| 最终成功协议门禁 | 47.383406466 s | `protocol-evidence/summary.json`；前五轮失败与修订在时间线另列 |
| 共同编排 smoke | 124.410003 s | smoke summary 的 UTC 整体区间，6 cell / 18 结果 |
| TCP 主矩阵 | 7224.817467 s / 120.413624 min | UTC 2026-10-19 20:41:54.554432 至 22:42:19.371899；81 cell / 216 结果，单个完成 session |
| 独立 PUT 资源专项 | 213.440114 s | UTC 22:42:44.441697 至 22:46:17.881811；9 cell，不加入主性能三重复 |
| 目标机恢复观测及命令 | 0.223977150 s | `environment-restored.json` 的目标机 monotonic 区间；SSH 外层命令另外计时 |
| 取证收集主入口 | 40.690383669 s | 本机命令墙钟；之后同步当前协议目录另用约 3.672 s，原 ledger 保留未取整值 |
| 首次报告生成主入口 | 53.289804743 s | 成功生成后发现页面重复嵌入完整协议证明过大，保留该次成功工程操作记录 |
| 紧凑证明绑定后的报告入口 | 43.617902926 s | 完整协议证明独立保留；页面用精确 SHA256/字节数绑定摘要，不改原始样本 |

主监控有 7217 个 1 秒调度样本；专项有 21192 个轻量样本，实际间隔 p50 9.999999 ms、p95 10.001185 ms、最大 40.048335 ms。专项读取计数成本平均 2.322780 ms，含 JSON/写入循环工作平均 2.619221 ms；观察者约占 0.260655 个 CPU 核，不称为零侵入。

本版 `timings.json/CSV` 为第二次报告打包前的 1154 项分层快照，`issues=[]`。后续独立归档验收、浏览器验收、发布与交付刷新由 `operations.jsonl` 保留原命令 UTC、monotonic 耗时和退出码；快照首行记录截止时刻及源日志 SHA。嵌套工程命令不能与内部 SDK 或阶段耗时直接相加。
