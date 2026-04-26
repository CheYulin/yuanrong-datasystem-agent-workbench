# RFC: ZMQ RPC Metrics 修复 - ENABLE_PERF=false 分段时间（0.8.1 分支）

- **Status**: **Draft**
- **Started**: 2026-04-25
- **Target Branch**: `main/0.8.1`
- **上游 PR**：[#706](https://gitcode.com/openeuler/yuanrong-datasystem/pull/706)（E2E ZMQ 分段时延 + `ENABLE_PERF=false` 可观测）
- **Goal**: 以**最小化修改**在 0.8.1 上复现 PR #706 行为；**Bazel 构建** `datasystem_worker` + whl；**复用** `run_smoke.py` 做端到端 ZMQ metrics 收集与验收

---

## 与 PR #706 对齐：最小化修改面

| 层 | 做什么 | 不做什么 |
|----|--------|----------|
| **Datasystem 源码** | 在 `main/0.8.1` 上 **cherry-pick / 等价合并 PR #706 单 commit**（`RecordTick` / `GetTotalElapsedTime`、ZMQ 埋点、`kv_metrics` 7 个 Histogram） | 不复刻设计、不另开一套 tick 名或 metric 名 |
| **Workbench** | 已维护：`run_smoke.py` 的 `ZMQ_METRIC_PATTERNS` 覆盖 7 个 `zmq_*` 分段指标；**仅 rsync 同步** workbench 到远程 | 不新增第二套 E2E 脚本；smoke 内已起 etcd + worker |
| **构建** | 远程/本地均走 **`build.sh -b bazel`**（与仓库约定一致；whl 由 Bazel 管线产出后 `pip install --user` 供 Python 客户端） | 不把本验证绑死在 CMake-only 路径上 |

**一句话**：验证侧 = **Bazel 产物 + whl +** 现有 `run_smoke.py`；C++ 侧见下节 **0.8.1 现状**（未必需整包 cherry-pick [#706](https://gitcode.com/openeuler/yuanrong-datasystem/pull/706)）。

### `main/0.8.1` 现状（2026-04-26 核对）

在 `remotes/main/0.8.1` 上已具备与 PR #706 **同目标** 的能力，实现路径与 master 上的单 commit **不完全相同**：

| 能力 | 0.8.1 中的位置 |
|------|----------------|
| 与 `ENABLE_PERF` 无关的 tick 写入 | `zmq_constants.h`：`RecordTick()` / `GetTotalTicksTime()`（始终往 `MetaPb.ticks` 写） |
| 7 个 Queue Flow Histogram | `kv_metrics.{h,cpp}` id 37–43；`zmq_stub_impl.h`：`RecordRpcLatencyMetrics()` |
| Server 侧分段 | `zmq_service.cpp`：`RecordServerLatencyMetrics()` 等 |

对 `8772945b` / PR #706 做 **盲 cherry-pick 会与 0.8.1 冲突**（`zmq_common.h` / `kv_metrics.cpp` 等已有并行演进）。**最小推进方式**：先跑通下面 **harness**，再仅对 diff 做针对性补洞（若 metrics 仍缺）。

### 一键 harness（Bazel + `run_smoke.py`）

```bash
cd /path/to/yuanrong-datasystem-agent-workbench
export DATASYSTEM_ROOT=/path/to/yuanrong-datasystem
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
# 本机
bash scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh --local
# 或已 rsync 后的远程
bash scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh --remote
```

脚本路径：[`harness_zmq_metrics_e2e.sh`](../../scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh)

---

## 问题背景

### 问题描述
当 `ENABLE_PERF=false` 时，ZMQ RPC metrics 无法记录分段时间，导致：
- 无法自证清白 network 和 RPC framework (send/recv) 的时间
- TCP 故障时无法通过 metrics 定位问题

### 根本原因
原代码中 `GetLapTime()` 和 `GetTotalTime()` 在 `ENABLE_PERF` 关闭时直接返回 0，不记录任何 tick：

```cpp
// 原实现（问题代码）
inline uint64_t GetLapTime(MetaPb &meta, const char *tickName) {
#ifdef ENABLE_PERF
    return RecordTick(meta, tickName);  // 仅在 ENABLE_PERF 时记录
#else
    (void)meta;
    (void)tickName;
    return 0;  // 始终返回 0，tick 被丢弃
#endif
}
```

---

## 修复方案（来源 PR #706）

### 核心改动
1. **新增 `RecordTick()` 函数** - 始终记录 tick，不受 `ENABLE_PERF` 控制
2. **新增 `GetTotalElapsedTime()` 函数** - 始终计算总时间，不受 `ENABLE_PERF` 控制
3. **修改 metrics 记录函数** - 使用新的始终启用的函数

### 修改文件清单

| 文件 | 修改内容 |
|------|---------|
| `zmq_common.h` | 新增 `RecordTick()`、`GetTotalElapsedTime()` |
| `zmq_service.cpp` | 拆分 `RecordServerLatencyMetrics`；ns→us 转换 |
| `zmq_stub_conn.cpp` | `GetLapTime` → `RecordTick` |
| `zmq_stub_impl.h` | `GetTotalTime` → `GetTotalElapsedTime` |
| `kv_metrics.h/cpp` | 新增 7 个 RPC Queue Flow Latency metrics |

### 新增 Metrics（用于自证清白）

| Metric | 说明 |
|--------|------|
| `ZMQ_CLIENT_QUEUING_LATENCY` | Client 框架队列等待 |
| `ZMQ_CLIENT_STUB_SEND_LATENCY` | Client Stub 发送 |
| `ZMQ_SERVER_QUEUE_WAIT_LATENCY` | Server 队列等待（自证 network） |
| `ZMQ_SERVER_EXEC_LATENCY` | Server 业务执行（自证业务逻辑） |
| `ZMQ_SERVER_REPLY_LATENCY` | Server 回复入队（自证 RPC framework） |
| `ZMQ_RPC_E2E_LATENCY` | 端到端延迟 |
| `ZMQ_RPC_NETWORK_LATENCY` | 网络延迟 = E2E - ServerExec |

---

## 落地位置（datasystem 0.8.1）

- 在 `main/0.8.1` 上合入 [PR #706](https://gitcode.com/openeuler/yuanrong-datasystem/pull/706) 的变更；若 0.8.1 与 master 有分歧，**以 PR #706 的 diff 为蓝本**做冲突解决，**保持** `RecordTick` / 7 个 Histogram / Server 端 `ServiceToClient` 后打点等行为不变。
- 参考单 commit（以你本地 `git log` 为准）：`d3f7dffd` — `feat: Add ZMQ RPC tracing and latency metrics`。

---

## 验证方案

### 工作流：rsync → **Bazel** 构建（worker + whl）→ **Python smoke** E2E

```
本地 DS + workbench
        │  rsync（排除 .git/build/.cache 等，见 remote_build_run_datasystem.rsyncignore）
        ▼
远程 xqyun-32c32g: ~/workspace/git-repos/{yuanrong-datasystem,yuanrong-datasystem-agent-workbench}
        │  build.sh -b bazel -t build  （DS_OPENSOURCE_DIR 固定缓存，见 .cursor 规则）
        │  pip install --user <bazel 产出的 openyuanrong_datasystem-*.whl>
        ▼
python3 scripts/testing/verify/smoke/run_smoke.py   # 内含 etcd + workers + 客户端负载 + metrics 解析
```

> `remote_build_run_datasystem.sh` 当前默认调用 **不带** `-b bazel` 的 `build.sh`。要满足「Bazel + whl」：在 **同步完成后** 于远程 `yuanrong-datasystem` 根目录执行 `bash build.sh -b bazel -t build -j <N>`，再安装 whl；或在本机/CI 用 [`scripts/build/build_bazel.sh`](../../scripts/build/build_bazel.sh) 作为统一入口后 rsync 产物（以团队实际流程为准）。

### 执行步骤

#### 步骤 1: 基于 `main/0.8.1` 合入 PR #706

```bash
cd ~/workspace/git-repos/yuanrong-datasystem
git fetch origin
git checkout -b fix/zmq-rpc-metrics-0.8.1 origin/main/0.8.1
git cherry-pick d3f7dffd  # 若有冲突，按 PR #706 意图保留 ZMQ / kv_metrics 行为
```

#### 步骤 2: rsync 同步到远程

任选其一（与团队习惯一致即可）：

```bash
cd ~/workspace/git-repos/yuanrong-datasystem-agent-workbench
bash scripts/development/sync/sync_to_xqyun.sh
```

或使用 [`remote_build_run_datasystem.sh`](../../scripts/build/remote_build_run_datasystem.sh) 做 **同步**；该脚本随后会跑 **默认 CMake 的** `build.sh`。**若坚持全程 Bazel**，可在同步后 **不再依赖** 脚本内编译段，或二次 SSH 用下面命令覆盖为 Bazel 产物。

#### 步骤 2b: 远程 Bazel 构建 + 安装 whl

```bash
ssh xqyun-32c32g 'bash -s' <<'EOF'
set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${HOME}/workspace/git-repos/yuanrong-datasystem"
bash build.sh -b bazel -t build -j "$(nproc)"
WHL="$(find bazel-bin output -maxdepth 6 -name 'openyuanrong_datasystem-*.whl' 2>/dev/null | head -1 || true)"
if [[ -n "${WHL}" ]]; then python3 -m pip install --user "${WHL}"; fi
EOF
```

本地亦可用同参数：`bash build.sh -b bazel -t build` 或 workbench 包装脚本 [`build_bazel.sh`](../../scripts/build/build_bazel.sh)（其内部即 `build.sh -b bazel`）。

> **与「最小化修改」关系**：若误用 `remote_build_run_datasystem.sh` 且只得到 CMake 的 `build/`，`run_smoke.py` 里 `find_worker_binary()` 会尽量找 `bazel-bin/.../datasystem_worker`；**仍应以 Bazel 编出与 PR #706 一致的 worker + whl** 为验收基线，避免混用两套后端导致指标缺失难以归因。

#### 步骤 3: 运行 smoke_test（脚本内含 etcd + worker 部署）

```bash
ssh xqyun-32c32g \
  'cd ~/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/testing/verify/smoke && \
   python3 run_smoke.py'
```

#### 步骤 4: 检查 metrics 输出

```bash
cat ~/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_*/metrics_summary.txt
```

#### 步骤 4c: 仅从 Worker glog grep（不看 client）

`metrics_summary.txt` 会合并 worker 与 client 来源。若本轮**只验 worker 进程**里的 `metrics_summary` / `zmq_*`，请按 [test-walkthrough.md — Worker-only grep](test-walkthrough.md#worker-only-grep) 只对 `worker-*_datasystem_worker*.INFO.log`（或 `workers/worker-*/*.INFO.log`）做 `grep`，**不要**扫 `clients/glog_*`。

#### 步骤 4a（推荐，先于 summary）：核对 tick 链

代码在 worker 出队后、`ServiceToClient` 追加 `SERVER_SEND` 后、以及 stub/unary 计算 7 指标前会打一行 **`[ZmqTickOrder]`**（`chain=` 为 meta 中 ticks **顺序** 的 `name@ts`；`SERVER_EXEC_NS@…[exec_dur_ns]` 表示该 tick 的 `ts` 是 **handler 耗时** 而非墙钟）。

在 smoke 或单测跑完后从 worker/日志目录检索：

```bash
rg '\[ZmqTickOrder\]' -n /path/to/workers/worker-*/ 2>/dev/null | head -40
# 或 rg '\[ZmqTickOrder\]' -n /path/to/results/smoke_test_*/*.log
```

**期望（逻辑顺序，不要求每个 RPC 都出现在同一文件）**：

- `unary_rsp_before_metrics` 或 `stub_rsp_before_metrics` 的 `chain` 在末尾应能看到 `CLIENT_RECV`，且含服务端 ticks（`SERVER_DEQUEUE` … `SERVER_EXEC_END` → `SERVER_EXEC_NS[exec_dur_ns]` → `SERVER_SEND` 等，中间可有 PERF 名）。
- `worker_after_server_exec_and_metrics`：应在 `SERVER_EXEC_END` 后紧跟 `SERVER_EXEC_NS`（duration）。
- `service_to_client_after_server_send`：应在链尾含墙钟的 `SERVER_SEND`。

若链顺序与 [sequence_diagram.puml](sequence_diagram.puml) 明显不符，再对照 C++ 打点与本次日志。

#### 步骤 4b: 解析 metrics 汇总

在确认 tick 链合理后，再对照 `metrics_summary.txt` 中 7 个 `zmq_*` 指标与 [验收标准](#验收标准)。

### 验收标准

1. ✅ `ENABLE_PERF=false` 时 ZMQ metrics 正常打印
2. ✅ `zmq_client_queuing_latency` 有值 → 自证 Client 框架队列等待
3. ✅ `zmq_client_stub_send_latency` 有值 → 自证 Client Stub 发送
4. ✅ `zmq_server_queue_wait_latency` 有值 → 自证 network 等待时间
5. ✅ `zmq_server_exec_latency` 有值 → 自证业务逻辑执行时间
6. ✅ `zmq_server_reply_latency` 有值 → 自证 RPC framework 回复时间
7. ✅ `zmq_rpc_e2e_latency` 有值 → 端到端延迟
8. ✅ `zmq_rpc_network_latency` 有值 → 网络延迟 = E2E - ServerExec

---

## 本目录文件

| 文件 | 说明 |
|------|------|
| [design.md](design.md) | 与 PR #706 对齐的修复设计 |
| [test-walkthrough.md](test-walkthrough.md) | Bazel + whl + `run_smoke.py` 串讲 |
| [results.md](results.md) | 验证记录 |
| [pr-description.md](pr-description.md) | 提 PR 文案模板 |
| [sequence_diagram.puml](sequence_diagram.puml) | 请求/双 worker 路径/回包 **`ZmqRecordServerSendLatencyMetrics`** / client **`RecordRpcLatencyMetrics`**（与 0.8.1 代码对齐） |

---

## 相关文档

- [PR #706](https://gitcode.com/openeuler/yuanrong-datasystem/pull/706)
- [run_smoke.py](../../scripts/testing/verify/smoke/run_smoke.py)
- [remote_build_run_datasystem.sh](../../scripts/build/remote_build_run_datasystem.sh)
