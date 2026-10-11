# PR2056 Client 双故障恢复检视修复

- **Status**: Implemented and locally validated
- **目标 PR**: openeuler/yuanrong-datasystem !2056
- **语义基线**: 历史可用版本 `53c2548301bca4ade95586d838e14b14dae8e3cd`
- **代码基线**: 最新 `main/master` `1fdeb4d7d885019db8a25be17139290060d92775`，保留 53c2 隔离语义并逐提交适配
- **目标**: 在不改变正常路径、不误判普通超时、不重放结果未知写请求的前提下，保证
  `enableLocalCache=true/false` 的长期 Client 在两个 Worker 被隔离后能够恢复。

## 1. 已确认边界

本轮闭环 Coordinator summary 并发保护、剩余 reporter 下的双故障 quorum、Client HashRing 刷新/重选路、
异步切换生命周期和 membership-to-topology 原子提交栅栏。ZMQ、大规模故障扩散和 Set Publish 结果未知
语义不在本 PR 扩展；现有精确 `K_RPC_PEER_DEAD` 快速切换继续保留。

恢复成功定义为：当前失败请求可以返回原错误；Coordinator 在 3 秒目标窗口内完成隔离后，Client
收到新 HashRing、切换已被移除的绑定 Worker，后续业务重试在测试恢复门限内成功。不得自动重放
可能已经发出的 Set Publish。

## 2. 方案

### 2.1 故障信号

- `K_RPC_PEER_DEAD`: 保持现有立即异步切换，同时请求 HashRing 刷新。
- `K_RPC_DEADLINE_EXCEEDED`、`K_RPC_NETWORK_BLIP`、`K_RPC_UNAVAILABLE`:
  只调用已有、可合并的 `Routing::ForceRefresh()`，不直接判定绑定 Worker 死亡。
- 不新增逐请求 TCP probe。高并发故障下不增加 socket 创建、10ms 等待、线程池排队或端口压力。

### 2.2 权威 ring 驱动绑定 Worker 切换

`HashRingRefresher` 仅在收到新 topology 时调用 ring update hook。hook 先应用 WorkerSnapshot，再检查
当前绑定 Worker：

- 仍为 `ACTIVE`: 不切换；
- 已被新 ring 删除或状态不再是 `ACTIVE`: 复用现有单线程异步 switch pool 提交切换；
- Client 初始化的 `InitialFetch` 阶段 `routing_` 尚未发布，不执行该检查，避免启动期误切换；
- 提交时继续校验具体 `workerApi` 实例、按实例去重，并由现有 shutdown drain 保证生命周期。

该判断只发生在 topology 新版本发布路径，不进入正常请求成功 hot path。

### 2.3 两种 local-cache 模式

- `enableLocalCache=true`: direct Get 的恢复类错误触发合并刷新；ring 排除绑定 Worker 后切换身份，后续
  请求继续使用 local-cache 绑定路径。
- `enableLocalCache=false`: metadata failure handler 触发合并刷新；后续请求从新 ring 重新选择 metadata
  owner。如果绑定身份本身也被 ring 排除，同一个 hook 同时触发绑定 Worker 切换。
- metadata owner 的连接失败、dispatch deadline、peer-dead、Client disconnect 和 owner-unavailable 不再在
  固定旧 owner 上内部重试；它们返回 stale-route 信号，由外层 ObjectClient 在同一 API deadline 内根据
  最新 ring 重选 owner。普通对象级失败和 dead replica 仍保持原有语义。

### 2.4 并发故障 quorum 与提交栅栏

- summary evidence 采用 reporter 反向索引，summary 更新不会覆盖其它 reporter 的并发证据；每轮直接
  probe 有 128 个硬上限，候选 provider 的 sweep 缓存避免每页重扫。
- quorum 上限按当前仍可报告的 eligible reporter 收敛，但 3 个及以上 Worker 的集群保留最少 2 票；
  两 Worker 特例允许唯一 survivor 报告，从而覆盖两个 Worker 连续/近同时故障。
- Coordinator 在最后候选复核时读取 membership 集合及 Store revision；topology CAS 在 Store 写锁内原子
  校验同一 revision。复核到提交之间出现 membership 换代、TTL 删除或其它提交时，本轮只返回重试，
  不发布基于旧 incarnation 的 Failure topology。

## 3. 性能、可靠性与可用性

### 性能

- 正常成功请求：零新增 RPC、socket、锁和容器操作。
- 故障请求：最多一次 `ForceRefresh()` 原子窗口合并；同一强制刷新窗口内重复失败不重复启动刷新。
- ring 更新：一次 protobuf members 查找和一次去重提交，仅发生在 topology 新版本。

### 可靠性

- timeout 不等价于节点死亡，避免负载抖动导致错误切换。
- Worker 是否退出服务以 Coordinator 发布的新 ring 为权威证据。
- 异步任务捕获强引用；Submit 与 drain 使用同一 mutex 线性化，不新增裸指针生命周期。
- 不跨 Worker 重放结果未知的 Publish。
- membership revision fence 只位于 summary 驱动的 Failure commit，不进入普通请求或正常 topology hot path；
  冲突只放弃本轮并由下一次 reconcile 重试。

### 可用性和 3 秒目标

- 明确 peer-dead 保留即时切换能力。
- timeout 路径与 Coordinator 3 秒隔离收敛对齐：故障触发强制 ring 刷新，refresher 以 250ms 单 RPC、
  每轮最多 4 个 Worker 的既有边界轮转拉取新版本。
- 看护用例分别记录 kill、Coordinator 隔离、survivor ring 收敛、Client 最后失败和首次连续成功时间；
  3 秒是隔离目标，Client 恢复另设包含调度余量的明确门限，不混淆两者。

## 4. 测试契约

### UT

1. 初始化 ring 不触发绑定 Worker 切换。
2. 新 ring 仍包含绑定 Worker 时不切换。
3. 新 ring 删除绑定 Worker时仅提交一次切换。
4. 旧 workerApi 的迟到 ring/任务不能切换新的当前 Worker。
5. shutdown 与 ring 驱动切换并发时线程池排空，无 UAF、pending 残留或死锁。
6. timeout 只合并请求刷新，不直接切换；peer-dead 仍立即切换。
7. metadata owner 连接失败和 dispatch deadline 返回 stale-route，外层第二轮使用更新后的 owner。
8. membership 在候选复核后换代时 topology CAS 被拒绝，旧 topology 保持不变。

### 默认看护 ST

- 4 个 Worker；两个长期 Client 分别为 `enableLocalCache=true/false`；
- 两个 Client 初始绑定 Worker A；测试 key 的 metadata owner 为 Worker B；
- 同时或固定小间隔 kill A、B；
- 断言 A/B 均隔离、survivor ring 排除 A/B、两个 Client 均完成绑定切换；
- 使用同一 Client 发起后续 Set/Get，连续成功；不得通过重建 Client 规避恢复；
- 用例默认启用，作为一个稳定看护 case；3/4 Worker 长耗时测量继续 disabled。

## 5. 最终 Commit 划分

1. `fix(cluster): isolate failed workers and refresh stale rings`
2. `fix(client): refresh ring after direct Get recovery failures`
3. `fix(client): switch bound worker after authoritative ring removal`
4. `fix(cluster): preserve isolation quorum during concurrent failures`
5. `fix(client): refresh authoritative ring after routed failures`
6. `test(st): guard two-worker client recovery modes`
7. `fix(client): reroute reads after metadata owner failures`
8. `fix(cluster): fence isolation against membership changes`
9. `refactor(client): keep metadata retry within codecheck limit`

所有检视修复均为独立 commit，没有 squash 到原提交。

## 6. 验证和交付

1. 本地 `git diff --check`、格式和相关 CMake 源码目标。
2. focused UT：routing、transport、ObjectClient shutdown/switch。
3. 默认双故障看护 ST，以及 PR2056 历史 focused isolation ST。
4. `tiantiyun-80c128g` 使用 CMake 构建和运行；Bazel 只检查源码构建闭包。
5. 推送仅到已验证的 `yche-huawei` fork PR 分支，并触发完整门禁。
6. 门禁通过后重新运行 `ds-pr-review prepare`，按检视轮次复核；有问题逐条修复和回复，无新问题不发布
   噪声总结评论。

### 6.1 Tiantiyun 最终验证（rebase 后）

- CMake `ds_ut` 和 `ds_st_kv_cache` 从最新源码重新构建成功。
- 112 个 routing/transport/topology/Store focused UT 全部通过。
- 53c2/PR1997 的 5 个历史 focused ST 加 1 个默认双故障看护 ST 共 6/6 通过。
- 双故障看护：两个 Worker 分别在 3324/3299 ms 隔离；`localcache=true` 最后失败 3348 ms，
  `localcache=false` 最后失败 3567 ms，两个原 Client 均恢复。
- Bazel 仅验证 `//tests/st/client/kv_cache:coordinator_active_failure_stop_resume_test` 源码构建闭包，不执行 ST。
- openEuler 门禁 #9853 最终 SUCCESS：CodeCheck、license、SCA、x86、aarch64、UT/ST 和 Python example 通过；
  aarch64 首轮偶发失败均在门禁自动重跑中通过。

## 7. 回滚

功能和测试 commit 独立。若恢复逻辑引起回归，可单独 revert 功能 commit，保留失败测试和现有 PR2056
的 summary、ring refresher、生命周期修复；不需要回滚协议或持久化格式。
