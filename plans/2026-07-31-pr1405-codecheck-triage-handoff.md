# PR1405 CodeCheck 未解决项分级与下一轮执行边界

## 1. 基线

- 导出文件：`openeuler_yuanrong-datasystem_全量导出（未解决）_20260731055325.xlsx`
- 导出时间标识：`2026-07-31 05:53:25`
- 核验 PR1405 head：`861fa07dfac02d394219d62cb3275e461eb4d899`
- upstream 基线：`main/master@7507f139fdb1222b29eb77542223a68dea6cd19f`
- 未解决告警：35 条
- 严重级别 1：2 条；级别 2：33 条

本结论以 Excel 的规则、示例和 PR1405 exact-head 源码共同核验。CodeGraph 只用于发现：现有索引为
30,997 files / 727,004 nodes / 2,127,528 edges，未包含 PR1405 新符号，因此最终判断以 exact-head `rg`、源码和
BUILD/CMake 为准，禁止为了本轮任务重建或同步索引。

## 2. 总体分级

| 类别 | 数量 | 本轮动作 |
|---|---:|---|
| 低风险直接修复 | 9 | 中控优先处理 |
| 测试代码窄屏蔽 | 2 | 中控优先处理，禁止扩大产品 API |
| 前置声明 | 12 | 保留并申请规则屏蔽/说明 |
| 函数过长 | 7 | 不纳入 CodeCheck 快修；4 条单独设计，3 条建议屏蔽 |
| 格式规则 | 5 | 暂不修改；与 clang-format 结果冲突或会增加无意义变量/行数 |

## 3. 本轮应改：9 条低风险源码项

### 3.1 空 catch 补充意图注释：7 条

涉及文件：

- `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp`：5 条
- `src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.cpp`：2 条

这些 catch 是可靠性边界，不应删除：

- completion callback 必须 best-effort，不能让用户注入 callback 的异常逃逸到线程池；
- 日志本身失败不能破坏 at-most-once completion；
- recovery terminal handler 不能反向击穿 recovery state machine。

每个空 catch 只补一句与该边界对应的注释，不抽 helper、不改变异常层级、不改 callback 调用顺序。

### 3.2 magic number：2 条

1. `worker_oc_service_impl.cpp` 的 `businessOperationId.substr(0, 12)`：改用局部或文件内命名常量，例如
   `TOPOLOGY_OPERATION_LOG_PREFIX_SIZE`。不要为了复用 `TopologyDiagnosticPrefix` 给
   `worker_oc_service_impl` 新增对完整 `cluster_topology` 的链接依赖。
2. `master_oc_service_impl.cpp` 的 `FLAGS_node_dead_timeout_s * 1000`：用
   `std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::seconds(...)).count()` 表达单位转换。
   必须证明转换前后值完全相同，不调整 timeout 默认值、上下界或失败语义。

## 4. 本轮应窄屏蔽：2 条测试 protected-access

文件：`.skills/ds-pr-review/tests/test_sensitive_scan.py`，两处直接测试内部函数
`review_pr._resolve_patch_payload`。

建议在两处调用上做行级 `pylint: disable=protected-access`，并说明这是精确验证 truncated patch
重构路径的白盒测试。不要为了消警：

- 把 `_resolve_patch_payload` 提升成公共 API；
- 新增仅供测试使用的 wrapper；
- 使用 `getattr` 绕过静态检查。

## 5. 建议保留/屏蔽：12 条前置声明

这些声明都用于 pointer/reference/friend 边界，直接 include 会扩大编译依赖，部分还形成循环：

- `worker_health_check.h`：`WorkerAdmissionGateUpdate` 仅按 const-reference 出现在接口中。
- `worker_oc_service_impl.h`：runtime、API、metadata reader、slot store 均为 pointer/shared_ptr 边界。
- `object_cache_recovery_state.h`：`WorkerRuntimeFacade` 仅用于 pointer 参数。
- `worker_coordination_backend_factory.h`：`EtcdStore` 仅用于 pointer 参数；include 会反向耦合具体 store。
- `worker_topology_object_cache_actions.h`：service 仅由 provider 返回 pointer。
- `cancellation_token.h`：`TopologyTaskExecutor` 是 friend；include executor 会产生直接循环。
- `worker_oc_server.h`：测试 fixture、isolation coordinator、runtime adapter 均为 friend/owned pointer；析构已在 cpp 定义。
- `worker_topology_metadata_actions.h`：`MetadataManagerHolder` 仅为引用成员。

本轮不以 include 替换这些前置声明。提交规则说明或逐项屏蔽证据即可。

## 6. 函数过长：不混入快修

### 6.1 建议直接屏蔽的 3 条

- `WorkerOCServer::CreateObjectCacheWorkerServices`：composition-root wiring，拆分只会增加跳转和私有 helper。
- `WorkerOCServer::ConfigureTopologyBuilder`：builder wiring 单一职责，拆分会增加 capture/生命周期噪声。
- `TopologyEngine::IsControlBackendReachableFromPeers`：51 行的单一 bounded probe loop，不值得为 1 行阈值拆函数。

### 6.2 需要单独 SDD/TDD 的 4 条

- `WorkerOCServiceImpl::RecoverMetadataOfRestartedWorkers`：可按事实解析/对象选择与恢复结果发布划分，但需保持
  generation、首事实诊断和 evidence 发布顺序。
- `WorkerOCServiceImpl::GetReadyToWork`：混合 reconciliation quorum、health、topology completion 和 evidence；
  值得后续重构，但属于高可靠状态机，不能作为 CodeCheck 快修。
- `WorkerOCServiceImpl::ReconcileNetworkRecoveryOwnership`：generation、fanout 注册、terminal handler 和调度顺序
  不能变化。
- `WorkerOCServiceImpl::ScheduleReconciliationRequest`：包含 at-most-once completion 和多层异常边界，拆分风险最高。

若后续整改，优先把已有业务阶段变成同类内部方法，不新增 Manager/Facade/Provider 类，不新增小文件。

## 7. 格式项：5 条暂不改

- `worker_oc_server.cpp`：1 条 Config 初始化缩进、3 条函数实参续行。
- `topology_controller.cpp`：1 条 lambda 返回引用的 `&` 位置。

当前 `clang-format` 会稳定地产生告警中的形式。为满足另一套格式规则而加 `clang-format off`、临时 lambda
变量或仅调整空格，会导致格式回退、增加函数行数或制造纯噪声。优先提交工具链冲突证据并屏蔽；只有 CodeCheck
是阻塞门禁且支持稳定的局部 suppression 时才处理。

## 8. 中控执行 allowlist

首批只允许修改：

```text
.skills/ds-pr-review/tests/test_sensitive_scan.py
src/datasystem/master/object_cache/master_oc_service_impl.cpp
src/datasystem/worker/object_cache/worker_oc_service_impl.cpp
src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.cpp
```

预期是注释、命名常量、等价时间单位表达和两处测试 lint suppression。禁止借机拆函数、改 timeout、改 retry、
改 recovery/admission 语义或新增抽象。

## 9. 验证门禁

1. 修改前再次确认 exact head；若中控分支已前进，重新映射位置。
2. 每个 C++ 文件执行 repo 约定的 `clang-format -i`，确认没有扩大格式 diff。
3. `git diff --check`。
4. 运行 `.skills/ds-pr-review/tests/test_sensitive_scan.py` focused tests。
5. 对两个 C++ target 做最小编译/已有 focused UT；构建失败必须按环境与源码分别归因。
6. 运行 repo-local sensitive scan，只报告脱敏后的类别/路径。
7. 重新导出 CodeCheck，预期关闭 9 条源码项和 2 条测试项；其余 24 条按本说明申请屏蔽或进入后续 SDD。
8. 禁止 push 到 openeuler；任何推送先核对 remote，仅按中控授权推到 yche/yche-huawei fork。不得手工 `/retest`
   或改变 WIP/PR 状态。

