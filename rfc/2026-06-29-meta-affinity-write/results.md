# Meta-Affinity Write — 验证结果

**Status**: In-Progress  
**Branch**: `feature/meta-affinity-write` · **HEAD**: `0e644bc4`  
**MR**: [!1151](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1151)

---

## 功能 / UT + ST（tiantiyun-80c128g，2026-06-29）

| 类别 | 结果 | 备注 |
|------|------|------|
| UT `MetaAffinityReplicateTest.*` | **4/4 PASS** | 调度条件、gflag、队列 |
| ST `ColocatePrimaryWithMetaOwnerAndReadLocalCopy` | **PASS** | 双 location + Invalidate 读 primary |
| ST `RemoteOnlyClientPutDirectlyOnMetaOwner` | **PASS** | remote-only 直写 meta owner |
| Get RPC perf ST ×2 | **2/2 PASS** | 4KB，`ASSERT=1` |
| `KVClientCoprocessTest.TestInitEmbeddedWithInvalidParam` | **PASS** | gflag 去重 fix 后 |

---

## Get RPC perf（4KB，tiantiyun，`0e644bc4` 前一轮）

| 场景 | Get avg (µs) | vs 基线 |
|------|-------------:|---------|
| 同节点 colocated Put+Get | 1735 | — |
| 同节点 cross-worker cold Get | 5863 | colocated **~70%** 更快 |
| 跨节点 W0 gateway cold Get | 5889 | — |
| 跨节点 W0 direct cold Get | 1896 | **~68%** 更快 |

详见 [perf-verification.md](./perf-verification.md)。

---

## CI（openEuler 门禁，2026-06-29）

| Check | 初轮 (`420bab11`) | 修复后 (`0e644bc4`) |
|-------|-------------------|---------------------|
| check_code | ❌ | 待重跑 |
| check_package_license | ✅ | — |
| check_sca | ✅ | — |
| x86_64 check_build | ❌ gflag 重复定义 | 待重跑 |
| aarch64 check_build | ❌ | 待重跑 |
| openyuanrong Bazel x86/arm | ❌ `etcd_cluster_manager_header` | **已修** → `cluster_manager_header` |

### 已修 CI 根因

1. **Bazel**：`//src/datasystem/worker/cluster_manager:etcd_cluster_manager_header` 不存在 → `cluster_manager_header`
2. **CMake ST**：`master_address` 在 `worker_oc_server.cpp` 与 `cluster_master_flags.cpp` 重复 `DS_DEFINE` → 统一到 `common_util_gflag` + worker `DS_DECLARE`

---

## Open / Deferred

| Issue | 说明 |
|-------|------|
| [#6](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/6) | Phase 2：同节点 client 直写 meta owner |
| [#7](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/7) | 统一 MetaAffinity / DirectRead ring source |
| [#8](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/8) | Scale 后写路由 ST |
| [#9](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/9) | 非 binary Publish replicate 挂接 |
| [#10](https://gitcode.com/yche-huawei/yuanrong-datasystem/issues/10) | Meta-affinity + Direct Read 组合 ST |
