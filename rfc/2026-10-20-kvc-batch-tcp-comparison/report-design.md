# 1. 批量报告与交付脚本设计

## 1.1 实际结果读取与独立重算

`batch_analyze.py` 只读取本地采集的 `parameters.json`、`plan.json`、`products.json`、`summary.json`、事件、客户端结果与 cgroup 样本。它独立核对三产品、四个 B、五个 API、三个互异轮次的完整网格，并核对每个客户端的身份、正常退出、预热/测量/关闭确认、固定 PUT 总量、GET 内容抽查与 UPSERT 更新后恢复。原始成功标志不能绕过这些检查。

正式协议为 `separate-read-and-fresh-put-v1`：每产品每轮先运行一个独立 READ epoch，常驻 key 池仅 65536；全部支持的读/更新/EXIST 完成后，每个支持 B 的首次 PUT 启动独立服务 epoch，重新预置同一热池。三轮共 9 个 READ epoch 和 27 个支持 PUT epoch，NA 不新增服务。PUT 预热固定插入并完整读回每客户端 4096 个独立 warm keys（全体 32768）；测前为 98304 keys / 96 MiB。测量再写 262144 新 keys，完成后为 360448 keys / 352 MiB；这些数据不进入下一 PUT 或 READ epoch。不存在 LMCache 大 B scalar 背景补齐步骤。

吞吐由全部完成 batch 数除以八客户端完整单调时钟窗口重算，keys/s 再乘 B。P50/P95/P99 从合并直方图重算，保留桶界；延迟属于整批 API，不除以 B。中位数、范围与 CV 只在三个计划轮次完整有效时生成。LMCache 不支持的原生 API 写为 N/A；未完成、失败和资源未知分别保留。

资源只取 SDK 窗口内真实样本。服务端与客户端是互斥 cgroup，CPU 与所选设备 I/O 使用窗口内首末计数器差分除以真实样本跨度；少于两个样本、计数重置或缺失返回 null，不向窗口两端外推。计费内存展示样本观测最大值，匿名内存加 SHM 另列，不重复加 file。每个结果保留样本数、覆盖率和真实间隔统计。

## 1.2 门禁、历史和源码来源分层

`batch_report.py` 在最终模式要求正式矩阵重算通过、同 stage/products 的十个真实门禁组件与 C8 smoke 分别成功，以及原始构建记录完整。`measured-runtime/` 只归档 `batch-stage.json` 已列出的输入，逐文件核对字节数与 SHA256，并在复制后和实际 ZIP 成员中再次核验。当前报告工具另存原 ZIP 的工具路径，不冒充原测量源码。

`batch_resource_audit.py` 通过独立后处理命令产生 JSON/Markdown，再由报告的 `--resource-audit` 接收。报告核对同一 suite、全部原始 sample/result 输入 SHA/字节数、SDK 窗口身份与单调边界、JSON/Markdown 一致性及实际 helper 源 SHA；不拼装新采样、不重跑负载。完整输出和 helper 源单独保存在 `resource-audit/`，页面只引用小摘要。throttle、memory.events 与 swap 的历史值和窗口增量分开；parent/monitor 不与 server+client 再相加，外部进程 CPU 只是采集器 leader-affinity 估计。

`--tcp-verification` 指向本批量门禁产生的 DataSystem 完整 TCP 审计。报告重新核对输入文件和解析器源码 SHA、十六个唯一组合及三十二个 PUT/GET 方向的结构、字节下限与同批 key 派生样本身份。完整审计 JSON 保持原字节外链；页面只含约束范围内的小摘要和文件 SHA/字节数，不内嵌大量 anchors，也不重新解析系统调用。每方向完整 1 KiB 样本与含 RPC 开销的 TCP 字节下限，不等于整批每个字节都被证明，更不代表正式性能窗口持续跟踪。

默认 r2 的失败门禁、无跟踪独立诊断、证据组合、失败 C8 smoke 与 default/8192 机制诊断分别通过 `--history` 保留。历史 JSON 和执行 helper 原件进入 ZIP，完整日志进入 raw tar。默认失败不算适配后主矩阵无效行，诊断与证据组合也不增加正式性能重复。队列原因尚未以明确 reason counter 确认。

`--failed-attempts` 可重复指定独立失败正式尝试的完整本地采集目录。目录须保留终止失败的正式 summary、同目录 stage、原 `runtime-inputs/` 和 run 日志。原件与逐项核验的历史冻结输入分别归档；历史八个成功行和后续失败行不会计入新主矩阵的重复。当前 R3 DataSystem 失败属于双 Worker 共用默认 RocksDB 目录的部署隔离遗漏，不据此判产品 API 缺陷。最终门禁还会核对两份原始 Worker 旗标、捕获的 live argv、PID/start-ticks 与独立实例内的非重叠 RocksDB 路径。

旧两份单 key 报告的 DataSystem 同样缺少显式 RocksDB 隔离，相关产品比较不作为公平基线，无法据不完整旧日志量化影响，也不把原接口成功记录全部改判为失败。页面所选 API 的 B1→B4096 比率只取本次同一冻结部署矩阵，不与旧 B1 合并。

## 1.3 页面生成

页面资源全部使用相对路径，包括 ECharts、数据、样式和脚本，可离线打开。桌面提供左导航，移动端提供目录按钮；章节编号、居中图注与资源窗口口径保持一致。九张图分别呈现拓扑、默认失败/配置阶段、能力矩阵、吞吐、整批延迟、资源、成本散点、资源时间线和主矩阵阶段时间线。

所选 API 的文字摘要只使用 B1 与 B4096 的完整三轮中位数，给出 keys/s 比率及所选整批分位数的原 µs；N/A 不推算比率。时间线保留原始边界，完整 suite、外层命令与 SDK 窗口可嵌套，不能相加当作总耗时。`--allow-partial` 仅生成明确标识的部分预览，不能替代最终验收。

```sh
batch_local=/home/t14s/workspace/scratch/2026-10-20-kvc-batch-tcp-r5
python3 batch_report.py \
  --suite "$batch_local/formal-batch-001" --out "$batch_local/report" \
  --rfc ../../../rfc/2026-10-20-kvc-batch-tcp-comparison \
  --builds "$batch_local/builds" --gate "$batch_local/batch-gate-001" \
  --smoke "$batch_local/batch-smoke-001" --environment "$batch_local/environment" \
  --runtime-inputs "$batch_local/runtime-inputs" \
  --history "$batch_local/history/default-r2" \
  --failed-attempts "$batch_local/history/shared-rocksdb-r3" \
  --tcp-verification "$batch_local/datasystem-batch-tcp-verification.json" \
  --resource-audit "$batch_local/resource-audit" \
  --operation-log "$batch_local/operation-timings.jsonl" --raw-evidence
```

示例从脚本目录运行；`--runtime-inputs` 应使用采集命令实际生成的目录名，不创建或替代冻结证据。输出必须是与输入分开的新空目录。上述命令是复现入口，不表示尚未执行阶段已经成功。

报告前单独运行 `python3 batch_resource_audit.py --suite "$batch_local/formal-batch-001" --out "$batch_local/resource-audit"`；输出目录必须是新的独立目录。若后处理 helper 源发生变化，原审计和源版本保留，使用新目录产生新审计，不能用事后源码冒充旧执行。

## 1.4 验收后文档刷新

`batch_refresh_delivery.py` 专供这份批量报告使用，默认 dry-run。它先验证现有 SHA256SUMS、所有文件与权限、ZIP CRC/成员，再在独立临时副本中更新文档和可选操作 ledger。独立副本避免暂存阶段对原核心文件产生硬链接写入。该成本只发生在测量结束后的本地交付阶段。

原 ZIP 中实际生成器源码、测量输入、完整证明与历史执行源码在原路径逐字节保留，连同 ZIP 成员属性一起核对。新 QA/交付工具以 SHA256 命名放入 `delivery-tools/`，manifest 明确它们是交付源码快照，不自动宣称已执行，也不覆盖原生成器。当前工具可通过重复 `--delivery-tool` 显式追加。

公开核心数据、图表 JS/CSS、完整 TCP 证明、历史文件和 raw tar 均冻结。HTML 只允许原始 UTF-8 字节中唯一 `reproduce.zip` 下载 href 更新为实际 ZIP SHA256 query，并同步 mtime=0 gzip；除此以外的 HTML 改动会被拒绝。新文档、ledger、工具和 ZIP 的校验通过后逐文件替换，SHA256SUMS 最后提交；中途失败恢复已替换文件。原始操作日志以刷新前截止点生成 ledger，不伪造刷新自身或后续步骤的耗时。

```sh
python3 batch_refresh_delivery.py \
  --out "$batch_local/report" \
  --rfc ../../../rfc/2026-10-20-kvc-batch-tcp-comparison \
  --operation-log "$batch_local/operation-timings.jsonl"
# 审阅 dry-run 后，以相同参数追加 --apply 落盘。
```

刷新脚本不连接节点或站点，不运行产品、压力测试或页面生成器。原始输入哈希、完整实测矩阵和统计结果保持冻结；最终浏览器与下载验收证据由单独执行记录证明。
