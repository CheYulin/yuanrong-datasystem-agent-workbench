# 5. 阶段验证记录

## 5.1 运行前自检

- 请求匹配：每次 API 调用 B=1/16/256/4096，原生 batch 与 N/A 分开；1KiB、C8、三轮、TCP、两服务。
- 离线机制验证：`python3 -m unittest discover -s scripts/experiments/kvc-fair -p 'test_batch_*.py'`，78 项通过（0.558s）。覆盖三适配器、18 个 gate fixture 与 harness 的私有工作集、fresh key 唯一性、upsert 恢复读回、失效 baseline 拒绝、监控进程部分启动失败等。
- `python3 -m py_compile scripts/experiments/kvc-fair/batch_*.py` 通过；`git diff --check` 与新文件 no-index whitespace review 无问题。
- 已修复准备期发现的 harness 风险：LM callable metadata；完整恢复读回；失败子进程升级 kill；服务/监控 finally 清理；SIGTERM 清理；baseline 完整 81 格身份与 gate 同输入绑定。
- 热路径：没有产品代码变动。适配器 O(B) guard 计入延迟；请求列表构造和抽验计入端到端吞吐但在 API 延迟边界外；正式采样额外开销隔离到 monitor cgroup。
- 并发：8 客户端私有 key，共享双服务；SDK 同步完成后才计成功；LM 锁 finally 释放并等 ACK，异常不当 miss。
- 持久化/恢复：无产品行为改动；只使用本任务 namespace，PID/start-ticks/cmdline 身份保护原样保留；不宣称此性能测试证明容灾能力。
- `.repo_context`：本任务不修改产品职责、结构或构建/测试路径，无对应模块上下文变更。
- **尚未证明**：离线 fake SDK 不能证明部署版支持 4096 或实际 TCP/完整字节；须以目标机 gate、smoke 和正式结果独立验收。

## 5.2 已保留的准备期失败

第一次全量 harness fixture 执行出现一处 fixture 缺 baseline plan、另一处 fake clock 恰好结束于原始字节版本，无法触发预期的损坏恢复；均为测试夹具缺陷，补齐81格plan并调整模拟时钟后通过。另一次命令最后的 `rg huge` 无匹配退出1，前置 unittest/compile 已通过；删除未经实际 runtime 证实的文档 flag 表述。这些失败不属于目标机实验数据。

第一版 stage 在尚未 prepare、启动服务或测试前完成，随后只读审查发现 symlink parent 目录约束需要物理路径判断，并将 gate strace 改为分线程 hex 输出以支持新 batch TCP 载荷证明。第一版目录原样保留，正式环境改用全新 `-r2` 根目录，重新记录 staging 命令和构建物 SHA，不修改已冻结输入。

## 5.3 真实门禁与后续自检

- 准备：完整硬件/进程/磁盘快照0.129320229s；无旧tmpfs待清理项，保留/删除目录数0。cgroup仍为server/client各8CPU/12GiB、monitor2CPU/1GiB、swap0。
- gate1真实失败：50.821004702s，DS四客户端/模式均通过，Mooncase B4096 GET中10项为空；结束四cgroup排空。
- 无跟踪独立诊断6/6通过：73.304707536s；没有调TCP queue参数；源码队列解释是推断。
- gate2为证据组合，不是新测量：2.118175008s。
- 针对运行/门禁/协议审计/组合脚本的107项离线机制检查通过（2.583s）；QA脚本Node语法检查通过。报告分析器仍在独立审查，未将其草稿fixture当作此运行验收。
- DS实际TCP审计：16组合中12满足指定key0前缀+字节量；4个B4096 GET均达到至少4MiB直接TCP成功读取，但-s2048截取未见指定key0前缀。此证据暂不完整，不把它改写成4个数据正确性失败；原门禁完整bytes/顺序已通过，保留原审计结果及范围。
