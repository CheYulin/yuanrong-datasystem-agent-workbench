# 1. 模块化实施中的代码边界

当前已接入以下边界，实际验收进度以 RFC 实施记录为准：

| 模块 | 文件 | 责任 |
| --- | --- | --- |
| core | `steps.py`、`timing.py` | 无 IO 的步骤/结果模型、结论计算、ID、wall 与同进程 monotonic 耗时 |
| platform | `files.py`、`cgroup.py` | 安全相对文件、SHA/size/mode、原子 JSON、明确资源域的成员与排空 |
| platform | `receipts.py` | attempt 的持久化、实际上游绑定、独立完整性核对、摘要与终态提交 |
| build | `bundle.py` | 显式源码清单、嵌套模块和资源、完整 ZIP 与权限校验 |
| build | `metadata.py`、`catalog.py` | 固定版本与构建记录、实际 HEAD/tag/状态/产物核验、构建来源快照；不要求旧正式压测成功 |
| platform | `locks.py` | 只打开真实普通锁文件，资源锁 inode 语义保留 |
| run | `stage.py` | 新目录部署、运行配置和输入冻结；通过显式同步 provider 消费构建/代码包服务，不导入 build/collect/report 实现 |
| core | `statistics.py`、`case_validation.py` | 直方图桶界、整批延迟与原生结果完整性校验；运行与离线分析共用，不依赖报告或资源解析 |
| run | `plan.py`、`runner.py`、`workers/`、`qualification.py`、`products/*_batch.py` | 顺序/epoch、原生测量循环和 batch 适配器；runner 显式接收观测 factory |
| observe | `environment.py`、`host.py`、`interference.py`、`light.py`、`session.py`、`validation.py` | 原始快照/采样、suite 观测会话、就绪快照和停止封存的独立复核 |
| collect | `resources.py` | 读取封存采样并归约资源；缺失值与计数器回退保持未知，不依赖 HTML |
| compat | `commands.py` | 临时旧参数转换与跨模块组合；构建/运行逻辑各归所属模块 |
| pipeline | `runtime.py`、`steps.py` | 显式 W01–W11 组合、分域验收、失败清理与恢复；正常准入不回退复用旧 PASS |
| run | `admission.py`、`environment.py`、`products/{datasystem,mooncake,lmcache}/` | 运行锁/门禁/矩阵证据校验、环境账本、产品 runtime 和 scalar/TCP adapter |
| CLI | `cli.py` | 对外显式命令和错误状态；不导入产品 SDK 或自动运行实验 |

正式部署与运行已改用独立的 digest/drain 公共能力，`profile_put` 保留导入别名供迁移期调用；主流程不再从特定 PUT 诊断中导入这两个机制。其余诊断耦合按后续阶段逐项迁移。

`configs/versions/kvc_20261020.json` 是构建版本声明的唯一数据文件。旧参数适配集中于 compat/entrypoints，历史完整控制器位于 tools/migrations/kvc_20261020。canonical 模块间没有原先的双向导入；W01–W18 已接入，最终 Coordinator 实测与公开交付仍须完整验收。

## 2. 中间结果如何交接

每个 attempt 先有 `start.json` 和 `inputs.json`；完成后写 `result.json`、`artifacts.json`、`acceptance.json`、`summary.md`，最后写 `commit.json` 并原子发布目录。失败 attempt 保留；新尝试使用新编号，不在旧目录上继续写。

commit 绑定回执文件，acceptance 绑定实际输入/结果/产物清单与验证代码快照，产物清单绑定真实文件 SHA/mode/size；上游也通过 commit hash 和实际文件绑定检查。修改 payload、权限、摘要、验证代码快照或上游来源都会使复核失败。

P1 提供持久化和绑定机制；各 W 步骤由对应 build/run/observe/collect/report 模块提供业务验收。通用回执不能替代产品正确性、采样覆盖、清理恢复或页面验收。

## 3. 历史与部署边界

历史目录和旧 ZIP 保持原样；带新清单的源码复现包使用明确成员，旧布局只保留已有兼容读取。新 batch/TCP 部署复制完整 package 并将它纳入冻结输入；native 入口也复制 package，并在运行源码 hash 与快照中记录其模块。

构建物、运行原件、回执、报告和下载包放在仓库外。DataSystem 后续说明统一称 Coordinator，历史配置/源码/log 字节保持原名；本阶段未修改产品代码、接口语义、线程或测量循环。


## 4. 观测的两个中间结果

`observe.Session.start()` 启动 host 和 interference，要求服务端/客户端 cgroup 首样本完整、必需外部工具存活且干扰采样已出现，再封存 `ready.json`。它包括独立的首样本、就绪样本和启动清单快照，不对开放流提前计算固定 hash。正式 runner 接收显式 observer factory，旧 CLI 的组合仅在兼容入口发生。

`Session.stop()` 等待自有监控退出，关闭日志后写 `final.json`，绑定已停止的采样、工具日志、最终 manifest 和 ready。两种中间结果由 `observe.validation` 独立只读复核；final 通过仍不证明测量窗口覆盖，由 collect/audit 继续核验。目标节点已实际验证启动—采样—停止—封存，相关数据在工作区外；这不替代三产品正式矩阵验收。


## 5. 收集与审计的模块边界

`collect/analysis/` 负责 scalar/batch 原始结果重算；`collect/audits/` 分离服务期、资源、部署、TCP、门禁来源、runtime/build 来源和后处理来源复核；`collect/traces.py` 只读解析 syscall；`snapshot.py`、`timings.py` 分别保留停止后的源码/构建快照和阶段耗时收集。原生 payload 约定和 source-state 规则为 core 纯函数，收集层不导入 run/build/report。

审计来源清单绑定实际 canonical 文件。新 TCP、资源、部署审计分别使用 schema 3、2、4，保留旧 schema 的只读解释；旧记录必须提供其原始 helper 字节，不用今天的薄 facade 冒充原先执行的脚本。当前版本的 closure 包括真实分析、直方图、原生结果校验、资源归约和相关 provenance 声明。

`collect/index.py` 按明确且唯一的 path/role 清单制作新快照，保存 SHA/size/mode 并可独立复核。允许选择已经关闭的 W01–W11 回执，排除本次 W12、自身索引和下游派生目录；复制中变化保留失败现场。terminal_suite 区分“原生失败但执行完整”和“清理未知”，后者阻断正常收集。W12 的锁、现场排空、恢复与上游回执准入仍由总编排提供，索引完整性本身不是这些业务前置条件的证明。


## 6. 报告与交付边界

新的 report/inputs.py 消费已落盘并绑定的中间结果；render.py、model.py、documents.py、offline.py 分别处理页面、图表投影、Markdown/Mermaid 阅读页和离线包，report 不导入 build/run/observe/collect。原来混合分析和生成的旧入口明确保留于 compat，用于旧格式复核，不作为新的 report 实现。

源码 inventory、交付树 inventory、SHA/gzip/ledger 与原始 tar 检查归 platform；report/package 使用明确源码清单。发布、注册和历史导航维护归 delivery；W16–W18 回执写在报告树外；已接入编排，每轮实际交付结论以独立回执为准。历史本地 refresh 仍支持旧报告，对新不可变报告禁止原地刷新。

新消费者按 audit 中的 SHA 绑定拒绝跨 run/profile 或错配组合，允许完整的原生失败矩阵保留 failed。离线 relocate 只增补到相同字节/权限的显式位置映射，不重写原始 audit/evidence；干净解压后的实际代码已完成 help、位置映射与重生成验证。


## 7. 源码布局与冻结运行布局

源码根目录保留 README、配置、模块、测试、文档和明确归档目录。构建 recipe 在 build/recipes；运行 shell 在 run/products；薄 Python 入口资源在 run/entrypoints；ECharts 在 report/assets，历史模板在 compat/assets。测试 fixture 和旧浏览器检查分别在 tests/fixtures、tests/acceptance/legacy。

run/layout.py 以明确清单 materialize 运行入口，校验复制前后 SHA/size/mode。部署目录仍使用已约定的 batch_benchmark.py、scripts/datasystem/runtime.py 等路径，保持冻结执行路径和证据解释。入口资源保留原字节，业务逻辑仍从 kvcbench 导入。历史完整控制器不加入正常主流程的依赖链。

目录迁移不改写正在执行的冻结代码包。实际测量源码身份和后续离线处理源码身份分别记录，后处理使用新代码必须重新生成其验收回执。


## 8. 大型观测原件的离线读取

资源审计逐行解析 JSONL，保留每条样本的时间戳及计数器、PID/start_ticks、所需主机 CPU 行、工具状态和覆盖检查字段；不丢样本、不插值、不改变窗口或归约规则。用于诊断的完整 host/proc 文本仍在原始文件中保留并整体绑定 SHA，审计内存对象不复制这些未消费字段。大文件校验复用 platform.files 的分块 SHA。原始数据、已封存回执和 SDK 测量路径不因后处理修正而改写。


## 9. 公网 HTML 与磁盘原件

站点会给 HTML 加入固定公共阅读器和统计组件。delivery.public 对原件、实际公网响应分别保留 SHA；仅对 configs/reports/yche-html-20261021.json 中精确列出的组件和已核验 query 改写计算核心内容 SHA，组件增加数量也有上限。任何其他 HTML 内容变化均拒绝，JSON、脚本、原始证据和 ZIP 仍要求原始字节 SHA 相同。W18 回执绑定实际使用的 profile 文件。站点公共组件更新后须重新核验配置，不能改成通用 script 删除或任意空白归一化。

已实测文档HTML可能没有末尾换行：只移除精确的阅读器尾部组件及其伴随换行，不要求它之前存在换行。原件自身0/1/2个末尾换行逐字节保留；额外尾部换行和正文空白变化仍拒绝，不执行一般strip/rstrip。最终报告包007保留当时源码；后续W18的只读校验器修正以其独立回执中的validator-sources为准，不重写原报告。
