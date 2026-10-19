# 阶段自检与验收记录

## 1. 实验脚本与报告准备阶段

- 需求范围：源码构建、双服务 CPU 内存接口对比、CPU/内存/磁盘采集、阶段耗时与可复现报告；正式矩阵和独立资源阶段均已完成，预览或 smoke 不作为最终成绩。
- 代码范围：仅 agent-workbench 本任务 RFC 与实验脚本；DataSystem 产品工作区无 diff，不向 openeuler 推送。
- 热路径：公共 benchmark.py 在正式矩阵启动后冻结；启动 cwd 修复只改变 LMCache wrapper 的工作目录。新增续跑、归档、后处理与浏览器检查均在测量热路径外。
- 所有权/并发：suite 排他锁、客户端 SDK 独占、测量/后验校验/关闭屏障；PID 与启动时间校验；恢复前要求 cgroup 排空，原始会话先归档后重测。
- 状态与恢复：不改变产品持久化/恢复机制；仅验证实验记录的事务式保存与中断续跑。不能据此宣称产品故障恢复能力。
- `.repo_context`：产品结构、职责、构建路径和测试路径未改，不需要更新产品上下文。

| 实际验证 | 结果与边界 |
|---|---|
| `python3 -m compileall -q scripts/experiments/kvc-fair` | 通过 |
| `node --check scripts/experiments/kvc-fair/report.js` / `qa_report.mjs` | 通过 |
| `git diff --check` | 通过 |
| `python3 .../lmcache/harness_correctness.py --kind fake --output <临时目录>` | 四接口机制与主动错误字节拒绝通过；rankable=false |
| 新 driver provenance 临时 fixture | 26 项通过，参数/配置/脚本变更拒绝，旧 metadata 保全；未接触远端 |
| legacy prepare_resume 临时 fixture | 14 项通过，含 generic 根目录相对 native 路径、归档不覆盖、哈希、精确 cwd 变更、重命名中断恢复与分 session 计时；未接触远端 |
| 源码构建与真实 smoke | 三产品 build_exit=0/smoke_exit=0；具体命令、SHA 与日志见 build.md/分产品文档 |
| 隔离浏览器 `qa_report.mjs` | 真实部分矩阵与耗时证据，1440/900/390 px；保留159项后扩充至212/212。新增完整身份、CSV/raw字段与全组中位检查的临时 fixture 51/51；真实100条raw绑定通过。无 profile 时明确等待，真实完整矩阵/profile/公开页面正向验收待执行 |
| 临时报告生成与 SHA256/ZIP CRC/成员检查 | 实际部分矩阵生成成功；脚本 ZIP 含 DataSystem driver，不含源码树、venv 或 bytecode |
| verify_results 临时 fixture | 15 项完整矩阵/篡改拒绝与 CLI 检查，加 11 项真实协议参数/清单检查通过；正式全量 gate 已执行，结果见下节 |
| verify_results 可选 PUT profile gate | 11项临时JSON检查通过，复用逐接口校验9次，主216/81与资源输出保持独立；身份、计数、purpose、少采样与时间回退拒绝；真实专项9/9通过，结果见下节 |
| capture_evidence 停止状态 fixture | 停止后快照、重复读取不覆盖、占用 cgroup 拒绝通过 |
| 发布注册的离线 fixture | 恰一入口、保留其他条目、重复执行不加重复入口通过 |
| profile_put / light_monitor 本地 fixture | 11 项与模拟九个 PUT-only 用例通过；未在主压测期间部署或运行 |
| 网站独立提交 fixture | 22 项通过，保留既有 dirty 字节、只收录本页注册与报告、拒绝并发/已有 staged/错误仓库；未操作真实站点 |
| 后处理异常采样 fixture | memory.events缺失/内部回退等6项、报告Python6项/JS7项/IO2项通过；未知保持null，正常资源值不变 |
| 历史计时边界 fixture | 归档旧summary检测5项通过，原14项恢复验证仍通过；未知结束不重复采用前一session边界 |
| 最终环境恢复 fixture | 10项通过，含占用拒绝、仅恢复原停unit、失败仍留after证据、重复不再start、大归档只stat；后续已在目标执行，结果见下节 |

## 2. 正式测量与证据验收

- `verify_results.py --formal --interference-json ... --put-profile ...`：passed=true，216/216当前接口结果与9/9独立PUT资源结果通过，errors=0；81cell、72三重复组，原始桶和计数重新合并校验。
- 唯一资源告警：20个PUT窗不足两个1Hz样本；evidence_complete=false，如实保留，未将缺测写成0。GET/EXIST均有足够样本。
- 当前216个SDK窗口与两次已知取证动作±2秒交集0；9次历史启动失败和10份干扰旧结果保留。
- 首轮归档和构建/脚本快照1,999个文件SHA256重新核对通过；原始资源JSONL无畸形行，7,074个时间戳唯一，memory.events无计数回退。
- `collect_timings.py`：两session边界、226个原始SDK记录（含10份干扰旧结果）、九格专项分离，初版1191条step id唯一，issues=0；最终打包前重生成1192条以纳入已完成验证耗时；生成及交付操作另见ledger。
- `finish_environment.py --restore`：原记录unit恢复命令成功，after为active/running；全实验cgroup空。源码tag SHA不变，DS仅多出未跟踪.venv/，其余源码完整工作树clean；旧归档stat大小匹配。
- fresh-repro metadata的DS已知.venv例外：10项临时Git验证通过，tracked修改/未知untracked/SHA变化仍拒绝；full工作树clean标记不伪造。
- 文本日志敏感模式文件名扫描无匹配，仅是模式检查，不宣称穷尽检测。

- `refresh_delivery.py`：10项临时fixture通过，原SHA篡改拒绝、core/gzip头/权限不变、ZIP/docs一致、失败回滚与ledger去重；实际首次刷新核心261个文件未变。

## 3. 最终页面与交付验收

完整216条+九格真实profile本地严格验收369/369通过（1440/900/390 px），225/225原始结果绑定、9×3资源指标×3视口共81项逐点校验通过，另有当前筛选最大重复跨度24/24通过。最后视觉调整另以三视口及1440→390实际resize检查108个散点与各自柱中心对齐，最大误差2.84e-14px，重绘随后稳定，无JS错误或页面横向溢出。公开严格369/369、重复跨度24/24及108个柱点几何/实际resize检查通过；公开JS/data/CSS/CSV字节与本地源一致，页面无JS例外或必需网络失败。共享auth/status=200、comments=200、受保护admin/pages=401单列为可选功能。站点统一HTML注入/共享资源query改写另留原始差异，不把传输HTML冒充源字节相同。公开完整验收耗时143.474242s，rc=0。产品C++ UT/ST、故障恢复和推理性能测试未运行：本任务未改产品逻辑，验证范围为锁定发布版本的实际接口矩阵，不能用本任务harness替代这些测试。

## 4. 统一 TCP 实验预检阶段自检

此节为追加实验，不改写上面的原生基线。DataSystem 六组逐操作 TCP 证据已通过；三产品两端点真实协议门禁均通过，47.383406466s；正式性能矩阵仍未完成。所有失败门禁原字节、修订 before/after SHA、独立操作耗时与权限修正保留。

实际执行 `python3 -m unittest -v test_tcp_workflow.py test_tcp_retry.py`：61/61，通过耗时13.034s；覆盖锁、空 cgroup、原始档案、核心输入拒绝、复制权限恢复、仅权限事务、失败回滚及中断窗口。`python3 -m compileall -q scripts/experiments/kvc-fair`、`node --check scripts/experiments/kvc-fair/report.js`、`git diff --check` 通过。DataSystem v3 raw parser 的23项自检通过，独立 verifier 补充16项临时传输端点/权限审计 fixtures 通过。

热路径保持共同 benchmark 和三个适配器冻结；修订仅涉及预检观测及 Mooncake 同一已构建 ELF 的启动与执行位。严格 PID/start_ticks/pidfd 停止器未放宽。新的 cgroup 与全局 flock 继续约束并发，权限事务的提交点/回滚通过负测。原始 stage 不覆盖；失败预检不充当产品接口错误或有效性能结果。产品源码及构建/测试路径未改变，`.repo_context` 无需更新。产品故障恢复、C++ UT/ST 和推理性能未测试。
