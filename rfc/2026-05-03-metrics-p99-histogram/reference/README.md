# Reference patches

- **`yuanrong-datasystem-p99-only.patch`** — 完整 metrics P99 变更：`metrics.h` / `metrics.cpp`、`metrics_test.cpp`、`tests/st/common/metrics/` 下 **`histogram_p99_perf_test`（`cc_test`）**。应用后请 `bazel test //tests/ut/common/metrics/... //tests/st/common/metrics:histogram_p99_perf_test --define=enable_urma=false`（或使用 RFC **`bazel_run_tests.sh`**）。

- **`yuanrong-datasystem-p99-only.md`** — 与 patch 配套的设计说明、测试表与命令（含为何 perf/verify 用 `bazel build`/`run` 而非 `ds_cc_test`）。

- **`metrics-p99-histogram-only.patch`**（可选保留）— 较早的「核心 + `metrics_test`」裁剪；若与主干差异较大，优先以 **`yuanrong-datasystem-p99-only.patch`** 为准。

已移除历史上的「metrics + ZMQ + ST」合并 patch，避免与本 RFC 范围混淆。
