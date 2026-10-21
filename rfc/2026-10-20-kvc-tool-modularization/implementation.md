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

## 16. W12 原件冻结、W13 离线派生与 W14 独立复核入口

新增 `collect capture/receive/verify/derive`。capture 要求 W01–W11（含入场和 smoke 子步骤）均已封存且符合当前 profile；重新持构建锁及共同运行锁，核对实际原生输出、源码 checkout、资源域排空和正式终态，再复制到新目录。索引 schema2 记录 runtime/control/native/bundle/revalidator/collector/invocation 等明确来源，不修改远端原始路径；receive 用映射核验原始回执完整链，原始构建失败结论仍保留为失败，后续重验通过另有记录。

DataSystem 关闭 RocksDB 后的空数据库目录和 mode 也列入冻结索引，收集与离线包保留它们；Coordinator 的 etcd-data 状态目录不收集，实际 RocksDB 文件若存在则保留供审计拒绝，不静默过滤。符号链接和非普通节点不跟随，并列出跳过记录。采集程序自身源码也进入快照，使 W12 validator 能在脱离目标节点后复核。

W13 从原始 SDK 结果/窗口重算矩阵、CSV、资源序列与三产品原始构建元数据。W14 独立重读归约，并核对构建、资格/TCP payload、21 个 DataSystem 部署 epoch、环境恢复、观测终态和资源覆盖；生成绑定 analysis/series/builds/workflow/evidence、四份当前文档和当前源码 ZIP 的 audit。正常报告仍只消费六份明确输入与已绑定文件；本次没有在 SDK 调用热路径新增 IO 或锁。

长窗口资源验收要求至少两个样本、90% 时间跨度、最大 1 秒间隔及必需工具持续可用；短 PUT 或提前返回的原生只读错误窗口采样不足显式列为 finding，未知指标保持 null。整个观测跨度也检查 OOM 差值，避免遗漏 case 间发生的 OOM。历史 R7 离线回归发现早期必需工具退出，因此新的覆盖检查正确拒绝该历史数据；不将其包装为当前正式新运行结论。

本节代码尚未对正在运行的 runtime004 部署，也未接触其开放采样流；必须等 W09–W11 结束后用新冻结包执行实际 W12–W14。上一个完整本地回归 549 项通过（60.991 秒，`collection-all-001.log`）；空目录及离线包新增覆盖后的相关 18 项通过（1.737 秒），完整复核见下一条实际记录。上述自动测试使用隔离 fixture，不替代正式原生矩阵。W15–W18 语义交付回执与 P6 目录归整仍未完成。

最终本地回归：`python3 -m unittest discover -s . -p 'test_*.py'` **551 项通过，75.811 秒**，日志 `collection-all-002.log`；8 个 owner AST 导入边界检查无跨层依赖。保留旧 TCP gate fixture 的未关闭锁 ResourceWarning，尚待 P6 归整。按 ds-self-verify 复核本阶段：diff 范围仅工具/RFC，资源锁内冻结、只读映射验收、新输出目录及失败保留已检查；无产品热路径/持久化格式改动，原始证据不覆盖，产品仓无需 `.repo_context` 结构更新。没有把本地 fixture 通过写成新压测通过。

## 17. W15–W18 交付入口与测试目录归整

新增 `report-workflow`（W15/W16）、`publish-workflow`（W17）、`check-publication`（W18）。W15 消费封存 W14，完整渲染新报告；W16 核对目录清单/gzip/链接，安全解压并恢复模式，实际执行包内 relocate/render，对比显示数据，再以显式 Node/Playwright/Chrome 检查原页面及离线页面的 1440/390/320px、九个 ECharts、失败空值、四文档、Mermaid 与菜单。W17 在新暂存目录上传并逐文件核验，安装全新 report 版本后按原站点锁/CAS 注册；W18 单独下载公开 manifest/依赖/文档/完整 ZIP 校验 SHA，再进行真实公开浏览器验收。未以这些入口存在宣称本次公开交付已通过。

`delivery/acceptance.py`、`browser.mjs`、`install.py`、`public.py` 分别负责本地文件/解压、浏览器、上传主机安装和公开字节验证；pipeline 只组合 owner 检查并封存回执。交付不调用原生 SDK。新增发行日期与外部 registration record 参数，保持旧调用默认值；新报告不因注册记录而改变其封存清单。

P6 首批归整：30 个根目录 unittest 迁入 `tests/unit/{build,run,collect,report,delivery,diagnostics,compat}`，共用 source root 在 tests/paths.py，跨测试 fixture 使用明确包导入。独立清理保护程序迁入 `tests/integration/datasystem_runtime_safety.py`，名称不触发默认发现。16 个原根目录实现在 `kvcbench/diagnostics/` 或 `kvcbench/compat/legacy/` 获得唯一归属，旧文件变为显式转发；修正直接运行时的 package root 和未来导入位置，不改变实际测量循环。旧 TCP gate 的锁现在由上下文管理器关闭，消除该 fixture 的 ResourceWarning。产品 shell 资产与最终旧入口退出仍待收尾，不能把本节当作完整 P6 完成。

验证记录：

- 仅测试迁移后 554 项通过（57.087 秒）；实现迁移首轮检出旧动态加载测试仍指向转发文件，改为测试 canonical owner；该失败原日志保留。
- 修正后相关兼容/诊断 76 项通过（12.945 秒）；最终全量 `python3 -m unittest discover -s tests -t . -p 'test_*.py'` **554 项通过，61.915 秒**，`organized-tests-004.log`，无先前 TCP gate 锁 ResourceWarning。
- 后加入的本地 publication install 两项与 delivery acceptance 三项共同通过（5 项，0.178 秒）；不将其额外两项计入上述 554 项。
- 独立进程身份/清理保护程序通过（只启动自有 fixture 子进程，不启动产品服务），结果 `p6-runtime-safety-001/safety-result.json`。
- 新 browser 验收器在历史干净包上正确拒绝其只有两份文档的布局，九图、交互、移动端、Mermaid 仍正常；不放宽四文档要求，不当作本次新报告验收。
- 冻结新后处理/交付包 `collection-bundle-001` 共 316 项，manifest SHA `3a3e2a2f7e19ff037dc9d71427c6494dc0aae8455a2a5c9aebe53183d50266f7`，ZIP SHA `c764b9e6c40a7851efd5f2ac76a2c4818a1a86a722ffec65fe527972630251a0`。尚未在正式测量开放时上传至目标节点。

本阶段按 ds-self-verify 检查：产品仓无改动；改动限工具/RFC，测量热循环未改；原运行根/原件/失败 attempt 未覆盖；上传使用新版本目录，失败不会覆盖已有报告。实际新矩阵、W12–W18 端到端和公开交付仍须由本次真实原件证明。

## 18. 实际交付样例、launcher 归属与 Coordinator 遗漏纠正

W15/W16 的完整隔离样例 `report-workflow-synthetic-002` 已实际通过：封存生成、manifest/链接、安全解压及模式恢复、执行包内源码 relocate/render、显示数据 SHA 比较、原报告与离线报告的三种视口/九图/四文档/Mermaid/失败空值检查。其 W14 明确标为 synthetic delivery fixture，原生数据全为 fixture，不用于正式产品成绩。第一样例保留失败：合法包根 bundle-manifest.json 被过严规则拒绝；仅放行固定成员名，其余安全规则不变。

Mooncake 的 env/start/stop/master launcher 与 LMCache 两种 server launcher 移入 run/products 对应目录，6 个 canonical shell 文件与迁移前逐字节相同，旧位置为薄转发。stage 在存在 canonical 资源时明确选用其源文件，保留旧小型 fixture 兼容分支。Mooncake、LMCache 及 stage/build 相关 35 项通过（6.699 秒）；包含新 publication install 的全量 **556 项通过，57.021 秒**（launcher-all-001.log）。收集策略后续修正另有针对性回归，不并入该计数。

runtime004 外层真实命令已终止：returncode=0，5056.379312235 秒，W09 passed_with_findings、W10/W11 passed；三个实验 cgroup.procs 均为空。实际 W12 尝试依次检出了 source_state 使用 --untracked-files=all 展开 .venv 与既定 normal 策略不一致、保留目录规则误将源码包 report/delivery 模块及其测试视为派生结果。统一为原有 normal 策略，明确白名单源码包前缀，仍拒绝真正派生目录、任意伪造源码前缀和缓存；失败 attempt 保留，新目录重做，不覆盖原件。

**关键遗漏：对实际 argv 的验收复核确认 runtime004 使用 ETCD，coordinator_address 为空。用户要求后续统一 Coordinator，因此这一轮只能保留为旧后端基线，不得作为最终符合要求的对比。** 已从相同固定 tag/SHA 的实际源码和 output/service 确认 Coordinator 二进制与配置已经构建；尚未把它冒充纳入原 metadata。后续按 [coordinator-correction.md](coordinator-correction.md) 验证其与原 tar 成员一致，明确纳入新 catalog、切换实际服务并重跑完整新矩阵；不需重编译或改变产品源码。

按 ds-self-verify 检查本阶段 diff：测量热循环未变；新 launcher 实体逐字节迁移；收集仍要求静止、锁定、绑定和新目录；安全解压只增加合法固定根成员。产品仓无改动。本次没有最终 Coordinator 矩阵、正式新报告或公开验收完成的证据，目标保持未完成。

## 19. 原生 Coordinator 已接入、实际资格通过与采样覆盖修正

新增 core/artifact_extension、build/adoption：额外产物只允许固定路径 datasystem_coordinator，原输出 hash/size、build tag/SHA/recipe/实际时间区间必须保持原值；额外二进制必须与原已绑定 tar 的对应普通文件成员 SHA/size/mode 相同。新 catalog 在 `native004/revalidation/attempt-003/`，真实验证命令 returncode=0，**8.040891305 秒**，未执行编译，输出清单从 9 增为 10。原始 metadata/失败验收不改写；W14 对归档字节和原 metadata 扩展另做独立复核。

运行已移除 ETCD 启动：默认一个原生 Coordinator（单节点无选举、8 个 RPC/bRPC 线程、4 个 watch dispatch 线程、关闭可选 metrics/采样），两个 Workers 的 etcd_address 为空、coordinator_address 非空。三进程全在 server cgroup 内计费。原 Worker RocksDB/Stream/IPC/RDMA/URMA 关闭项保持。run/configuration_evidence 接管原诊断中的 live 配置读取，主运行不再依赖诊断脚本；新模式每个 DataSystem epoch 在 SDK 窗口前读取实际 PID/start_ticks/argv/cgroup/原生二进制 SHA，检查 server 域内正好三个进程；collect/audits/coordinator 独立复核 21 个 distinct epoch 及归档来源。

| 真实尝试 | 当前证据与结论 |
| --- | --- |
| coordinator-command001/control001 | W01/W02 通过；W03 发现入口 Python 自动生成 __pycache__ 后拒绝包清单，外层 4.379510422 秒 returncode=1，未部署或启动 SDK。保留该包与失败记录；冻结包入口明确使用 python3 -B 和 PYTHONDONTWRITEBYTECODE=1，不放宽完整性检查。 |
| coordinator-command002/control002/runtime002 | W01–W07 通过；真实三产品 gate valid/native_passed=true，156.380353736 秒，现场 Coordinator verified=true，server_members 为 Coordinator+两 Worker。 |
| 旧 ETCD 基线实际 W12/W13/W14 | 缺失闭包依赖的接收被拒绝；新 receipt_closure 纳入全部显式来源内的回执依赖，新 snapshot005 共 **4304** 文件，received002 原始回执链独立验证通过。W13 为 valid132/failed18/N/A30，无 invalid；W14 的构建/TCP/部署/资源归约均通过，但 r0-mooncake 的 B16/B4096 fresh PUT 未达到 90% 采样覆盖，被拒绝正常报告。 |
| 安全取消 coordinator002 smoke | 在正式测量前，按明确的 90% 覆盖要求决定增加采样频率。新 cancel-workflow 只对本 control 的 active smoke/measure PID 使用 pidfd+SIGINT，核对 boot、进程 lifetime、argv、cwd，先记 intent。真实 signal 请求 0.013919721 秒；W07.smoke 记录失败/取消，W11 恢复通过，server/client/monitor 全空，未进入正式矩阵。不是产品资格失败。 |
| coordinator-command003/control003/runtime003 | 使用全新源码包/根目录、相同 workload/配额、**50ms 采样**；三产品 gate valid=true，于 UTC 2026-10-20 23:23:20 结束；smoke 的实际 monitor-manifest.interval_s=0.05。smoke/正式矩阵仍在进行，未提前宣布完成。 |

提升采样密度而不降低 90% 的既定门槛：200ms 的两端边界损失对约两秒 PUT 窗口不可忽略，50ms 留出更充分的实际覆盖余量。监控仍在独立的 2 CPU / 1GiB monitor 域，必需系统工具保持原配置，所有产品一致；实际覆盖仍须最终 W14 判断。报告前端仍保留每个有效 SDK 窗口内的全部实际 CPU/charged-memory 点，只移除前端时间线从不使用的 anon/shmem/IO 字段；完整 series/raw 与 IO 聚合不删减、不下采样、不插值，避免 50ms 数据将页面推过明确的 20MiB 预算。

当前运行包 coordinator-bundle-004 共 329 项，manifest SHA `8e2cc56bd76af2da2a28e12cce88b3f00049d547f8f05b2f306013a35c508c3c`，ZIP SHA `ca291ced79be499912a092f56d4f97f2d78f635d7cf7a194df17737308a42fa7`。冻结运行目录 `/home/worktrees/kvc-coordinator-modular-20261021-003`，外层命令在 `/home/cache/kvc-modular-20261021/coordinator-command003/`，control 同前缀 coordinator-control003；后续只检查该具体句柄和真实终态，不根据日志暂时无更新重启。

本地验证：初始全量发现 fixture 仍期望旧 artifact 个数，修正后 560 项通过（57.318 秒）；后续 560 项通过（74.772 秒）。新增 live/cancel 拒绝错误 argv/cgroup/PID 复用/其他 boot 的检查后，最终 **563 项通过，54.945 秒**（coordinator-all-004.log），针对性 17 项通过（1.725 秒）；独立精确进程清理程序通过。两条后续只读 Coordinator 审计条件增加了 Worker 完整性和 expected binary 路径检查，继续由实际新数据审计验收，未把 fixture 当作最终 SDK 结果。

按 ds-self-verify：产品源码仓干净；工作台 diff 限本次构建产物扩展、运行 backend/现场观察、收集闭包、采样与交付契约。实际 SDK 热循环未改，额外现场检查在窗口外；配置 backend 的性能影响是明确目的，因此正式三轮重新运行。pidfd 取消已核对实际恢复；旧基线、失败 attempt、原始编译记录未覆盖。P6 旧入口退出、最终 Coordinator 完整矩阵与 W12–W18 公开交付仍未完成。

## 20. P6 源码目录收敛与逐步证据入口（2026-10-21）

源码根目录现在只保留 README.md、bundle-inputs.json、requirements-report.txt 三个文件。构建配方、运行产品、观测、收集、报告与交付分别归所属模块。24 个必需薄入口资源移入 run/entrypoints，run/layout.py 按明确清单生成冻结运行目录，保持运行中的相对路径；根旧 facade 退出。历史完整控制器和旧产品 smoke 集中归档到 tools/migrations/kvc_20261020，兼容格式解析留在 kvcbench，正常模块不得导入归档控制器。原 RFC 的一次性网页脚本/模板与旧 QA 也已归档，原执行记录保留历史命令，链接同步到新位置。

迁移清单仍是原基线的 142 项，目标路径已按实际实现修订并逐项确认存在；主设计现状证据链接固定基线提交，避免用迁移后的行号解释旧代码。文档入口为 python3 -B -m kvcbench，默认回归按职责组织；旧完整实验从自己的冻结源码包重放。

网页工作流行增加输入、中间结果、产物清单、逐项验收、摘要和完整性回执的入口。report 只接受同 attempt、六项齐全且已绑定文件清单中的链接；W13 自己的闭合回执随证据复制，W14–W18 仍保留在不可变报告树外，避免自引用。浏览器实际检查渲染后的链接与数据一致，并读取目标文件/响应，不只检查页面存在。

| 实际检查 | 结果与边界 |
| --- | --- |
| 首轮搬迁后全量 | 563 项，60.912 秒，18 个错误；均为旧测试/兼容工具路径和 patch 模块名引用；日志 p6-layout-all-001.log 保留，未当作通过。 |
| 修正后针对性 | 128 项通过，11.126 秒，p6-layout-targeted-002.log。 |
| 目录及归档边界契约后全量 | 566 项通过，59.735 秒，p6-layout-all-005.log。 |
| 干净解压完整回归 | 566 项，64.429 秒，通过，1 项 Git 索引检查因包内无 .git 明确跳过；p6-clean-all-001.log。 |
| 增加逐步骤链接准入后全量 | **567 项通过，57.833 秒**；p6-layout-all-006.log。另有 13 项链接/收集针对性通过，1.432 秒。 |
| 实现与入口字节对照 | 对提交 14056ed，SDK worker、产品 runtime/adapter/shell 和共享 payload/统计/结果校验共 **28 个实现文件字节不变**，**24 个入口资源仅移动且字节不变**；p6-hotpath-equivalence-001.json。不以此替代现场新部署验证。 |
| 新完整源码包 | p6-layout-bundle-002 共 **307** 项；manifest SHA 8ee06aa77c34255b47aa45ff7464fb5a101c45cbf908891ebfd2579e84793469，ZIP SHA 68bcd88bd190e896f632fbfbd85e52aefc370608d1e03e0c25b5e45cf6808b76；制作与独立 check-bundle 通过。 |
| 实际 W15/W16 合成交付 | p6-report-synthetic-002，W16 19.935143953 秒；本地和干净解压后各 1440/390/320 三视口，9 个 ECharts、4 份文档、Mermaid、交互、失败空值与每步 6 个证据链接均通过。**仅证明交付机制，不是原生 SDK 运行或源码构建证据。** |

以上命令/JSON/日志均在本地外部验证根 /home/t14s/workspace/scratch/2026-10-21-kvc-refactor，构建物和运行原件不提交仓库。仅清理工具旧 datasystem/mooncake/lmcache 三目录中已验证只包含 .pyc 的生成缓存，共 86811 bytes，未触碰产品源码或远端运行目录。

按 ds-self-verify 复核：请求匹配模块化与逐步验收；本次未改 SDK 热循环、并发/进程生命周期、资源配额或原生产物；部署入口生成改为显式清单并拒绝缺失/符号链接，需在正式测量结束后再作目标节点新布局 gate/smoke。状态/回执仍封存后绑定、不覆盖旧 attempt。产品源码仓未改，无产品 .repo_context 结构变化；工具架构与迁移文档同步。Coordinator 正式候选仍使用其冻结 bundle-004，在 UTC 00:05 的实际 summary 已完成 r0 三产品与 r1 Mooncake，W09 未结束；不可用上述合成/本地通过提前宣布最终比较完成。

## 21. 独立机械验证程序入口复核（2026-10-21）

第 20 节已提交并推送 master：f2181b9a09394327d3bdd100b011651c47222e89。继续逐个检查非默认导入的独立程序时，发现 LMCache harness correctness 迁移后的 scalar 模块路径仍写成不存在的 run.benchmark；默认 567 项回归没有覆盖该独立入口。已改为 run.workers.scalar，子进程明确 cwd 为工具根并使用 -B。

实际 --kind fake 运行通过四类调用及故意损坏 GET 拒绝，输出 p6-harness-fake-001。新增从无关目录启动该独立程序的回归，确认 rankable=false、四类调用齐全及 mismatch 被拒绝；连同布局/工具包契约共 **14 项通过，4.596 秒**（p6-harness-targeted-002.log）。假适配器仅验证工具机械行为，不计入产品结果。此修正只触及独立测试入口和清单，无 SDK 实现、并发、持久化或现场配置变化；diff 与文件范围已按 ds-self-verify 复核。

## 22. 最终构建章节与公开证据链接覆盖（2026-10-21）

独立机械验证修正已推送 master：e49c26e。该提交的完整源码包 p6-layout-bundle-003 共 308 项，manifest SHA 1302e96027d8260316e7bfc5f633d0dfcb35210793590c0b056b790c77b26867，ZIP SHA 14b1c4602da1df8a590c5cceb49dd88a264f99be8efa2b5f89b99d72e0a9f7b0；干净解压后 **568 项通过，61.737 秒**，1 项只适用于 Git 工作区的索引检查明确跳过。

核对用户最初的构建附录要求后，补足 collect/documents：按产品列出原始实际 argv/cwd、编译器、构建耗时、构建与 metadata 各自退出码，并展开原始冻结 recipe。显示前对 recipe 重算 SHA，与当前已验收构建记录绑定；不是把今天的配方冒充当时执行版本。原始 DS metadata 退出码 1 保留，后续重新验收另有原件。原始命令记录、构建日志和冻结 recipe 均可从网页跳转。运行配置子章节也补上编号。

使用已收集原始三产品 native 构建字节验证生成：rc44/post1/v0.5.6、Mooncake USE_CUDA=OFF、LMCache NO_GPU_EXT=1、DS -X/-M/-A off 和原始 metadata 失败均实际出现在 HTML。该构建章节预览三份 recipe 全展开，1440/390/320 三视口无页面溢出或 JS 错误（p6-native-build-doc-preview-001/checks.json、browser-checks.json）。只证明原始构建章节，不代表 Coordinator 最终性能或整页交付。

公开验收的下载集合增加所有前端文档链接目标及 workflow 动态证据链接，按已接受 manifest 核对实际下载 SHA；仅本地存在或上传成功不算公开可访问。其他未链接 raw payload 仍由 W17 在发布主机逐文件 hash。拒绝越界及不在 manifest 的链接，W18 验证源码清单也纳入使用的 HTML 链接解析器文件。真实公开验收仍待本轮最终发布。

新增构建文档/公开链接选择及发布针对性 **6 项通过，0.205 秒**；最终当前源码全量 **572 项通过，63.513 秒**（p6-layout-all-007.log）。按 ds-self-verify 复核 diff：仅离线文档/公开下载验收及相关测试/清单变化，不改 SDK、现场进程、并发、持久化或资源配置；产品仓干净，工具实施文档同步。最终 Coordinator 正式矩阵和新布局现场验收仍未终结，不提前关闭目标。

## 23. Coordinator 正式矩阵终态与 W12 冻结（2026-10-21）

正式候选 coordinator003 已于 UTC **2026-10-21 00:46:55.453295** 写出 suite 终态：9/9 产品轮次、180 个 case 记录、execution_complete=true，failed=[]、fatal=null，host/interference 观测进程退出码均为 0。原生 valid=false，仍需 W13/W14 重算和核验，不将“执行完整”解释为“全部 API 成功”。

| 已封存阶段 | 实际结论 | 原始 monotonic 耗时 |
| --- | --- | ---: |
| 外层 workflow all | returncode=0、error=null | 5279.143559577 秒 |
| suite 完整生命周期 | execution_complete=true、native valid=false | 4481.155593257 秒 |
| W09 正式测量 | completed、returncode=1，验收 passed_with_findings | 4457.367467702 秒 |
| W10 停止与排空 | completed、returncode=0，验收 passed | 54.374696476 秒 |
| W11 恢复 | completed、returncode=0，验收 passed | 23.137824022 秒 |
| W12 原件冻结 | passed、4517 文件、命令 returncode=0 | 180.216223930 秒 |

这些区间的起止边界不同且部分重叠，不能相加为“总运行时间”。W10/W11 后实际 server/client/monitor 及父 cgroup.procs 均空、populated=[]；随后才开始冻结，没有在 SDK 窗口中执行大规模收集或新布局实验。

不可变快照：tiantiyun-80c128g:/home/worktrees/kvc-coordinator-snapshot-20261021-001，独立 W12 receipt 为同路径加 -receipt；collector 命令在 /home/cache/kvc-modular-20261021/coordinator-capture-command001。原始 root/control/运行代码包保持原样。

W12 使用新 postprocessor/collector 包 p6-layout-bundle-004：310 项，manifest SHA f0657203e5c027e53e90c3b0ba8c8af6caff81a7537c8edfcc0c72d0a7bce480，ZIP SHA 8ccdb7171d8822946d61a76c70c936c71f6df168fb6b79fc5476f50ffbdfff31。全部源文件 SHA/size/mode 再与当前干净 checkout 对照相同。正式 SDK 测量仍绑定其原来的 coordinator-bundle-004（329 项），不把后处理包假写成执行源码。

此时跨节点 receive 仍在运行，尚未宣布本地回执闭包核验通过。P6 新布局的独立 start/gate/smoke/restore 和 W13–W18 最终交付继续执行；新布局验证不复写本轮结果，不计入 180 个正式 case。脚本/文档提交已至 a0495ea，未修改产品源码。
