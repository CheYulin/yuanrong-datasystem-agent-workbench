# 1. 重构实施与逐阶段验收

**Status: In-Progress · 2026-10-21**

用户已要求按照设计实施重构，并刷新最终测试运行结果。本文件持续记录真实实施与验收，不用原有测试成绩代替重构后的测试，也不将 P1 完成等同于整个目标完成。

## 2. 当前范围

| 阶段 | 状态 | 实际内容 |
| --- | --- | --- |
| P0 | 已完成设计 | 五个主模块、142 项基线迁移表、W01–W18 中间结果和独立验收契约 |
| P1 | 已验收 | core/platform/build 包、回执与只读复核、显式源码包；部署/源码快照/ZIP 接入嵌套模块；完整回归与干净包通过 |
| P2 | 进行中 | P2a 已迁移 metadata/catalog/stage 与配置归属；构建服务、实际新构建及 workflow 接入待完成 |
| P3 | 待实施 | 运行/资格/epoch/worker/environment 与 observe 分离及真实产品 smoke |
| P4 | 待实施 | 原件收集、离线 reader/analysis/audits、来源链与逐步验收 |
| P5 | 待实施 | 报告模板/文档/资源/复现包、独立交付与浏览器验收 |
| P6 | 待实施 | 旧入口退出、引用迁移、干净包验收与完整新实验及最终报告刷新 |

## 3. P1 实际中间结果

新增代码在工具目录 `kvcbench/{core,platform,build}/`；入口与已实现命令见 [commands.md](../../scripts/experiments/kvc-fair/docs/commands.md)。没有空目录占位或将未来 build/run 命令显示成已经实现。

回执实际包括开始、输入、结果、清单、验收、摘要、最终 commit 和验证源码快照。运行状态、验收结论和原生失败分别保留；外部 payload/权限或上游来源改变会使复核拒绝。硬退出只保留未提交 attempt 和未知活性，不依据文件记录自动重新启动测量。

工具包中间结果为 `payload/`、`manifest.json`、`bundle.zip`、`archive.json` 和独立 `verify.bundle` 回执。该回执只验收完整代码包，不宣称 W01–W18 已接通或产品已经重新构建/压测。

正式 batch/TCP stage 接入 package 安装，冻结输入覆盖嵌套模块；native 入口复制 package，并将其纳入运行源码 hash 与后续快照。报告复现 ZIP 对新源码使用完整明确清单，旧包布局保留兼容分支。

## 4. 实际验证与边界

工作区外的本轮证据根为 `<verification-root>/2026-10-21-kvc-refactor/`，包括原始测试日志、目标节点只读基线、工具包、独立验收回执与干净解压结果。结构化阶段结论为 `phase-p1-result.json`，绑定原始检查日志、工具包与只读验收结果的 hash/size，并明确 `full_goal_complete=false`、下一阶段 P2。

- 修改前基线：`python3 -m unittest discover -s . -p 'test_*.py'`，465 项通过，47.020 秒。
- 首轮 contract：`python3 -m unittest discover -s tests/contract -p 'test_*.py' -v`，26 项通过，1.998 秒。
- 首轮含原有用例的全回归：491 项通过，47.873 秒；后续修订以最终 498 项结果为准。
- 最终修订后全回归：`python3 -m unittest discover -s . -p 'test_*.py'`，**498 项全部通过，49.885 秒**。上述为 unittest 自报耗时，不冒充整个命令启动至退出的 wall。
- 干净解压：从包中运行 `env -u PYTHONPATH PYTHONNOUSERSITE=1 python3 -S -m kvcbench --help` 与同样环境下的 contract 用例；32 项中 31 通过，1 项仅检查 Git 索引，在无 Git 的解压目录明确跳过，2.088 秒；ZIP/payload 清单另行核验，不以该跳过代替包完整性验收。
- 151 个源码/资源文件的干净解压 mode 全部与 manifest 对应；最终 `p1-bundle-005` 与提交前工作区的全部 151 个文件字节相同。ZIP SHA256 为 `2d2654b2d332fc0854ec2332449c723f9a13da520297dca64348b4ad2dfd4261`。
- `python3 -m kvcbench verify-step --commit <bundle-005-step-commit> --out <new-check-006>` 实际通过；保留新的验证代码快照、输入绑定和摘要。移动证据根目录使用显式 map-root，不改写原回执。
- 独立 `datasystem/test_runtime_safety.py --out <new-safety-output>` 通过：自有进程终止、身份不符的进程存活、启动参数和独立 Worker 目录保护成立；此程序启动的产品进程数为 0，不能解释为真实双 Worker smoke。
- 目标节点只读检查：三个实验 cgroup 成员均为空，未发现 DataSystem Worker、Mooncake Master、LMCache 服务或 batch runner 残留；这仅证明检查时刻状态，不作为下一次开测的永久准入。

端到端实跑曾发现只读检查入口的局部变量遮蔽，已修复并补真实 CLI 成功/篡改拒绝回归；之前包与失败输出保留为不同尝试，不覆盖。P1 源码包回执只证明其声明的完整性 scope，不把源码快照复制耗时当作全 ZIP/发布命令耗时。

## 5. P1 工程自检

- hot path：未改 SDK 测量循环、调用次数、拷贝、锁、预热或批量语义；新增验收与 hash 在控制面执行，实际产品性能尚待新实验。
- 并发与清理：共享 flock inode、原有进程身份检查和 cgroup 排空机制保留；新模块接入由 fixture 和自有子进程验证。
- 证据与恢复：原始实验不修改；source ZIP、实际权限、上游来源和失败回执独立保留，篡改/中断/根目录迁移有负例。未执行断电持久化或真实产品故障恢复实验，不据本阶段推断这些性质。
- 上下文：未修改 DataSystem 产品模块/构建规则/产品测试路径，工具 docs/RFC 已更新，无需更新产品 `.repo_context/`。
- 提交前执行 `git diff --check` 与 `git diff --cached --check`，检查 staged 范围仅为 KVC 工具与本 RFC/索引；没有将日志、ZIP、二进制或目标机快照提交到仓库。

本阶段尚未改变产品构建参数、SDK 请求循环或原始 R7 结果，未向目标节点写入新实验。最终新的测试矩阵、资源曲线和网页结果必须待后续阶段实际运行后产生。

## 6. P2a 构建记录与部署归属

- 原 build_metadata 实现迁入 `kvcbench/build/metadata.py`，固定 tag/SHA/配方/产物布局数据迁入 `configs/versions/kvc_20261020.json`；旧路径为兼容入口，不再复制实现。
- 原 builds-only 的来源验证和快照归 `build/catalog.py`，锁归 `platform/locks.py`；运行 stage 归 `run/stage.py`。stage 通过显式同步 provider 使用构建和代码安装能力，core/platform/run 不反向导入 build/collect/report/diagnostics。
- 旧参数组合归 `compat/commands.py`，消除 stage 与构建辅助脚本互相导入；静态配置与包一起安装，仍保留新目录、模式冻结、原 inode、独立 Worker 目录和原生协议。
- 重点构建/部署/回执/包用例：56 项通过，6.057 秒；整套回归 **498 项通过，49.194 秒**。160 文件完整包实际通过校验，干净解压后的 metadata 与 builds-only CLI help、模块依赖检查通过。
- 证据：`p2-stage-provider-tests.log`、`p2-ownership-all.log`、`p2-ownership-bundle-001/`、`p2-ownership-extracted-001/`。新包只验收该 scope，不表示三产品已新构建或正式运行。

目标节点检查确认：系统 Python 为 3.9，三个 SDK 使用的 3.11 工具链与系统解释器需要明确区分；编译器和观测工具已有，源码版本继续冻结于本次对比版本。现有缓存只有原始旧格式构建记录，缺少 rich source-build metadata 所需字段，不能把它们补写为一次新构建成功。后续新构建需要显式共享 Python 路径和真实开始/结束/退出/产物记录；旧数据继续只读。

P2a 结束时尚未启动本任务的新编译或压测。P2 后续状态以第 7 节为准，不能将旧检查当成持续运行证据。


## 7. P2b 构建步骤的中间结果

构建配方和依赖锁迁入 `kvcbench/build/recipes/<product>/`；旧路径保留临时转发入口。新增 `build/service.py`，使用显式主机配置、共享 CPython、编译器和独立构建 cgroup，从全新根目录执行固定版本配方。Mooncake 发布信息按固定 tag 获取，避免 latest 随时间变化。旧回顾性摘要不参与新构建成功判定。

- W01 固定 source-build 配置及实际执行的 package/配方身份；每个 W02 产品子步骤绑定实际命令、日志、构建记录和原生产物。完整实验计划 W01 尚须在后续模块接入，不能将本 source-build scope 扩大为整个实验。
- 每条命令独立保存 argv、工作目录、PID、起止 wall/monotonic、耗时、退出和异常；不自动重试。构建 cgroup 仅操作本次创建的域，启动失败不会清理同名外部域。
- 每产品通过后独立封存；全部通过才生成 build-catalog。清理另存 build-cleanup，聚合 W02 同时检查三产品和资源排空。资源 peak 是累计构建域口径，不伪装为每产品单独峰值。
- 回归：`python3 -m unittest discover -s . -p 'test_*.py'`，505 项通过，47.447 秒（unittest 自报耗时）；其中新增 7 项覆盖配置拒绝、目录保护、非自有 cgroup、实际退出/日志/计时、超时停止、启动失败和禁止覆盖。
- 最终执行源码包含 168 个文件，`p2b-source-bundle-002` ZIP SHA256 为 `d0a54ed3ea50b9053eafb953e51a131dcb58ee348adfba5f8fbf915dd8825b46`，显式清单及独立 bundle 回执通过。
- 实际构建已在 `tiantiyun-80c128g` 的 `/home/worktrees/kvc-native-modular-20261021-001` 启动，执行代码来自冻结的 `code-p2b-002`。使用 8 个指定 CPU、32 GiB 内存、swap=0 的独立 `kvcbuild20261021modular001/build` 域；启动后已观察到 Mooncake 的原生 CMake 编译输出。

当前构建尚未完成验收；没有新的正式压测成绩。P2 配置物化、运行/观测、收集、分析审计及报告交付仍须继续。上述源码与控制面修改未改变 SDK 测量循环；构建进程并发、清理与中间结果绑定已检查，实际三产品构建能力以最终回执为准。未修改产品源码、产品构建规则或其测试路径，无产品 `.repo_context` 结构更新需求。
