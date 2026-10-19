# 阶段自检与验收记录

## 1. 实验脚本与报告准备阶段

- 需求范围：源码构建、双服务 CPU 内存接口对比、CPU/内存/磁盘采集、阶段耗时与可复现报告；正式矩阵仍在执行，不将预览或 smoke 当作最终成绩。
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
| verify_results 临时 fixture | 15 项完整矩阵/篡改拒绝与 CLI 检查，加 11 项真实协议参数/清单检查通过；正式全量 gate 待执行 |
| capture_evidence 停止状态 fixture | 停止后快照、重复读取不覆盖、占用 cgroup 拒绝通过 |
| 发布注册的离线 fixture | 恰一入口、保留其他条目、重复执行不加重复入口通过 |
| profile_put / light_monitor 本地 fixture | 11 项与模拟九个 PUT-only 用例通过；未在主压测期间部署或运行 |
| 网站独立提交 fixture | 22 项通过，保留既有 dirty 字节、只收录本页注册与报告、拒绝并发/已有 staged/错误仓库；未操作真实站点 |
| 后处理异常采样 fixture | memory.events缺失/内部回退等6项、报告Python6项/JS7项/IO2项通过；未知保持null，正常资源值不变 |
| 历史计时边界 fixture | 归档旧summary检测5项通过，原14项恢复验证仍通过；未知结束不重复采用前一session边界 |
| 最终环境恢复 fixture | 10项通过，含占用拒绝、仅恢复原停unit、失败仍留after证据、重复不再start、大归档只stat；尚未在目标执行 |

以上检查仅证明对应脚本、记录和页面行为。最终全矩阵 gate、原始监控连续性、干扰排除后的结果身份集合、公共页面及恢复环境将在完整测量结束后补录。
