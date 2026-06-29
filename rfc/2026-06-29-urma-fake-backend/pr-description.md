# URMA Fake Backend - PR1129

**Branch:** `feat/urma-fake-r11-rebase`  
**HEAD:** `0899800baff320ea2849799aae37352c6442f07f`  
**PR:** https://gitcode.com/openeuler/yuanrong-datasystem/pull/1129  
**RFC:** `rfc/2026-06-29-urma-fake-backend`

---

## Summary

- 新增 URMA fake backend，使无 RNIC 环境也能运行 Object/KV URMA UT/ST。
- fake 用 memfd + UDS `SCM_RIGHTS` 模拟同节点跨进程远端 DMA，payload 不走 TCP。
- URMA SDK ABI 通过 dlopen fake sentinel + `ds_urma_fake_*` 映射，业务路径仍以 URMA API 形态调用。
- 支持 CQE error、wait timeout、async event、queue full、handshake 等失败注入，覆盖 fallback、disable fallback、reconnect、failover。
- 尽量把 `USE_URMA_FAKE` 收敛在 fake ABI/dlopen/backend/test 边界；业务逻辑继续优先使用 `USE_URMA`。
- 保留 `docs/source_zh_cn/design_document/urma_fake_validation_20260628.md` 作为 PR 内验证记录。

## Design

```text
Object/KV URMA path
  -> UrmaManager / fast_transport_manager_wrapper
      -> dlopen fake sentinel
      -> ds_urma_fake_* ABI
      -> FakeUrmaBackend
          -> FakeContext / FakeJfc / FakeSeg / FakeJetty / FakeTjetty
          -> memfd resolver + UDS fd transfer
          -> shared page memcpy + fake completion
```

关键边界：

| 边界 | 说明 |
|------|------|
| ABI mirror | SDK/C ABI 名称、结构和 opaque handle 保持贴近 URMA |
| 数据面 | 同节点 memfd 共享页；UDS 只传元信息和 fd |
| 失败注入 | fake 生成 URMA failure 信号，fallback 仍由业务层处理 |
| 生产能力 | 只用于 CI/本地/沙箱，不用于生产和性能评估 |

## Test Plan

远端 tiantiyun：

```bash
cmake --build /home/ds-pr1129-urma-fake-e03ccb54-build -j40 --target common_urma_fake
cmake --build /home/ds-pr1129-urma-fake-e03ccb54-build -j40 --target ds_ut datasystem_worker_bin

cd /home/ds-pr1129-urma-fake-e03ccb54-build
./tests/ut/ds_ut --gtest_filter="*Urma*:*URMA*:*urma*" --gtest_also_run_disabled_tests
./tests/st/ds_st_object_cache --gtest_filter="*Urma*:*URMA*:*urma*" --gtest_also_run_disabled_tests
./tests/st/ds_st_kv_cache --gtest_filter="*Urma*:*URMA*:*urma*" --gtest_also_run_disabled_tests
```

当前结果：

| 类别 | 结果 | XML |
|------|------|-----|
| URMA UT sweep | 75/75 PASS | `/tmp/pr1129_codecheck_fix_urma_ut.xml` |
| Object URMA ST sweep | 68/68 PASS | `/tmp/pr1129_codecheck_fix_object_full.xml` |
| KV URMA ST sweep | 11/11 PASS | `/tmp/pr1129_codecheck_fix_kv_full.xml` |

## Review Notes

- `urma_api_compat.h`、`ds_urma_fake_*` 和部分 SDK mirror 命名是 ABI 兼容需要，不建议按项目 C++ 命名风格强改。
- address-to-pointer 转换位于 fake DMA 边界，属于模拟远端地址语义的必要实现。
- fallback case 验证的是 URMA 失败后的业务降级，与 fake backend 本身不是同一层语义。
- fake 性能不能代表真实 URMA 性能。

## Deferred

- developer guide 是否入仓为正式文档。
- 长时间压测下 JFC completion 堆积、UDS fd 生命周期和 fork 后清理观测。
- 后续新 URMA ABI 的 fake mirror 和 UT 模板化。
