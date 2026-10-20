# 1. 模块化实施中的代码边界

当前 P1 与 P2a 已有：

| 模块 | 文件 | 责任 |
| --- | --- | --- |
| core | `steps.py`、`timing.py` | 无 IO 的步骤/结果模型、结论计算、ID、wall 与同进程 monotonic 耗时 |
| platform | `files.py`、`cgroup.py` | 安全相对文件、SHA/size/mode、原子 JSON、明确资源域的成员与排空 |
| platform | `receipts.py` | attempt 的持久化、实际上游绑定、独立完整性核对、摘要与终态提交 |
| build | `bundle.py` | 显式源码清单、嵌套模块和资源、完整 ZIP 与权限校验 |
| build | `metadata.py`、`catalog.py` | 固定版本与构建记录、实际 HEAD/tag/状态/产物核验、构建来源快照；不要求旧正式压测成功 |
| platform | `locks.py` | 只打开真实普通锁文件，资源锁 inode 语义保留 |
| run | `stage.py` | 新目录部署、运行配置和输入冻结；通过显式同步 provider 消费构建/代码包服务，不导入 build/collect/report 实现 |
| compat | `commands.py` | 临时旧参数转换与跨模块组合；构建/运行逻辑各归所属模块 |
| CLI | `cli.py` | 对外显式命令和错误状态；不导入产品 SDK 或自动运行实验 |

正式部署与运行已改用独立的 digest/drain 公共能力，`profile_put` 保留导入别名供迁移期调用；主流程不再从特定 PUT 诊断中导入这两个机制。其余诊断耦合按后续阶段逐项迁移。

`configs/versions/kvc_20261020.json` 是构建版本声明的唯一数据文件。旧的 build_metadata/batch_stage/batch_build_stage 路径暂留为薄参数适配，canonical 模块间没有原先的双向导入；新构建服务与正式 workflow 命令尚在接入。

## 2. 中间结果如何交接

每个 attempt 先有 `start.json` 和 `inputs.json`；完成后写 `result.json`、`artifacts.json`、`acceptance.json`、`summary.md`，最后写 `commit.json` 并原子发布目录。失败 attempt 保留；新尝试使用新编号，不在旧目录上继续写。

commit 绑定回执文件，acceptance 绑定实际输入/结果/产物清单与验证代码快照，产物清单绑定真实文件 SHA/mode/size；上游也通过 commit hash 和实际文件绑定检查。修改 payload、权限、摘要、验证代码快照或上游来源都会使复核失败。

P1 提供持久化和绑定机制；每个 W 步骤的业务验收函数仍随 build/run/observe/collect/report 模块逐步接入。通用回执不能替代产品正确性、采样覆盖、清理恢复或页面验收。

## 3. 历史与部署边界

历史目录和旧 ZIP 保持原样；带新清单的源码复现包使用明确成员，旧布局只保留已有兼容读取。新 batch/TCP 部署复制完整 package 并将它纳入冻结输入；native 入口也复制 package，并在运行源码 hash 与快照中记录其模块。

构建物、运行原件、回执、报告和下载包放在仓库外。DataSystem 后续说明统一称 Coordinator，历史配置/源码/log 字节保持原名；本阶段未修改产品代码、接口语义、线程或测量循环。
