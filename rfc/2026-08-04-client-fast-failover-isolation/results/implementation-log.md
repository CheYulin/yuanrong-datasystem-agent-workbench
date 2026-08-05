# 2026-08-04 实施记录

## 关联

| 项目 | 内容 |
|---|---|
| datasystem PR | [openeuler/yuanrong-datasystem!1840](https://gitcode.com/openeuler/yuanrong-datasystem/merge_requests/1840) |
| branch | `feat/fast-failover-isolation-20260804` |
| base | `main/master@ac6b8edd3c4c627bffac886b35f3f01cda1365bd` |
| commit | `d90da667f60992c3ced5da14e7416de9669f85bc` |

## 已落地

- `node_timeout_s` 作为主动隔离窗口；`node_dead_timeout_s` 作为租约兜底。
- Worker 侧统计 metadata/connectivity RPC 连续失败，命中阈值后随 keepalive 上报 `failed_targets`。
- Coordinator 侧汇总 `target -> reporter -> receiveTime`，多 reporter 命中后触发 hashring 更新。
- witness probe 继续保护租约兜底路径；主动失败 summary 不被单个 witness reachable 绝对阻断。
- Bazel 全包路径修复：
  - 删除不存在源码对应的 stale `//:hashring_parser_file`。
  - 补齐 `tests/st/cluster:st_cluster` 的 `common.h` 与 direct deps。

## 验证

| 项 | 结果 |
|---|---|
| CodeGraph | shared index up to date: 2,159 files / 53,469 nodes / 157,732 edges |
| `git diff --check` | PASS |
| C++ `git clang-format --diff main/master` | PASS |
| CMake build | PASS，`-- build datasystem success!`，`elapsed_sec=920.45` |
| Bazel build | PASS，`-- build datasystem (bazel) success!`，`elapsed_sec=83.14` |
| UT | PASS，6 cases |
| ST | PASS，`CoordinatorBackendClusterThreeWorkerTest.KilledWorkerScaleDownAllowsNewWritesReadableFromOtherWorker`，`36.83s` |

## 说明

- CMake 日志有三方 patch 已应用后的非致命 warning。
- strip 阶段有 `debuglink section already exists` warning，命令退出码为 0。
