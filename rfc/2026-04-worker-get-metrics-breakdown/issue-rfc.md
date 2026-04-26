# [RFC]：Worker Get 路径时延可观测定界（性能定位 + 跨进程对照）

## 背景与目标描述

KV / ObjectCache **读（Get）** 路径在排障时存在多类可观测性缺陷，与 [ZMQ TCP/RPC metrics](../2026-04-zmq-rpc-metrics/issue-rfc.md) 的「**故障/性能定界**」互补：ZMQ 层回答 **client↔worker 通信框架** 是否清白的；本 RFC 回答 **worker 业务侧** 在 **一次读** 上时间花在 **本进程线程池、Master 元数据、寻址、跨 worker、对端 URMA 数据面** 的哪一截。

**A. 性能定位缺失（时延「黑盒」或误导）**

1. **`worker_process_get_latency` 在 MsgQ 开启时严重低估**，仅覆盖到**提交线程池之前**，与「线程池里真正跑完 `ProcessGetObjectRequest`」不对等，无法代表 worker 处理读的业务耗时。
2. **线程池排队**与**业务执行**无独立桶，慢时无法判断是**调度/饥饿**还是**业务逻辑/IO**。
3. **`worker_rpc_get_remote_object_latency`（id=13）在发起拉数的 worker 与提供数据的 worker 上混用同一名字**，avg/max 无法区分 **outbound** 与 **inbound**，跨机对照失效。
4. **HashRing / 路由到 Master 地址**、**QueryMeta 返回后本地后处理**无独立时延，大块时间不可分。
5. **`worker_urma_write` / `worker_urma_wait` 为全进程混桶**，无法单独「定界」**Worker↔Worker 拉数**时的数据面 URMA。

**B. 定界能力目标（与 ZMQ 协同）**

- 在 **无全链路 trace 拼接** 的前提下，凭各进程 `metrics_summary` 的 **avg/max** 与下表**证据组合**，将慢读**收敛到 1～2 个可行动因**；并与 `zmq_*` 流式时延、**`client_rpc_get_latency`** 联读，完成 **Client ↔ 链路 ↔ Worker 业务** 的纵贯定界。

**非目标**：

- 不在此 RFC 内要求 `client_rpc_get` 严格等于各段之和（多进程墙钟并行）。
- 不为「**仅** 因本次对端 `GetObject*` 而发生的 QueryMeta」单开直方图（对端主路径常不调；见 [README 非目标](./README.md#非目标刻意缩小范围)）。

**关联**：

- 上游实现落点、指标全表、Mermaid 时间线树：[design.md](./design.md)
- 分阶段修改顺序：[modification_plan.md](./modification_plan.md)
- 互补：**[ZMQ TCP/RPC metrics issue-rfc](../2026-04-zmq-rpc-metrics/issue-rfc.md)**（`zmq_client_queuing_latency` / `zmq_rpc_e2e_latency` 等）

---

## 建议的方案

基于已合入的 `datasystem::metrics` 轻量框架与现有 `KvMetricId` 枚举，**不重组**已有 id 顺序；在 **`URMA_IMPORT_JFR` 与 `KV_METRIC_END` 之间追加** 新 histogram；**仅** 将 id=**13** 的 **Prometheus 名与 C++ 枚举** 改为 **outbound** 语义，并全仓替换引用；其余逻辑在既有路径上加 `ScopedTimer` / `Timer::Observe`。

### Layer 0：客户读 E2E（不改名，作联读基线）

| 指标名 | 类型 | 测量点 | 定界价值 |
|--------|------|--------|----------|
| `client_rpc_get_latency` | Histogram | 已有 | Client 读路径 **E2E**；与 `worker_process_get`、`zmq_rpc_e2e_latency` 对照，**差值**偏向链路或 ZMQ 层 |

### Layer 1：Entry worker 线程池与 handle E2E

| 指标名 | 类型 | 测量点 | 定界价值 |
|--------|------|--------|----------|
| `worker_get_threadpool_queue_latency` | Histogram | 仅 **MsgQ**：`Execute` 前 → 回调首行 | **排队/调度** 是否主因 |
| `worker_get_threadpool_exec_latency` | Histogram | 回调内 **`ProcessGetObjectRequest`** 整段 | **业务处理** 是否主因 |
| `worker_process_get_latency` | Histogram | **语义修正**：MsgQ 下为 **queue+exec** 的 E2E；非 MsgQ 为 **exec** 或与 exec 同值 | 与上两项做 **闭合核对**；**删** 外层对 `getProc_->Get` 的误导 `METRIC_TIMER` |

### Layer 2：元数据与寻址、QueryMeta 之后

| 指标名 | 类型 | 测量点 | 定界价值 |
|--------|------|--------|----------|
| `worker_rpc_query_meta_latency` | Histogram | 已有 | Worker→Master **QueryMeta** |
| `worker_get_meta_addr_hashring_latency` | Histogram | `GetMetaAddressNotCheckConnection` **非 centralized** 整段 | 路由/hashring/解析 **到 master 地址** 的开销 |
| `worker_get_post_query_meta_phase_latency` | Histogram | `QueryMetadataFromMaster` 成功且 `after_query_meta` 之后，至本段处理结束 | **元数据已回** 后的本地重逻辑（锁/批量/remote/L2 等） |

### Layer 3：跨 worker（发起 / 对端被拉，分桶）

| 指标名 | 类型 | 测量点 | 定界价值 |
|--------|------|--------|----------|
| `worker_rpc_remote_get_outbound_latency` | Histogram | `WorkerRemoteWorkerOCApi::GetObjectRemote` | **本 worker 调对端** 的 RPC+payload 时延（**原 id=13 改名**） |
| `worker_rpc_remote_get_inbound_latency` | Histogram | `WorkerWorkerOCServiceImpl` 服务 `GetObjectRemote*` | **对端被拉** 的服务侧时延，与 **outbound** 分离 |

### Layer 4：对端数据面 URMA（与通用 `worker_urma_*` 双写）

| 指标名 | 类型 | 测量点 | 定界价值 |
|--------|------|--------|----------|
| `remote_worker_urma_write_latency` | Histogram | `UrmaManager` 写路径，且 **UrmaRemoteDataProviderMetricsScope** 有效 | 仅 **W↔W 拉数** 的写侧 |
| `remote_worker_urma_wait_latency` | Histogram | `WaitToFinish` 等，同上 scope 有效 | 仅 **W↔W 拉数** 的完成等待；与全量 `worker_urma_wait` **对照** 定界「普遍慢」 vs 「仅拉数面慢」 |

### 性能 Breakdown（ASCII 树）

墙钟上 **Client / Entry / Peer** 多为**并行**；下树只描述 **指标名** 的**包含/先后**与 **RPC 边**（**非** client = entry + peer 的代数相加）。**链路侧** 用 **ZMQ 分段** 见 [ZMQ issue-rfc](../2026-04-zmq-rpc-metrics/issue-rfc.md)（`zmq_*` / `zmq_rpc_e2e_latency` 等）。

```text
[Client 进程]
  client_rpc_get_latency ..................... client 读 E2E（Histogram us）
         |
         |  .......... 差值/联读: 对 zmq_client_queuing, zmq_rpc_e2e, zmq_rpc_network, ...
         |
         v
[Entry worker 进程]
  worker_process_get_latency .................  handle 总时延(修正后: MsgQ ~ queue+exec)
      |
      |-- worker_get_threadpool_queue_latency ...  仅 MsgQ: Execute 前 -> 回调起
      '-- worker_get_threadpool_exec_latency ....  ProcessGetObjectRequest
              |
              |-- worker_get_meta_addr_hashring_latency
              |-- worker_rpc_query_meta_latency
              |-- worker_get_post_query_meta_phase_latency
              '-- worker_rpc_remote_get_outbound_latency
                        |
        [ cross-worker RPC / data path ]   |
                        |                  |
                        v                  v
                 [Peer worker 进程] <------'
                      |
                      |-- worker_rpc_remote_get_inbound_latency
                      |-- remote_worker_urma_write_latency   (与下项「双写」定界 W<->W)
                      |-- remote_worker_urma_wait_latency
                      |-- worker_urma_write_latency ....... 全进程 URMA(混桶)
                      |-- worker_urma_wait_latency ........ 全进程 URMA(混桶)
                      '-- worker_rpc_query_meta_latency .... 仅进程级样本(非目标: 不绑单次 pull)
```

**辅助公式（自证，非恒等式）**：

```text
同进程（MsgQ 开）: worker_get_threadpool_queue + worker_get_threadpool_exec
                   ≈ worker_process_get  （允许计时边界微差）

定界联读: client_rpc_get 高 且  worker_process_get 低
          → 慢很可能不在 entry worker 业务环，看 ZMQ / client 侧（ZMQ issue-rfc）
```

### 定界决策树

```text
用户读慢（client_rpc_get avg/max 高）？
      │
      ├── zmq_* /  queue  层已高？ → 定界到 ZMQ/排队（ZMQ issue-rfc + threadpool queue）
      │
      ├── worker_process_get 高，queue 高、exec 低？ → 线程池饥饿/调度
      ├── exec  高，query_meta 高？ → Master 或到 Master 网络
      ├── exec  高，post_query_meta 高？ → 元数据返回后的本地逻辑
      ├── exec  高，hashring 高？ → 寻址/路由侧
      ├── outbound 高，对端 inbound 同量级？ → 对端同担；inbound 低则查网络/超时
      └── 对端 remote_worker_urma_wait 高 且  worker_urma_wait 也高？
              ├── 是 → 普遍 URMA/设备面
              └── 仅 remote 高 → 定界在「拉数数据面」路径
```

> 对端 `worker_rpc_query_meta`：**进程级** 样本，不保证与单次 **inbound** 绑定；**专桶** 为一期不做的 [非目标](./README.md#非目标刻意缩小范围)。

---

## 涉及到的变更

### 新增文件

| 文件 | 说明 |
|------|------|
| `yuanrong-datasystem/src/datasystem/common/rdma/urma_metrics_peer.h` | 对端拉数数据面 **RAII 上下文**（thread-local 深度） |
| `yuanrong-datasystem/src/datasystem/common/rdma/urma_metrics_peer.cpp` | 同上实现 |
| `yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh` | 从日志中 **grep 本 RFC 相关 metric 名** 的辅助脚本（已存在可仅更新 pattern） |

### 修改文件

| 文件 | 改动说明 |
|------|----------|
| `src/datasystem/common/metrics/kv_metrics.h` / `kv_metrics.cpp` | 追加 7+ 个 histogram id 与 `MetricDesc`；id=13 改为 **outbound** 名与枚举名 |
| `src/datasystem/common/rdma/CMakeLists.txt` | `urma_metrics_peer.cpp` 加入 `FAST_TRANSPORT` 或等价库源列表 |
| `src/datasystem/common/rdma/urma_manager.cpp` | 条件双写 `remote_worker_urma_*`（`#ifdef USE_URMA` 与现有块一致） |
| `src/datasystem/worker/object_cache/service/worker_oc_service_get_impl.cpp` | `Get`：queue/exec/E2E；`ProcessObjectsNotExistInLocal`：post_query_meta 段 |
| `src/datasystem/worker/object_cache/worker_oc_service_impl.cpp` | 去掉对 `getProc_->Get` 整段的误导 `WORKER_PROCESS_GET` timer |
| `src/datasystem/worker/object_cache/worker_worker_oc_api.cpp` | outbound `METRIC_TIMER` |
| `src/datasystem/worker/object_cache/worker_worker_oc_service_impl.cpp` | inbound；`GetObjectRemoteImpl` / `WaitFastTransportAndFallback` 加 **Scope** |
| `src/datasystem/worker/cluster_manager/etcd_cluster_manager.cpp` | `GetMetaAddressNotCheckConnection` 非集中式路径 Observe |
| `tests/ut/common/metrics/metrics_test.cpp` | 枚举数、id13 名、新 id 的摘要断言 |

### 不变项

- **不** 改 `StatusCode` / 对外**业务 API 签名**。
- **不** 改 [ZMQ issue-rfc](../2026-04-zmq-rpc-metrics/issue-rfc.md) 已合入的 zmq 埋点语义。
- **不** 在 Master 上为本需求单独加 Get 专项 metric（非目标）。

**详图与 Mermaid 树**：[design.md 附录](./design.md#附录时间线树tree)

---

## 测试验证

### UT（Bazel，推荐，增量/缓存效率更高）

```bash
export DATASYSTEM_ROOT=/path/to/yuanrong-datasystem
cd "$DATASYSTEM_ROOT"
# 可选: export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
bazel test //tests/ut/common/metrics:metrics_test --test_output=errors
# 全输出: --test_output=all
```

### UT（CMake，备选）

```bash
cd /path/to/yuanrong-datasystem/build
ctest -R metrics_test -V
```

### 构建验证（Bazel，按需）

```bash
cd "$DATASYSTEM_ROOT"
bazel build //src/datasystem/common/metrics:common_metrics --jobs=8
bazel build //src/datasystem/common/rdma:common_rdma --jobs=8
# 以实际 WORKSPACE 中 target 名为准
```

### 线上/集成日志（定界验收）

- 在 **`xqyun-32c32g`** 或同环境跑会触发 Get / remote / URMA 的场景（st / smoke），对 worker、client 日志执行：

```bash
bash yuanrong-datasystem-agent-workbench/scripts/metrics/grep_get_latency_breakdown.sh "$LOG_DIR"
```

- **必须生成性能 Breakdown 树**：脚本在扫描日志后**末尾**会输出与 [§ 性能 Breakdown（ASCII 树）](#性能-breakdownascii-树) 一致的 **Generated: Get performance breakdown tree** 块，验收材料中应**保存该段输出**（或重定向到文件，如 `get_breakdown_tree.txt`）。仅打印树、不扫日志时：`bash .../grep_get_latency_breakdown.sh --tree-only`。
- 确认各 metric 的样本行 / `metrics_summary`：`client_rpc_get_latency`；MsgQ 下 `queue`/`exec`/`process_get` 关系；跨 worker 时 **outbound** 与对端 **inbound**；对端 `remote_worker_urma_*`（在 URMA 与路径同时满足时）。

具体命令与说明见 [design.md §7](./design.md#7-验收与回归)。

---

## 遗留事项（待人工决策）

1. **Grafana/告警** 若仍绑定**字符串** `worker_rpc_get_remote_object_latency`：需改为 `worker_rpc_remote_get_outbound_latency` 并**发版说明**；历史曲线与改名后**不可直比** `avg` 语义混桶期。
2. **Dashboard**：建议增加「Client / Entry / Peer」**三块面板**，按进程角色分屏，与 ZMQ 面板**并排**做纵贯定界。
3. **Bazel 版本**：若与仓库已有 `.bazelversion` 不一致，以仓库为准；`USE_BAZEL_VERSION` 以团队约定为准（参见 ZMQ issue-rfc 验证节）。
4. **二期**（本 RFC 不实现）：`remote_worker` 专指元数据 `QueryMeta` 桶、或 trace id 与 metric 关联，需**额外**调用链打标，单独 RFC。

## 期望的反馈时间

- 建议反馈周期：**5～7 天**。
- 重点反馈：
  1. id=13 **改名** 的 breaking change 对现网**告警/看板**是否可接受、是否需要**双发一期**（仅讨论，实现可选）；
  2. `worker_process_get` **语义更新** 后，是否需在文档/运维手册中**强提示**与历史数据不可比；
  3. 排障 SOP 是否将本 RFC 与 [ZMQ issue-rfc](../2026-04-zmq-rpc-metrics/issue-rfc.md) 的**定界树**合并为一张运维图。

---

## 文档与状态

- 本 `issue-rfc` 为 **ZMQ issue-rfc 同构模板**；落地细节、附录树图见 [design.md](./design.md) 与 [README.md](./README.md)。
