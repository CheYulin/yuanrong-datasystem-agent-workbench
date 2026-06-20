# `scripts/` 地图（指导 Agent）

优先查阅 **`.skills/`**（`wb-build` / `wb-dev` / `wb-daily` / `wb-perf` / `wb-docs` / `wb-html-publish`），按 skill 表和 `scripts/harness/profiles.yaml` 运行 profile。

## 1. 总览

| 子目录 | 一句话 | Skill |
|--------|--------|-------|
| **`scripts/harness/`** | `ds_harness.py` + `profiles.yaml` 统一入口 | **wb-build / wb-dev / wb-daily / wb-perf** |
| **`scripts/build/`** | 编译链辅助，不是替代 `build.sh` | **wb-build** |
| **`scripts/testing/verify/`** | smoke / UT / ST / 专项门禁 | **wb-dev** / **wb-daily** |
| **`scripts/metrics/`** + DS log scripts | metrics_summary、KV perf Markdown | **wb-perf** / **wb-docs** |
| **`scripts/analysis/perf/`** | perf、锁竞争、executor 曲线 | **wb-perf** |
| **`docs/observable/workbook/`** | Workbook Markdown sources | **wb-docs** |
| **`scripts/development/sync/`** | rsync、HTML git | **wb-html-publish** |

## 2. 按任务选脚本

### 2.1 构建画像 — **wb-build**

| 目标 | 命令 |
|------|------|
| CMake dry-run | `python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json` |
| Bazel dry-run | `python3 scripts/harness/ds_harness.py build --backend bazel --dry-run --json` |
| 构建 evidence | `python3 scripts/harness/ds_harness.py build --profile build.quick` |

### 2.2 特性验证 / 门禁（无 sudo）— **wb-dev**

| 目标 | 脚本 |
|------|------|
| 开发闭环 | `python3 scripts/harness/ds_harness.py dev --profile dev.default` |
| Smoke（tiantiyun） | `bash scripts/testing/verify/smoke/run_smoke_remote.sh` |
| UT | `bash scripts/testing/verify/ut/run_ut_remote.sh` |
| ST | `bash scripts/testing/verify/st/run_st_remote.sh` |
| KV executor 注入 + 源码关键字审计 | `bash scripts/testing/verify/validate_kv_executor.sh`（日常加 `--skip-build`） |
| URMA/TCP 观测日志 | `bash scripts/testing/verify/validate_urma_tcp_observability_logs.sh <log_dir>` |
| ZMQ metrics E2E | `bash scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh` |

### 2.3 每日构建 — **wb-daily**

| 目标 | 命令 |
|------|------|
| 全量 dry-run | `python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json` |
| 全量质量构建 | `python3 scripts/harness/ds_harness.py daily --profile daily.full` |

### 2.4 性能分析 — **wb-perf**

| 目标 | 脚本 |
|------|------|
| 热点 dry-run | `python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json` |
| 回归 profile | `python3 scripts/harness/ds_harness.py perf --profile perf.regression` |
| Executor inline vs injected | `python3 scripts/analysis/perf/kv_executor_perf_analysis.py` |
| 锁 baseline 采集 / 对比 | `collect_client_lock_baseline.sh` / `compare_client_lock_baseline.sh` |
| bpftrace 工作流 | `bash scripts/analysis/perf/run_kv_lock_ebpf_workflow.sh` |
| URMA 宏索引 | `python3 scripts/development/code-index/refresh_urma_index_db.py` |

### 2.5 文档交付 — **wb-docs**

| 目标 | 脚本 / 路径 |
|------|-------------|
| KV perf Markdown 报告 | `python3 scripts/metrics/gen_kv_perf_report.py` |
| Bugfix ↔ FEMA HTML | `python3 scripts/analysis/generate_bugfix_fema_report.py` |
| 可观测工作簿（已提交 xlsx） | `docs/observable/workbook/` |

## 3. 环境变量

- **`DATASYSTEM_ROOT`** / **`YUANRONG_DATASYSTEM_ROOT`**：datasystem 绝对路径（两仓不同级时必设）。  
- **`CTEST_OUTPUT_ON_FAILURE=1`**：失败时打印用例输出。  

## 4. 相关文档

| 文档 | 用途 |
|------|------|
| [`INDEX.md`](../../INDEX.md) | Skill 路由总表 |
| [`scripts/harness/verify_matrix.yaml`](../../scripts/harness/verify_matrix.yaml) | 改动类型 → 最低验证级别 |
| [`cmake-non-bazel.md`](../verification/cmake-non-bazel.md) | `build.sh`、CTest 组合 |
