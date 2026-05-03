# RFC: Histogram P99（metrics）

**Status**: Draft  
**Date**: 2026-05-03  

**Datasystem 落点**：`src/datasystem/common/metrics/*`，`tests/ut/common/metrics/metrics_test.cpp`。

## 本目录内容

| 文件 | 说明 |
|------|------|
| [design.md](design.md) | P99 桶、算法、JSON、`Observe` 与分桶约定 |
| [verification.md](verification.md) | 验证记录（bucket / 算法 / UT） |
| [implementation-plan.md](implementation-plan.md) | 实施步骤与 `Observe`/patch 缺口说明 |
| [issue-rfc.md](issue-rfc.md) | Issue / RFC 正文 |
| [pr-description.md](pr-description.md) | PR 描述模板 |
| [reference/](reference/README.md) | **`yuanrong-datasystem-p99-only.patch`**（推荐）、说明 **`yuanrong-datasystem-p99-only.md`** |
| [scripts/rsync_datasystem.sh](scripts/rsync_datasystem.sh) | 本地 `yuanrong-datasystem` → 远端（同 workbench `remote_build_run_datasystem.rsyncignore`） |
| [scripts/bazel_build.sh](scripts/bazel_build.sh) | 远端 `DS_OPENSOURCE_DIR` + `bazel build`（`//tests/ut/common/metrics/...` 与 `//tests/st/common/metrics/...`） |
| [scripts/bazel_run_tests.sh](scripts/bazel_run_tests.sh) | 远端 `bazel test //tests/ut/common/metrics/...`，日志写入 `results/` |
| [scripts/run_full_tests_remote.sh](scripts/run_full_tests_remote.sh) | 顺序执行上述三步 |

## 快速链接

- 上级索引：[../README.md](../README.md)
