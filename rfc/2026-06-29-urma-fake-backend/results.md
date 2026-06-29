# URMA Fake Backend - Results

**Status**: In-Progress  
**Last updated**: 2026-06-29 00:14 CST  
**HEAD**: `0899800baff320ea2849799aae37352c6442f07f`

---

## 1. 构建验证

| 命令 | 结果 |
|------|------|
| `cmake --build ... -j40 --target common_urma_fake` | PASS |
| `cmake --build ... -j40 --target ds_ut datasystem_worker_bin` | PASS |

## 2. UT

```bash
./tests/ut/ds_ut \
  --gtest_filter="*Urma*:*URMA*:*urma*" \
  --gtest_also_run_disabled_tests \
  --gtest_output=xml:/tmp/pr1129_codecheck_fix_urma_ut.xml
```

| 项 | 结果 |
|----|------|
| Suites | 10 |
| Tests | 75 |
| Passed | 75 |
| XML | `/tmp/pr1129_codecheck_fix_urma_ut.xml` |

## 3. Object URMA ST

```bash
./tests/st/ds_st_object_cache \
  --gtest_filter="*Urma*:*URMA*:*urma*" \
  --gtest_also_run_disabled_tests \
  --gtest_output=xml:/tmp/pr1129_codecheck_fix_object_full.xml
```

| 项 | 结果 |
|----|------|
| Suites | 17 |
| Tests | 68 |
| Passed | 68 |
| XML | `/tmp/pr1129_codecheck_fix_object_full.xml` |

覆盖观察：

- `UrmaObjectClientTest.*`
- disabled event mode cases
- `UrmaClientWorkerDisableUDS.*`
- worker disconnect / CQE error injection
- heartbeat reconnect
- async event
- NUMA affinity
- fallback / disable fallback
- eviction manager end-to-end

## 4. KV URMA ST

```bash
./tests/st/ds_st_kv_cache \
  --gtest_filter="*Urma*:*URMA*:*urma*" \
  --gtest_also_run_disabled_tests \
  --gtest_output=xml:/tmp/pr1129_codecheck_fix_kv_full.xml
```

| 项 | 结果 |
|----|------|
| Suites | 1 |
| Tests | 11 |
| Passed | 11 |
| XML | `/tmp/pr1129_codecheck_fix_kv_full.xml` |

覆盖观察：

- local discovery uses UDS
- remote UB/TCP/URMA error switch by discovery
- switch failure keeps current worker available
- healthy window reset
- min samples / threshold update
- local fail + remote fail + switch back

## 5. Codecheck-oriented cleanup

| 项 | 处理 |
|----|------|
| `fake_endpoint.h` 私有 `FdGuard` | 移除 header helper，`.cpp` 复用公共 `Raii` |
| UDS fd collection `const_cast` | `CollectReceivedFds(msghdr &)`，直接遍历 cmsg |
| SCM_RIGHTS fd `reinterpret_cast` | 改为 `std::memcpy` 逐个读取 fd |
| `ImportSeg` 复杂度 | 拆 `HasRegisteredImportEndpoint` / `OpenImportEndpointMemfd` / `MmapImportedMemfd` |

## 6. 不修改/豁免理由

| 类别 | 理由 |
|------|------|
| `urma_api_compat.h` SDK/C ABI mirror | 必须匹配 URMA SDK 命名、结构、宏和回调形态 |
| `ds_urma_fake_*` C ABI | fake 对外导出符号，需保持 C ABI 和 dlopen entry 一致 |
| fake object opaque handle | 模拟 SDK opaque handle，raw pointer/forward declaration 有边界意义 |
| address-to-pointer fake DMA | fake 模拟远端地址访问的核心边界，不能机械替换 |
| 非 fake 业务文件 | PR1129 目标是本节点 URMA mock，不扩大业务逻辑改动 |

## 7. 待跟踪

- GitCode 门禁 codecheck 刷新后确认未解决项是否下降。
- 如剩余 SDK mirror 问题仍出现，补 PR comment 说明 ABI 豁免原因。
- 评估 developer guide 是否入仓为正式设计/开发文档。
