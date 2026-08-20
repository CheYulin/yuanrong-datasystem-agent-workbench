# TDD 实现计划

原则：每个行为先写测试并确认因缺少新策略而 RED，再做最小生产实现，最后回归相邻行为。

## 1. 选择策略 RED → GREEN

- [x] 在 `tests/ut/client/urma_chip_inflight_test.cpp` 增加策略值保存/恢复和亲和优先边界用例。
- [x] 先构建/运行 focused UT，保存缺少枚举、flag、归一化函数或错误选芯的 RED 证据。
- [x] 在 `urma_send_lane.h` 增加 `UbNumaSrcChipPolicy`。
- [x] 增加 `ub_numa_src_chip_policy` flag、声明和 `[0,1]` validator，默认 1。
- [x] 在 `GetAffinitySrcChipId` 中按策略生成候选，再复用原严格 `>` 深度覆盖。
- [x] 亲和优先分支不推进 RR sequence；不增加锁、分配、时间采样和成功日志。
- [x] 运行 focused UT 到 GREEN。

## 2. 协议传播 RED → GREEN

- [x] 先在 `client_worker_common_api_test.cpp` 写字段 33 默认 0/显式 1 用例并确认 proto 编译 RED。
- [x] 在 `RegisterClientRspPb` 增加字段 33；Worker 注册响应下发策略。
- [x] 扩展 wrapper 和 `SetClientUbNumaConfig` 签名；Client 在 UB Arena 初始化前应用策略。
- [x] 将策略加入首 Worker `call_once`、冲突比较和诊断日志；未知远端值归一化为 1。
- [x] 将新 flag 加入 worker config、operation logger 和部署文档。
- [x] 运行协议/Client focused UT 到 GREEN。

## 3. 双策略 URMA Mock ST RED → GREEN

- [x] 将现有 ST 源码编译为 RR 与亲和优先两个独立可执行文件和 CTest case。
- [x] 增加仅测试构建可见的 RR/亲和候选注入点；先确认亲和 case 在旧实现下失败。
- [x] 两个 case 复用相同 3 Worker、8 Client × 16 线程、8 MiB 一写十个独立并发读、GatherWrite 负载。
- [x] 分别断言 Client 与每个 Worker 执行正确策略分支，错误策略分支不执行。
- [x] 断言两个源芯片、深度覆盖、Gather 计数归零和 payload 一致。
- [x] 对两个独立进程 case 分别连续执行三轮。

## 4. 上下文、性能与完整回归

- [x] 更新 `common-infra.md` 的策略、并发和兼容语义。
- [x] 更新 `tests-and-reproduction.md` 的独立进程消融入口。
- [x] `git diff --check`、格式和构建闭包检查。
- [x] 在 Tiantiyun 使用 CMake、复用共享第三方缓存、`-j80` 构建 focused 目标。
- [x] 回归选择/协议/NUMA focused UT 和双策略 URMA Mock ST。
- [x] 明确 Mock 与真实 HCCS P99/PMax 证据边界。

## 5. 交付

- [x] 运行 DataSystem `ds-self-verify` 和 AI self-verification。
- [x] 相对最新 main 保持一个 DataSystem commit；检查 tree、状态和测试证据。
- [x] 确认 `origin` 是 `yche-huawei` fork 后才推送，绝不推送 `openeuler` remote。
- [x] 使用 `ds pr create` 创建 DataSystem PR，正文包含设计、热点开销、兼容矩阵和验证证据。
- [x] 在 workbench `master` 仅提交本 RFC 目录，避免混入既有脏文件；推送前核对远端与分支。
