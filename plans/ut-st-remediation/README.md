# UT / ST 全量跑测与整改跟踪

本目录存放 **CMake 全量 `ds_ut` / `ds_st`** 跑测结论与整改项跟踪。

**主文档**：[UT-ST全量跑测结论与整改计划.md](./UT-ST全量跑测结论与整改计划.md)

**逐用例耗时/成败（部分 ST）**：[runs/README.md](./runs/README.md)（TSV + 解析脚本；**UT 暂无完整表**）。

**Bazel 构建失败归因与修复计划**：[bazel-build-failure-analysis-and-fix-plan.md](./bazel-build-failure-analysis-and-fix-plan.md)（配置 vs 环境、分阶段整改）。

**Bazel 远端修复流程与运行报告（ZMQ metrics）**：[../../results/20260417_remote_bazel_zmq_metrics_workflow_and_report.md](../../results/20260417_remote_bazel_zmq_metrics_workflow_and_report.md)（`.bazelversion`、build/test 命令与耗时）。

**跑测环境（参考）**：远端 `root@38.76.164.55`，构建目录 `.../yuanrong-datasystem/build`，二进制 `tests/ut/ds_ut`、`tests/st/ds_st`。
