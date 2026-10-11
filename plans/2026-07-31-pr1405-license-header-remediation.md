# PR1405 新增源码许可证头整改清单

## 1. 核验基线

- PR1405 exact head：`861fa07dfac02d394219d62cb3275e461eb4d899`
- merge-base：`main/master@7507f139fdb1222b29eb77542223a68dea6cd19f`
- 新增文件：49 个
- 新增生产目录文件：37 个，其中 C/C++ 35 个、构建文件 2 个
- 新增测试 C++：12 个
- 新增 C/C++ 合计：47 个

目标许可证头为用户确认的紧凑 Apache-2.0 全文模板：

```cpp
/**
 * Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 * http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
```

## 2. 结论

| 分类 | 生产源码 | 测试 | 合计 | 行数影响 |
|---|---:|---:|---:|---:|
| 短版许可证，需要补全 8 行 | 22 | 5 | 27 | +216 |
| 完整但年份为 2024，需要确认后改 2026 | 5 | 1 | 6 | 0 |
| 完整但含两行多余 `*` 空行，统一紧凑模板 | 0 | 2 | 2 | -4 |
| 已符合 2026 紧凑模板 | 8 | 4 | 12 | 0 |
| 合计 | 35 | 12 | 47 | 净 +212 |

按当前 PR 的首次可见历史，6 个标记 2024 的文件均在 2026-07-22 才进入仓库分支，没有找到 2024 的路径历史。
若没有外部代码来源证明，建议统一改为 2026；若来自 2024 已有受版权保护实现，应保留原年份并补来源说明。

## 3. P0：补全短版许可证的生产源码（22 个）

### cluster（4 个）

```text
src/datasystem/cluster/executor/cancellation_token.h
src/datasystem/cluster/executor/topology_cleanup_effect.h
src/datasystem/cluster/executor/topology_phase_action.h
src/datasystem/cluster/runtime/topology_availability.h
```

### worker/object_cache（7 个）

```text
src/datasystem/worker/object_cache/recovery/metadata_recovery_summary.h
src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.cpp
src/datasystem/worker/object_cache/recovery/object_cache_recovery_state.h
src/datasystem/worker/object_cache/worker_topology_metadata_actions.cpp
src/datasystem/worker/object_cache/worker_topology_metadata_actions.h
src/datasystem/worker/object_cache/worker_topology_object_cache_actions.cpp
src/datasystem/worker/object_cache/worker_topology_object_cache_actions.h
```

### worker/runtime（9 个）

```text
src/datasystem/worker/runtime/worker_control_backend_probe.cpp
src/datasystem/worker/runtime/worker_control_backend_probe.h
src/datasystem/worker/runtime/worker_recovery_evidence.cpp
src/datasystem/worker/runtime/worker_recovery_evidence.h
src/datasystem/worker/runtime/worker_runtime_facade.cpp
src/datasystem/worker/runtime/worker_runtime_facade.h
src/datasystem/worker/runtime/worker_runtime_types.h
src/datasystem/worker/runtime/worker_topology_runtime.cpp
src/datasystem/worker/runtime/worker_topology_runtime.h
```

### worker composition（2 个）

```text
src/datasystem/worker/worker_coordination_backend_factory.cpp
src/datasystem/worker/worker_coordination_backend_factory.h
```

## 4. P0：补全短版许可证的测试（5 个）

```text
tests/ut/worker/object_cache/migrate_data_admission_test.cpp
tests/ut/worker/object_cache/object_cache_recovery_state_test.cpp
tests/ut/worker/worker_control_backend_probe_test.cpp
tests/ut/worker/worker_runtime_facade_test.cpp
tests/ut/worker/worker_service_impl_admission_test.cpp
```

## 5. P1：年份需要确认并建议改为 2026（6 个）

这些文件已有完整紧凑 Apache-2.0 文本，只需在确认无 2024 外部来源后替换年份：

```text
src/datasystem/worker/object_cache/central_metadata_address_resolver.cpp
src/datasystem/worker/object_cache/central_metadata_address_resolver.h
src/datasystem/worker/object_cache/object_metadata_coordination_reader.cpp
src/datasystem/worker/object_cache/object_metadata_coordination_reader.h
src/datasystem/worker/object_cache/service/object_metadata_reader.h
tests/ut/worker/object_cache/central_metadata_address_resolver_test.cpp
```

## 6. P1：完整但模板不紧凑（2 个）

以下文件在 `You may obtain a copy of the License at` 后和 URL 后各多一行 ` *`。法律文本完整，但与用户指定模板
不一致；删除两行空注释即可：

```text
tests/st/client/object_cache/migration_target_admission_test.cpp
tests/ut/worker/worker_oc_server_recovery_evidence_test.cpp
```

## 7. 无需修改的新增 C/C++ 文件（12 个）

### 生产源码（8 个）

```text
src/datasystem/worker/object_cache/object_table.cpp
src/datasystem/worker/object_cache/object_table.h
src/datasystem/worker/object_cache/recovery/object_cache_recovery_evidence.cpp
src/datasystem/worker/object_cache/recovery/object_cache_recovery_evidence.h
src/datasystem/worker/runtime/worker_isolation_coordinator.cpp
src/datasystem/worker/runtime/worker_isolation_coordinator.h
src/datasystem/worker/runtime/worker_runtime_state.cpp
src/datasystem/worker/runtime/worker_runtime_state.h
```

### 测试（4 个）

```text
tests/ut/worker/object_cache/object_cache_recovery_evidence_test.cpp
tests/ut/worker/worker_isolation_coordinator_test.cpp
tests/ut/worker/worker_runtime_facade_topology_availability_test.cpp
tests/ut/worker/worker_runtime_state_test.cpp
```

## 8. 新增构建文件不纳入整改（2 个）

```text
src/datasystem/worker/runtime/BUILD.bazel
src/datasystem/worker/runtime/CMakeLists.txt
```

同目录现有 `BUILD.bazel`/`CMakeLists.txt` 也不带许可证头，因此本轮不要单独给这两个文件加头，避免改变仓库
构建文件惯例。

## 9. 修改过但非新增的历史短版文件（21 个）

这 21 个文件的短版头在 merge-base 已存在，PR1405 对其许可证块改动为 0。默认不混入“新增文件许可证整改”；
只有许可证门禁按整文件扫描并明确阻塞时，再作为第二批机械修复。

### 生产源码（10 个）

```text
src/datasystem/cluster/control/topology_controller.cpp
src/datasystem/cluster/control/topology_controller.h
src/datasystem/cluster/control/topology_failure_classifier.cpp
src/datasystem/cluster/control/topology_failure_classifier.h
src/datasystem/cluster/coordination_backend/ds_coordination_backend.h
src/datasystem/cluster/executor/topology_phase_callbacks.h
src/datasystem/cluster/runtime/topology_engine.cpp
src/datasystem/cluster/runtime/topology_engine.h
src/datasystem/cluster/runtime/topology_runtime_types.h
src/datasystem/coordinator/topology_recovery_manager.cpp
```

### 测试（11 个）

```text
tests/ut/cluster/hash_algorithm_test.cpp
tests/ut/cluster/testing/fake_coordinator_service_proxy.h
tests/ut/cluster/topology_controller_runtime_test.cpp
tests/ut/cluster/topology_controller_test.cpp
tests/ut/cluster/topology_engine_test.cpp
tests/ut/cluster/topology_failure_classifier_test.cpp
tests/ut/cluster/topology_plan_builder_test.cpp
tests/ut/cluster/topology_runtime_composition_test.cpp
tests/ut/cluster/topology_task_executor_test.cpp
tests/ut/cluster/ub_health_lease_sync_test.cpp
tests/ut/worker/object_cache/topology_phase_callbacks_test.cpp
```

若第二批也补全，额外增加约 168 行许可证文本。两个 rename 文件
`worker_topology_phase_callbacks.{h,cpp}` 已经是完整许可证，不在此清单。

## 10. 执行要求

1. 修改前重新确认 PR1405 exact head，重新枚举 `diff-filter=A`，避免中控继续提交后清单漂移。
2. 第一批 allowlist 只包含第 3 至第 6 节的 35 个新增文件。
3. 只替换文件开头许可证块；不得改 Description、include、空行、命名或业务代码。
4. 不使用批量全文件格式化制造正文 diff。遵循仓库 C++ 格式门禁后，必须确认最终 diff 仍只有许可证块。
5. 验证 35 个目标文件与模板逐行一致；年份确认项单独记录来源结论。
6. `git diff --check`，并运行 repo-local license/CodeCheck 和 sensitive scan。
7. 许可证注释不要求重新跑全量 ST；至少执行现有静态门禁，并与中控当前 build/UT 结果合并，不伪造 PASS。
8. 禁止 push 到 openeuler，禁止手工 `/retest` 或改变 WIP/PR 状态；任何 fork 推送服从中控授权。

