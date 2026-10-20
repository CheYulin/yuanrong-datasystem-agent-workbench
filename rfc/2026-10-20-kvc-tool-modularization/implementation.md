# 1. 重构实施与逐阶段验收

**Status: In-Progress · 2026-10-21**

用户已要求按照设计实施重构，并刷新最终测试运行结果。本文件持续记录真实实施与验收，不用原有测试成绩代替重构后的测试，也不将 P1 完成等同于整个目标完成。

## 2. 当前范围

| 阶段 | 状态 | 实际内容 |
| --- | --- | --- |
| P0 | 已完成设计 | 五个主模块、142 项基线迁移表、W01–W18 中间结果和独立验收契约 |
| P1 | 已验收 | core/platform/build 包、回执与只读复核、显式源码包；部署/源码快照/ZIP 接入嵌套模块；完整回归与干净包通过 |
| P2 | 进行中 | source-build、独立重验目录、显式运行 spec、W01–W04 计划/复用/代码包/部署验收已接入并通过真实节点检查；完整实验仍待下游闭环 |
| P3 | 进行中 | Python 产品 runtime/adapter 归整，W01–W11 编排已实现，新的三产品 gate 通过；观测 smoke 与正式矩阵正在实际验证，旧入口与测试归整仍待 P6 |
| P4 | 进行中 | 原件 snapshot/index、analysis/audits 与来源复核已迁移，历史原件重算一致；完整 W12–W14 准入与实际新实验审计待完成 |
| P5 | 进行中 | 新 report 消费者、ECharts/文档/离线包已实现且浏览器/干净包验证通过；上游 report-inputs 自动生成与 W16–W18 仍待接入 |
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


## 8. P3a 运行、观测与离线归约的依赖拆分

- scalar/batch worker、资格验证和三个 batch 适配器迁入 run；独立的 plan 和 runner 管理 epoch。旧入口仅作临时转发，后续 P6 收束。六份迁移实现共 36 个函数/类定义已与迁移前源码逐一定义比较 AST，结果完全相同；SDK 测量循环、调用和校验逻辑未改写。
- 原生结果完整性校验归 core/case_validation，直方图归 core/statistics；在线只读错误继续策略不再导入 batch_analyze。资源采样归约归 collect/resources，batch_analyze 不再导入 report。run 的观测会话 factory 由调用方显式提供，不反向导入 observe。
- environment、host、interference 和 light 采样归 observe。新增 suite 会话与只读 validation：ready 是独立首样本/启动快照；final 在所有自有采样进程退出后绑定已关闭文件。正常 runner 已使用该会话，在必需采样未就绪时阻断测量；失败依然写 summary 并排空资源。
- 首轮全回归发现 9 个 DataSystem adapter fixture 仍通过动态旧文件加载并 patch 薄 facade，未命中 canonical 函数全局；测试改为直接验证 canonical adapter，保留原有原生调用和失败语义断言。该失败日志 `p3a-all.log` 保留，没有弱化校验条件。
- 修订后全回归 `python3 -m unittest discover -s . -p 'test_*.py'`：509 项通过，49.986 秒（unittest 自报）；后续新增只读 validation 与篡改拒绝检查的重点回归 89 项通过，1.134 秒。四项观测会话契约覆盖 ready 后追加采样、停止封存及篡改拒绝、第二进程启动失败清理、必需工具死亡和已有目录保护。
- 真实节点执行源码冻结于 `code-p3a-001`，包包含 189 个文件，ZIP SHA256 `f916b59e9072b8d847cb3debff53a5d8ed5f48c9b96608a393abd6c6f1f293d5`。在持原 R7 suite.lock、所有实验角色为空的前提下执行观察 smoke，实际 4.659130967 秒；ready hash 在持续采样后不变，正常停止后 cgroup 再次为空。证据为 `observe-smoke-001/` 与 `observe-smoke-001-result.json`，原始文件收回后使用独立 validator 核验了 11 个封存文件。
- 观测 smoke 仅验证采样链路；构建同时在另一个域运行，不将此样本解释为产品性能或正式矩阵资源覆盖。Mooncake 实际构建退出 0，metadata identity_valid=true；DataSystem 构建仍在执行，全部三产品的聚合验收尚待终态。

P3 尚未完成：产品 runtime/现场环境与恢复还需迁移，W07–W11 的业务回执仍须由总编排逐步绑定，不能以 ready/final 文件存在代替全工作流验收。P4/P5/P6、真实三产品资格/正式矩阵、刷新报告和公开交付继续保留在目标内。共享锁、进程身份、写请求不重放和历史原件不改写的边界保持；未修改 DataSystem 产品代码，无产品上下文结构变更。


## 9. P4a 收集、分析与独立来源复核

迁入 collect 的实现包括 batch/scalar 分析、epoch/resource/deployment/TCP/integrity 审计、停止后 snapshot、timings；原报告中的 runtime/build、gate/TCP 和后处理来源核验分别迁入三个审计模块。确定性 payload 和 source-state 规则归 core；collect 不导入 build/run/report，build 版本信息通过明确参数提供。根目录暂时保留兼容入口，P6 清理尚未完成。

新 `collect/index.py` 接收明确、排序、唯一的 path/role 清单，将原始文件 SHA/size/mode 保存至全新 payload；只读 verify 检查精确文件集、字节、权限和符号链接。选定的 W01–W11 关闭回执可作为源，W12/后续派生目录与活动状态索引不能混入；复制中变化留下 failure。正常 terminal_suite 要求完整执行、正常停止监控和已保存排空，原生 failed 仍可作为终态证据，不能用清理未知放行。实际持锁/现场状态/恢复/上游回执准入仍须由 workflow 接入，不以索引通过冒充完整 W12。

来源声明改为绑定实际 canonical 模块及依赖闭包。新 TCP/resource/deployment schema 分别为 3/2/4；旧 2/1/2–3 仍按原格式读，并要求显式提供其原始 helper 文件，旧归档不改写。相关 fixture 改为新路径和完整闭包，同时保留错误来源、篡改、缺项、重复和旧 schema 拒绝/接受条件。

- 最终 `python3 -m unittest discover -s . -p 'test_*.py'`：**516 项通过，51.691 秒**（unittest 自报），日志 `p4-complete-all-008.log`。迁移初轮的来源路径失败日志、误选不存在测试模块的命令日志均保留，不能列为通过。
- 对历史 R7 原件作只读分析回归：counts/issues/rows/aggregates/epochs 五项与原 independent-formal-analysis 完全一致，仍为 132 valid /18 failed /30 N/A；27.050262513 秒，证据 `p4-real-evidence-regression/regression.json`。这不是新的 runtime 结果。
- 将历史 180 个 case 与 5 个计划/终态文件制作独立 index 快照，185 文件通过复核，0.17883294 秒；原件 valid=false 被保留。该快照不包含资源/全部现场材料，不能冒充完整 W12。
- 代码依赖检查通过；SDK 测量循环未改，变更位于构建、控制面与离线处理。没有修改产品代码或其上下文结构；并发/持久化风险落在明确文件清单、原子 JSON、失败现场保留和来源重核，不据单元测试推断产品恢复能力。

## 10. 真实构建失败与编译器输入修正

第一次真实构建的完整命令耗时：Mooncake 373.074 秒/退出 0，DataSystem 629.469 秒/退出 1，LMCache 152.385 秒/退出 1。DataSystem 首个错误为第三方 RocksDB 在 GCC 15 下缺少 cstdint；LMCache 扩展已构建，后置 probe 受工作目录同名 namespace 干扰。原始日志、metadata、失败 W02 及清理记录保留；独立 verify-step 确认绑定有效，同时 accepted_for_normal_use=false，未将失败改写为成功。失败 cgroup 已排空并移除。

LMCache probe 改用 `-I -B`，目标机实际复核加载了固定 source/lmcache/__init__.py，版本和 distribution 均为 0.5.6，三个 native 模块导入成功；原失败记录不改写。

第二次构建在新的 `...-002` 根目录进行。观察到第三方 CMake 将 CXX 解析后的 compiler path 单独转发，丢失追加的环境参数，RocksDB 仍报原错误；不能认为上一轮环境变量调整已修复。当前配方改用 `build/toolchain.py` 创建带固定 header preinclude 的可执行 driver，原始 GCC 15 的身份、wrapper 字节/权限/参数另有记录，不修改产品/第三方源码和运行功能开关。metadata 核对实际 RocksDB CMakeCache 是否使用该 driver；catalog、W02 和冻结 build provenance 绑定这些 compiler 输入。

目标机 `compiler-probe-001` 以实际 GCC 15 和显式 CMake compiler 参数完成 configure/build/execute，三个命令均退出 0，实际 1.028712926 秒；测试证明参数经过 executable driver 传递，不代表完整 DataSystem 已构建成功。两项 compiler 输入/实际 cache/篡改单测已包含上述 516 项。

第二次构建已终态：Mooncake、LMCache 通过，DataSystem 失败，构建域排空并移除。第三次以全新 `...-003` 根目录和 `kvcbuild20261021modular003` 域启动，使用 `code-p4-004` 冻结包；旧目录与失败不改写。完整 native catalog、P3 剩余环境/恢复、P4 工作流绑定、P5/P6、全新正式矩阵及站点交付都仍未完成。


## 11. P5a 已绑定中间结果的报告与离线交付

新的 report/inputs、render、model、documents、offline、relocate 分别负责输入组合、页面、展示投影、文档网页、离线包和位置映射。report 入口不导入 build/run/observe/collect；旧的混合报告流程明确保留于 compat 供历史格式复核。共用 inventory、SHA/gzip/操作账本和原始 tar 检查归 platform；上传与注册归 delivery，原来仅修改本地文档/ZIP 的 refresh 归 report 并拒绝对新 render-record 报告原地更新。

正常 report 要求 run/profile 一致、完整的 analysis/series/builds/workflow/evidence/audit、审计 SHA 绑定、完整固定矩阵和三个通过的构建记录。诊断预览保持 publication_eligible=false；完整原生失败矩阵仍保留 native_passed=false，失败图表不填零。MD 保留原件并生成互链网页，Mermaid/ECharts 全部本地资源，编号章节、左导航、居中图注与移动目录已实现。下载 URL 使用 ZIP SHA 版本参数。

离线 ZIP 包含代码、report/index.html 与全部本地阅读资源、输入原件和 portable template；relocate 仅新建相同文件身份的位置映射，不重写原 audit/analysis/evidence 字节。Unix mode 与 SHA 不符时拒绝重建；旧的 inplace refresh 对新不可变报告拒绝写入。上游自动生成 report-inputs 以及完整 W15–W18 业务回执仍须接入，不将手工布局 fixture 当成完整工作流。

- 全回归 python3 -m unittest discover -s . -p 'test_*.py'：524 项通过，48.840 秒，日志 p5-release-all-009.log。随后版本化 ZIP 入口与报告契约重点 23 项通过，1.203 秒。接口消费者覆盖错 run/错绑定、未验收阻断、完整原生失败保留、篡改拒绝、原始 audit 不改写及 legacy 刷新保护。
- 用历史 R7 原件制作明确标记的 diagnostic 布局页；未发布，不能作为新测试结果。真实 Chromium 148.0.7778.97 在 1440/390/320 三种宽度检查 9 张 ECharts、48 条 failed/N/A、移动目录、居中图注和 Mermaid，页面无整体横向溢出、无 JS 错误。EXIST 命中 DS 的 B=16/256/4096 聚合保持 null。p5-browser-layout-002 保留实际截图和检查结果。
- 干净解压后的 report 同样在三种宽度通过浏览器检查（p5-browser-offline-003），包括 1 张实际 Mermaid SVG，未以空图集冒充图形验收。
- 从实际解压代码 cwd 执行（清除 PYTHONPATH、关闭 pyc 写入）help、relocate、report 重生成均退出 0，实际命令耗时 0.114363981 /0.265265754 /2.822331619 秒。证据 p5-clean-command-check-002，每条 argv/cwd/退出/耗时均独立落盘。更早从工作区调用的检查只证明位置映射，未列为干净代码执行证明。
- 全部本轮检查属于工具、历史数据回归与页面交付能力；没有新的正式性能矩阵或公开站点交付。SDK 请求循环未改，所增处理均在控制面或离线；模块依赖检查通过。DataSystem 产品仓未修改，无产品上下文结构更新。

## 12. 第四次构建终态与缓存校验修正

第三次构建的 C++ header driver 已实际传递到 RocksDB；libiconv 的旧式函数声明在默认 C 模式下不兼容。目标机最小实际编译对比退出码为默认模式 1、GNU17 0，耗时 0.097051413 秒；C driver 和参数纳入 compiler-inputs，配置中的实际 CC 也要核对。

第四次原生构建命令全部退出 0：Mooncake 376.759765198 秒、DataSystem 965.342663636 秒、LMCache 74.125489483 秒。实际 artifacts 各 3 个，source/build 命令和 cleanup 原件保留，cgroup 排空并移除。Mooncake/LMCache 身份验收通过；DataSystem 只因旧校验器要求本地 RocksDB CMakeCache 而拒绝——该依赖已从缓存复用，本次实际主项目 CMakeCache 和 iconv config.log 存在。

当前 toolchain 校验已支持主项目的实际编译器配置及缓存复用，并有正/负单测；没有伪造缺失的 RocksDB cache 文件，也不修改第四次已封存的失败 metadata/回执。后续需以原始成功命令、原 artifact 哈希和新版只读规则生成新的 revalidation metadata/catalog，再让部署消费显式验收后的构建目录。构建命令成功不等于已有三个 passed 的构建目录，不在此阶段进入正式测量。

P3 剩余环境/恢复和统一资源锁、P4/P5 工作流绑定、P6 入口清理、实际资格/全新正式矩阵、最终报告及公开验收仍保留在目标内。

Mermaid vendor 从已使用的本地阅读器逐字节保留，vendor.json 记录 SHA/size；其 minified 模板字符串含尾随空白，.gitattributes 仅对这一文件关闭 whitespace 报错，不改写第三方字符串，其他源码继续执行完整 diff --check。

## 13. 构建重验的独立中间结果

新增 `python3 -m kvcbench.build.revalidate --root <native-root> --out <native-root>/revalidation/<fresh-attempt>`。它持原构建域锁，要求原命令成功、时间区间对应、构建已排空和移除，重新检查源版本、编译器和产物；新产物 hash/size 必须与原 metadata 一致。保留原 metadata、失败回执和命令日志，不重新执行 recipe。输出新的各产品 metadata、`build-catalog.json`、`result.json`，后者绑定原记录、命令日志、验证代码及本次耗时，明确 `compiled_in_this_attempt=false`。

目标节点 native004 的 `revalidation/attempt-001` 因主项目 CMakeCache 含非 UTF-8 诊断内容而拒绝；原失败文件保留。读取改为 surrogateescape，仍对编译器路径逐字匹配。`attempt-002` 于原生目标节点通过，耗时 **4.072820486 秒**，绑定 10 个原输入，三个产品 `identity_valid=true`，未重编译、未修改原产物。

部署入口新增显式 `--build-catalog`，消费时重新检查所选 metadata 与实际原生产物；部署快照及离线来源审计使用该明确路径。不会自动挑选最近一次目录。原默认 metadata 仍保留失败身份，未因重验改写历史结论。

验证命令：在工具目录执行 `python3 -m unittest test_batch_build_stage test_batch_stage_overrides tests.contract.test_build_service tests.contract.test_toolchain`，**38 项通过，7.074 秒**。新增验证覆盖原件保持、重复输出目录拒绝、完整产品选择、安全路径、已保存 catalog 与原生产物篡改，以及包含非 UTF-8 字节的缓存。这里仅验收构建重用来源，不声称接口资格、正式性能或 W01–W18 全链已通过。

本次修改不触及 SDK 测量热路径、产品源码、运行共享状态或持久化数据。并发约束为构建域独占锁；重验失败输出保留，重做使用新目录。下一步仍须解决跨运行根目录的共同资源锁，之后才进入新部署和资格验证。

## 14. 共同运行锁、环境账本及真实新部署资格验证

运行部署现在显式接受共同资源锁，持构建域锁和资源锁后才核对 cgroup、复验构建及写入新根目录；新根目录硬链接该资源锁。运行入口复核部署绑定的设备号/inode和实际共同锁。`run/isolation.py` 与 `run/environment_restore.py` 分别逐字节迁移旧 cgroup 和历史恢复实现，根入口保留兼容转发；新 `run/environment.py` 持运行锁执行有账本的准备/恢复，不进行历史 tmpfs 目录清理。详见 [runtime-design.md](runtime-design.md)。

新增明确 CLI：`stage`、`environment-prepare`、`environment-restore`。准备保存 before/intent/after/result，先记意图再操作；恢复仅作用于账本中的差异；失败留结果、同名输出拒绝覆盖。局部准备失败后可以显式恢复，不自动重放测量。

实际新运行根目录：`tiantiyun-80c128g:/home/worktrees/kvc-batch-modular-20261021-001`。冻结代码包 `p3-runtime-bundle-002` 含 252 项，manifest SHA `cc9ddb5ad8d4ce927c519036c9aaff95fc26f28494e9ddb85f1332c99ebe5d2e`，ZIP SHA `0c312725b075d25f570bfa3b525eb5f2e4ab0f58f21e5811d6e8135037549e3a`。该快照早于本节后续 CLI/失败记录增强，未原地刷新。新部署核对了 9 个原生产物和 245 个冻结输入；运行锁与旧 R7 的锁同为 device 64770 / inode 953499872，独立于 native004 的构建锁 inode 966002198。

| 实际操作 | 中间结果 | 实际耗时与结论 |
| --- | --- | --- |
| 环境基线 | `environment-before-full.json`、外部 `runtime-control001/preflight-command.json` | 已采集，独立命令记录保留 |
| 环境准备 | `environment-preparation/{before,intent,after,result}.json` | 0.157651041 秒；配置差异 0；仅临时停止已记录的 setroubleshootd.service |
| DataSystem deadline 诊断 | `deadline-diagnostic-001/summary.json`、原生步骤/日志、外部 command 记录 | 完整外部命令 64.154084430 秒；diagnostic_complete=true，all_sdk_steps_passed=false；失败原件保留 |
| 三产品资格验证 | `batch-gate-001/summary.json`、两端点/原生 batch/strace/配置证据 | suite 158.845983258 秒，外部命令 159.044490914 秒；三产品全部完成且 valid=true |
| 环境恢复 | `environment-restoration/result.json` | 0.101867403 秒；accepted=true；再次观察全部实验 cgroup.procs 为空 |
| 离线 DataSystem TCP 审计 | 本地 `runtime001/tcp-audit-001.json`，引用已收集资格原件 | 16 项 case 验证通过，9.107633543 秒；不启动或联系产品服务 |

资格通过与独立 deadline 诊断失败均按原结论保留，不能互相改写，更不能由此推导正式大 batch EXIST 性能通过。新运行目录尚未进行正式三轮性能矩阵，也尚未取得完整窗口资源审计或公开报告验收。

本地全量 `python3 -m unittest discover -s . -p 'test_*.py'`：先因漏列新增测试文件失败，补齐显式代码包清单后 **529 项通过，52.404 秒**，日志 `p3-runtime-all-002.log`。随后增加服务停止失败恢复、缺必需工具前置拒绝两项覆盖，运行 `python3 -m unittest tests.contract.test_runtime_environment` **3 项通过，0.237 秒**；不是声称这两项已包含在先前全量计数内。全量有旧 `tcp_protocol_gate.py` 测试路径的未关闭锁文件 ResourceWarning，本次新运行入口使用上下文管理器；该历史路径尚待 P6 归整。产品仓没有修改。

最后修改环境配置写入保留换行，使空 cpuset 原值也能正确恢复到 cgroup 接口。`python3 -m unittest tests.contract.test_runtime_environment tests.contract.test_bundle` **13 项通过，1.552 秒**，日志 `p3-runtime-final-targeted-003.log`。本地资格/诊断/环境收集为 686 MiB 原件，放在仓库外的 `runtime001/`；不把原件或大型 strace 提交到 Git。

W01–W18 的完整语义回执接入、P6 旧入口/测试归整及新的正式测量仍未完成。本节是 P3 的实际推进，不能据此将整个目标标为完成。

## 15. 产品运行代码归属与 W01–W11 实际编排

三产品 Python runtime、scalar/TCP adapter、Mooncake peer/进程身份实现迁入 `run/products/{datasystem,mooncake,lmcache}/`；旧路径只做显式 package 转发。LMCache 通过明确 LMCACHE_ROOT 定位运行目录和 SDK source，不再通过新模块的 sibling 目录猜测。7 个迁移文件的 **26 个原有顶层函数/类定义 AST 均未改变**；Mooncake peer 原件逐字节迁移。DataSystem 另增加 main 负责旧命令转发，不改变 launch/stop 的所有权与保护逻辑。比较结果在仓库外 `product-migration-functions-001.json`。

`core/spec.validate_runtime` 固定完整运行参数、3 次重复、原生只读错误政策和明确路径。`pipeline/runtime.py` 组合 owner 检查，`pipeline/steps.py` 只处理回执。W01–W06 分别固定计划、重验构建复用、复核完整工具包、新部署、观察基线和隔离；W07/gate、smoke 独立验收；正式 W08 ready 后才进入 W09，W09 封存原生 case 与结果后由 W10 停止观测并重新排空，W11 恢复账本。W09 重新调用 core 完整校验，包括所有 planned case 的参数、client 阶段、字节验证、histogram、错误会计；完整原生失败只生成 findings，不改写 native_passed。

新增 `workflow all/start/gate/smoke/measure/restore`。all 从冻结 bundle payload 执行 start，再从新运行根目录的冻结 package 启动独立子命令，command/log/耗时均保留，无隐式重试。较新的 partial attempt 阻断消费更早 PASS。current admission 子步骤在持资源锁时重新核对现场，不用 W06 历史 PASS 代替当前条件。

真实尝试与失败均保留：

| 尝试 | 实际结论与处理 |
| --- | --- |
| pipeline-control001 / runtime002 | W01–W06 均通过，外层 start 8.442241279 秒；gate 前发现 setroubleshootd 被重新激活，拒绝进入 SDK 并恢复，W11 通过。最初准入失败缺少 W07 子回执的缺口已在后续版本修正，不回填旧记录。 |
| 诊断干扰来源 | systemd show/journal 证明 mihumo.service 为 Restart=always、ExecMainStatus=203 的失败重启环，持续触发 SELinux 诊断；仅观察，未执行日志建议的 restorecon/SELinux 策略变更。 |
| pipeline-control002 / runtime003 | W06 使用新账本暂停明确失败重启来源，并 runtime-mask 诊断服务；资格检查发现 DataSystem 兼容脚本没有执行 main，未启动 Worker，W07 失败并恢复。本次是工具入口缺陷，不能记为被测产品性能失败。已修正 main 转发并新增无关 cwd 下 CLI/parser 与 start/stop 分发测试。 |
| pipeline-control003 / runtime004 | 使用修正后的冻结 pipeline-bundle-004，W01–W07 已通过；W06 验收 0.831300570 秒，W07 153.660115484 秒，gate 子命令 154.526988013 秒，三产品 native_passed=true。正在验证观测 smoke/正式矩阵，未提前宣布完成。 |

当前正式候选源码包 271 项，manifest SHA `8b96526809a5ecc3a9c6e61966851491ae869f6cf126a011616c373b9e1b7236`，ZIP SHA `c2874c5de7534f4197e014edae881ad60c7e1ff0a3025027b26d46deae3282b6`。节点根 `/home/worktrees/kvc-batch-modular-20261021-004`，control `/home/cache/kvc-modular-20261021/pipeline-control003`；外部总 command 在 `pipeline-command003/all-command.json`，其只在该真实进程终止后出现，缺失时不能据此认定停止或重启。

验证：第一次全量 536 项因两个绕过 __init__ 的 fixture 缺少新的可选 observer/log 字段失败，更新 fixture 后 536 项通过（54.380 秒）。加入环境 mask 与失败账本检查后 537 项通过（51.239 秒）；最终 main 转发修正后 **539 项通过，51.727 秒**，日志 `pipeline-all-004.log`。最后有关子集为 35 项通过（5.461 秒）。8 个 owner 的 AST 导入边界检查无跨层依赖。独立自有进程清理保护程序通过，不把该程序当成产品 SDK smoke。

环境新规则只移除本次创建的 runtime mask，已有 mask 保留；恢复有 baseline/hash 绑定，空 cpuset 用换行正确写回。mihumo 仅在重新观察到明确的 203 失败重启状态时暂停，恢复的是原激活意图，不声称修复原有故障。源码、永久 unit、SELinux 策略和历史原件均未修改。新步骤回执与重验在套件边界发生，不在每次 SDK 调用热路径新增 IO、锁或采样。

W12–W18 的跨节点收集/报告/公开验收、P6 归整和最终新结果仍是未完成项。后续将复核运行句柄/实际终态，再冻结原件及生成新的报告输入，禁止在开放流上提前封存或覆盖这次候选输入。
