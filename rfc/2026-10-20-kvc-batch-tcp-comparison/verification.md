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
