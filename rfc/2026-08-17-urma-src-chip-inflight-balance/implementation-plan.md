# URMA 源芯片 inflight WR 近似均衡：实现计划

基线：PR 2081 HEAD `8e4aa6967fef67ced6228b478924b33d0e703835`  
分支：`codex/pr2081-inflight-balance`  
原则：每一阶段先写可失败测试，确认 RED，再做最小实现并回归 GREEN。

## 1. 选择算法与配置传播

1. 在 `urma_chip_inflight_test.cpp` 增加阈值边界、双方向、禁用和 RR type 用例。
2. 在 Client 注册配置 UT 增加 proto 阈值传播和首 Worker 冻结用例。
3. 运行 focused UT，保存缺少 flag/字段/签名的 RED 证据。
4. 增加 `ub_numa_inflight_wr_diff_threshold`，默认 15；0 表示禁用。
5. 为 `RegisterClientRspPb` 增加字段 32，Worker 下发，Client 应用；同步非 URMA wrapper 签名。
6. 在 `GetAffinitySrcChipId` 中先生成 RR candidate，再用两个 relaxed inflight 快照按严格 `>` 覆盖。
7. 添加只在覆盖分支触发的测试注入点和限频日志，不改变正常路径观测成本。
8. 运行 focused UT、格式检查与 proto/CMake/Bazel 构建闭包检查。

## 2. PR 2081 NUMA Arena 闭环

1. 先补绑定计划 UT：arena 数量、等分范围、node round-robin、非法输入。
2. 补 ArenaGroup UT：UB transport arena 数量、每 Arena 大小、Allocate 的 round-robin 归属。
3. 验证绑定计划覆盖完整 Arena；生产路径依赖 `BindMemoryToNumaNode` 逐页触页后再构建 NUMA range table。
4. 提取无系统调用的 `BuildRoundRobinNumaBindingPlan` 给生产路径与 UT 共用。
5. 将 UB transport 的地址到 NUMA 查询放到首次触页之后，失败仍保持可诊断的 best-effort 行为。
6. 回归现有 shared-memory/allocator/URMA manager UT。

## 3. URMA Mock 分芯片排队

1. 先为延迟解析增加 UT：chip1/chip2、全局回退、非法值与无驱动扩展。
2. 在 Mock PostSend 中从 bondp WR 扩展读取 `src_chip_id`，解析
   `URMA_MOCK_CHIP_1_LATENCY_US`/`URMA_MOCK_CHIP_2_LATENCY_US`。
3. 延迟仍由现有 Mock 完成线程执行；不改变发送 API 和旧环境变量语义。
4. 增加两个芯片均完成、chip1 完成更晚的组件断言，避免只测解析函数。

## 4. 独立 URMA Mock ST

1. 新建独立测试可执行文件，避免 Client 单例和环境变量受其它 object-cache case 污染。
2. CMake 从既有 object-cache glob 排除该文件，新增只在 `BUILD_WITH_URMA_MOCK` 构建的目标；Bazel
   增加 `manual`/URMA Mock 目标。
3. 启动 3 个 Worker，启用 NUMA affinity、2 transport arenas、阈值 15。
4. 创建 8 个逻辑 Client，逐一断言初始化成功；进程级 2 GiB transport pool 给 128 个并发读线程留出
   1 GiB 接收缓冲和 1 GiB 分片/回收余量。
5. 每个 Client 并发写 4 个 8 MiB key，然后每个 key 用十个同步开始的任务并发读取；总工作线程覆盖
   8 Client × 16 线程，写/读目标覆盖多个 Worker。
6. 配置 chip1 Mock 延迟高于 chip2；由于 Mock memfd 重映射不保留物理 NUMA 页位置，使用仅测试构建
   可见的 affinity 注入，并为每个进程注入一次 16:0 深度快照，确定性验证默认阈值 15 的覆盖。
7. 断言 Client 与 Worker 覆盖注入点均触发、两芯片均被选择、payload 完整一致；阈值 0 的纯 RR
   回退由选择 UT 覆盖。

## 5. 验证与交付

1. 运行新增及相邻 focused UT。
2. 运行新增 URMA Mock ST，并至少重复 3 次排除调度偶然性。
3. 运行现有 `UrmaNumaAffinityTest` 和相关 object-cache URMA ST 回归。
4. 检查 `git diff --check`、格式、CMake/Bazel 源码闭包。
5. 更新 `.repo_context` 中 URMA 配置、热点选择和测试入口的窄范围说明。
6. 执行 `ds-self-verify`，逐项审查热点路径、并发、生命周期、协议兼容和测试证据。
7. 不向 `openeuler/yuanrong-datasystem` 推送；如需交付，只能在确认 URL 后推到用户 fork。

## 6. 实机性能验收（不由 Mock 替代）

固定 8 MiB、8 Client × 16 线程、一写十个同 key 同时读，目标 315 write groups/s、3150 reads/s。
分别测试阈值 0、8、15、24，并记录：

- 每 100 ms 的 chip1/chip2 inflight WR 和差值；
- 每芯片选择、覆盖、完成 WR 数；
- 两 CPU 间 HCCS 单/双向带宽与两个 UB Die 端口带宽；
- 总吞吐和 P50/P99/PMax，重点核验 P99 1.5 ms、PMax 5 ms；
- 是否存在持续至少 3 个采样窗的一侧空闲、另一侧高排队。

选取满足时延目标且覆盖抖动最少的阈值；默认 15 的依据是 15 × 4 MiB = 60 MiB，约等于
单端口 45 GB/s 下 1.4 ms 的粗粒度排队量。这个估算不改变生产选择算法。
