# 用例构造与验证观测（Po2、连接数、URMA、UB）

- **Status**: Draft  
- **与** [validation-po2-client-count.md](./validation-po2-client-count.md) **关系**：该文件是 **Case 0–3 的骨架**；本文说明 **怎么设计变量**、**记什么**、**怎么比**，以及 **URMA/UB** 与 **etcd 陈旧度** 的观测注意点。  
- **环境**：**ST/联调/内部压测** 为主；`xqyun-32c32g` 等执行细节见工作区规则与现网基线，不在此固定 **Prometheus 名字**（落地后替换占位）。

## 0. 用例落地顺序与代码覆盖（团队约定）

| 维度 | 约定 |
|------|------|
| **用例/场景** | **先** 做 **非 URMA** 路径的 Po2+连接数、回退、基线对比（不编译 `USE_URMA`、或**运行时**关闭 `FLAGS_enable_urma` / 以 **TCP/非快路径** 为主），环境简单、**先** 把选路与负载暴露跑通；**再** 做 **URMA/UB** 专项（`USE_URMA` 开启、`TryUrmaHandshake`、切流、重试/析构，见 [notes-use-urma-ub-lifecycle.md](./notes-use-urma-ub-lifecycle.md)）。 |
| **代码** | **不** 因「用例后做」而只实现单一路径：**`SwitchToStandby` / Po2 / 负载读** 在实现上**必须**对 **非 URMA 与 URMA 两条 C↔W 路径都可验证**；MR 中 **UT/集成** 需覆盖**双路径**（至少：非 URMA 一组、URMA 或模拟握手失败回退 一组），与用例的**分阶段**正交。 |

---

## 1. 验证目标分条（对应用例）

| 目标 | 要证明什么 | 主要操纵因子 |
|------|------------|--------------|
| **Po2 + 连接数** | 多 Client 在 **同一故障** 下，**存活 Worker 上连接数** 的偏斜**小于**基线（固定序首成） | 开/关 Po2、`GetAll` 里**是否有** 可比连接数、候选集 **≥2** |
| **回退安全** | 缺负载或候选 **&lt;2** 时，行为与 **现网一致** | 不推 etcd 数、关 gflag、双候选里人为让一侧不可达 |
| **URMA 生命周期** | 切流后 **新 Standby 上** 建链/重试/析构**无** 明显泄漏、**无** 旧 Worker 上长期悬挂 | 反复故障、`USE_URMA` 与**回退 TCP** 两条线 |
| **里程碑 B**（后续） | Po2 比较量换 **近窗 UB** 时，在 **可加重数据面** 的负载下 **L** 有区分度 | 读放大、W↔W 迁移、滑窗 T |

---

## 2. 用例构造：推荐维度

把一次「可重复报告」的实验写清 **五元组**（可做成表格/脚本头注释）：

1. **拓扑**：`N` 个 Worker、是否 **三节点** 与 RFC 主场景一致；`M` 个 **Client 进程/实例**（多进程更能暴露「同时涌向同一 Standby」）。  
2. **连接布局（偏斜度）**  
   - **高偏斜**（主测 Po2）：绝大多数 Client 初连**同一** Worker（如 w1），故障 w1 时**迁移压力最大**。  
   - **对照**：均匀分布或随机首连，用于排除「仅因初连就均衡」的误解。  
3. **故障方式**：`SIGKILL` 进程、断网/iptables、**pause 容器** 等；需记录 **T0** 与**恢复 T1**，便于对齐日志与 metric 时间窗。  
4. **控制面/数据面**  
   - 里程碑 A **主体验证** 在 **非 URMA** 下**优先** 完成（见 **§0**），再在 **USE_URMA** 上复现；若 **USE_URMA** 开启，仍建议 **同场景** 再跑 **关 `FLAGS_enable_urma`/回落 TCP** 作对照，区分「选路问题」与「URMA 握手洪峰问题」。  
5. **随机性**：Po2 含随机抽对；**重复跑 K 次**（如 K≥10 或 30），报告**分布**而不仅是单次截图。

---

## 3. 里程碑 A：Po2 + 连接数 —— 推荐用例模板

### 3.1 基线（无 Po2 或 旧行为）

- **目的**：与「改进后」同一脚本、仅 **gflag/配置** 不同。  
- **构造**：高偏斜 + w1 故障，**记录** 故障后 30s～2min 内 w2、w3（及存活集）的 **per-Worker 连接数时间序列**。

### 3.2 处理组（开 Po2 + 可读连接数）

- **前置条件**：`ObtainWorkers` / Heartbeat 等路径上 **L(worker)=active_client_count** 在**两次随机候选**上**可比较**（同一次快照、或**陈旧上界** 已知）。若实现里 **先 Put 再选**，需在文档中写清。  
- **构造**：同 3.1；**对比** 同 K 次重复下的**均衡度指标**（下节）。

### 3.3 边界用例（必做小集）

| 用例 | 构造 | 预期 |
|------|------|------|
| **单候选** | 只剩一个 Standby（缩容/隔离两台） | **不** 进入 Po2，与现网一致 |
| **无负载** | 关闭 etcd 扩展或未刷新 | **回退** 固定序，不劣化 |
| **双候选一挂** | 人为让首选失败（CONTINUE） | **次候选** 被尝试，不崩溃；日志可审计 |

---

## 4. 观测什么、怎么记

### 4.1 核心：per-Worker 连接数

- **权威来源**：**Worker 进程** 内 `ClientManager::GetClientCount()`（与 `ResMetricCollector` 注册的 **`ACTIVE_CLIENT_COUNT`** 一致，见 `worker_oc_server.cpp`）。  
- **现成可观测：Worker `resource.log`**（在 `log_dir` 布局下，ST 中一般为 **`$ROOT/worker{i}/log/resource.log`**；Client 的 `FLAGS_log_dir` 为 `$CASE/client` 时，相对路径常写作 **`$FLAGS_log_dir/../worker{i}/log/resource.log`**）：  
  - 由 **`log_monitor=true`** 周期性写入；**取末行**、按 **` | `** 切分；**前 7 个字段** 为时间戳等**元数据**，之后按 **`ResMetricName` 与 `SHARED_MEMORY` 的次序** 与各资源列一一对应（与 `stream_observability_test` / `kv_client_log_monitor_test` 同口径）。  
  - **`ACTIVE_CLIENT_COUNT`** 列下标 = `(int)ResMetricName::ACTIVE_CLIENT_COUNT - (int)ResMetricName::SHARED_MEMORY`（在去掉前 7 段后的**指标段**里）。  
  - **适用**：Po2 里程碑 A 在 **未接 etcd 扩展** 时，仍可用 **每 Worker 本地 `resource.log` 末行** 作 **L = 当前连接数** 的**机架侧**观测/脚本解析；**里程碑 B 的 UB** 与 **etcd 对外** 口径仍按 design。  
- **其他展示**：etcd `KeepAliveValue` 扩展、管理面、**Client `metrics_summary`（JSON）** 等与场景互补；`po2_standby_switch_observability_st_test` 中同时打了 **`[ST_OBSERVABILITY] resource_log … ACTIVE_CLIENT_COUNT=…`** 与 **kv `DumpSummaryForTest`**。  
- **采样**：**故障前** 稳态 1 次 + **故障后** 每 Δt（如 5s）**若干** 点，持续 **60s～5min**（看迁移是否长尾）。  
- **对齐**：所有 Worker **时钟** 或 至少 **同一时区 + NTP**；**`resource.log` 行时间** 与 故障 **T0** 对齐时需考虑 **`log_monitor_interval_ms`** 的一拍延迟。

### 4.2 均衡度指标（可自动化）

在 **某时刻** 对存活 Worker 集合 `{Wi}`，令 `c_i` 为各节点连接数（只计 **业务 Client**，若实现排除内部连接需写进脚本）：

- **峰谷差**：`max(c_i) - min(c_i)` —— 直观。  
- **标准差/变异系数**：`stddev` / `mean` —— 规模变化时可比。  
- **报告方式**：**基线 vs Po2** 的上述指标在 **K 次实验** 中的 **中位数** 与 **(p10, p90)** 或箱线图；**单次** 结果仅作样例。  
- **注意**：`c_i` **滞后** 于「切换决策」：etcd **Put 周期** 与 **RegisterClient 完成** 可能不同步；**判据** 中应写「在 **T0+Δ 之后** 的窗口内」取数，**Δ** 由实现/节流决定（如 2×心跳周期）。

### 4.3 Client 侧与选路可观测

- **切换原因与候选序**（若已有 INFO 日志）：**GetStandbyWorkersForSwitch** 后打印的**候选表**、Po2 选中的 **先试对**、**TrySwitch** 成功/失败。  
- 用于**调试**与**回归**：确认「先试轻载」在日志上可核对，**非** 仅看最终连接数。

### 4.4 etcd 与「陈旧度」

- 若 L 从 **GetAll** 解析：记录 **value 的 timestamp** 与 **当前时间** 差，避免 **Po2 基于过时两节点** 选错；验证文档中可列 **可接受上界**（如「若 &gt;X 秒则视为不可比并回退」与实现一致）。

---

## 5. URMA / UB 专项（与 Po2 正交）

**构造**

- 在 **USE_URMA** 下重复 **3.1 / 3.2** 的**同一** 故障轮替；**额外** 跑一遍 **关闭 URMA 或 强制 TCP**，其余不变。  
- **重复切流**：对单 Client 做 **N 次** 换 Standby（或轮换 kill），检查 **无** 异常堆积（见 [notes-use-urma-ub-lifecycle.md](./notes-use-urma-ub-lifecycle.md) §7）。

**观测**（与具体 metric 名解耦，用「查什么」描述）

- **建链**：`TryUrmaHandshake` / `FastTransport` 相关 **成功/回退** 日志；故障窗口内 **单位时间** 失败次数。  
- **重试**：`stopUrmaHandshakeRetry_` 设位后，**无** 针对已销毁 CWA 的**持续**重试 log。  
- **销毁**：`Disconnect` 与 Worker 侧 `RemoveClient` 成对；可选 **lsof/连接数/UM 资源** 前后对比（环境允许时）。  
- **URMA 与 L 的错位**：在 Po2+连接数 场景下，**记录**（若可）「URMA READY」与 **etcd 上 count** 的**先后**；若 Po2 读的是**连接数**而 URMA 仍**握手ing**，不期望**完美** 与 UB 级均衡一致，**里程碑 B** 再收紧。

**里程碑 B 追加**

- 在 **w1/w2** 上**人为** 制造**不对称 UB**（大读、W↔W 拷贝等），**滑窗 T** 内 **L=UB 字节/速率** 应有**明显差**；Po2 应更常让**轻载**先试（需**足够 K** 与**随机种子** 记录以便复现）。

---

## 6. 与自动化、CI 的衔接

- **UT**：`design.md` §6 的 **gflag/候选数/回退** 用例；**不替代** 本节 ST。  
- **ST/长稳**：`validation-po2-client-count.md` 的 **0–3 步** + 本文 **§3.3、§5**，形成 **清单**；跑完**导出** `RUN_INFO_*.txt`：拓扑、M、K、T0、版本、gflag 快照。  
- **回归**：每次改 Po2 或 etcd 扩展，**重跑** 处理组与基线各 **K** 次，**对比** `max-min` 与 `stddev` 的**中位数** 是否不劣于上一版**宽容阈值**（由团队定 5% 或只盯「不退化」）。  
- **Bazel ST 目标**：见下 **§7**；**支持**，与现网 OC ST 同构。

---

## 7. Bazel ST 落点与构建（`yuanrong-datasystem`）

结论：**已支持** 用 Bazel 编译、显式运行 Object Cache 相关 **ST**；新增 Po2/切流/双路径验证时，**按现有模式** 加 `ds_cc_test` 即可。

| 项 | 说明 |
|----|------|
| **规则** | `ds_cc_test`（`bazel/build_defs.bzl`）基于 `cc_test`，带 **gtest / gtest_main**、`WITH_TESTS=1`、`-Itests`，默认再打上 **`ds_test`** tag。 |
| **推荐包路径** | `tests/st/client/object_cache/`，与同目录 `object_client_scale_test`、`object_client_with_tcp_test`、`urma_object_client_test` 一致；多节点可参考 **`//tests/st/cluster:st_cluster`**、**`OCClientCommon` + `ExternalCluster`**（见 `object_client_scale_test.cpp` 等）。 |
| **`manual` tag** | 当前大量 OC ST 使用 **`tags = ["manual"]`**：含义是**不**指望 `bazel test //tests/st/...` 在**无过滤**时默认全跑满；**跑法**为**显式指定 target**（见下 **已合入示例**）。CI 若只跑 `//...` 且排除 `manual`，则 ST 需**单独 job** 显式 label。 |
| **非 URMA 构建** | 根目录 **`config_setting //:enable_urma`**（`define_values: enable_urma=true`）为关时，`//src/datasystem/common/rdma:common_rdma` 的 select **不** 链入 `urma_manager`（见该 `BUILD.bazel`）。**默认不传** `--define=enable_urma=true` 即 **优先覆盖非 URMA/TCP 主路径** ST，与本文 **§0** 一致。 |
| **URMA 构建** | 需要链上 URMA 实现时与现有一致：`--define=enable_urma=true`；**头文件/设备** 需本机或远程（如 `xqyun-32c32g`）具备与 `urma_manager` 相同前提，否则 ST 应 **GTEST_SKIP** 或文档说明环境。 |
| **已合入示例（非 URMA + 指标）** | **`//tests/st/client/object_cache:po2_standby_switch_observability_st_test`**：`TcpPathMetricsDumpAndFailover` 在三 Worker + etcd + ServiceDiscovery 下 **Create/Seal**，`ShutdownNode worker0` 后再次 **Create/Seal**；日志带 **`[ST_OBSERVABILITY]`**：① **`metrics::DumpSummaryForTest` JSON**（`client_put_*` / `client_get_*`）；② **`resource.log` 末行解析的 `ACTIVE_CLIENT_COUNT`**（**worker0 故障前 ≥1、failover 后 worker1 ≥1**）。**timeout=long**（多进程启停）。 |

**命令示例**（需本机 **Bazel 7.4.1** 与 `.bazelversion` 一致，或在 **`xqyun-32c32g`** 上执行）：

```bash
cd yuanrong-datasystem
bazel build //tests/st/client/object_cache:po2_standby_switch_observability_st_test
bazel test //tests/st/client/object_cache:po2_standby_switch_observability_st_test --test_output=all

# 非 URMA 对照（默认未开 enable_urma）
bazel test //tests/st/client/object_cache:object_client_with_tcp_test

# URMA 链入时（与根目录 config_setting 一致）
bazel test //tests/st/client/object_cache:urma_object_client_test --define=enable_urma=true
```

**默认远端构建（推荐）**：在 **agent-workbench** 下用 **`rsync` + `xqyun-32c32g` 上 `bazel`**，并查 **`bazel-bin` / `output_path`** 排查产物，见  
[scripts/build/REMOTE_BAZEL_BUILD.md](../../../scripts/build/REMOTE_BAZEL_BUILD.md) 与 [`rsync_datasystem_remote_bazel.sh`](../../../scripts/build/rsync_datasystem_remote_bazel.sh)；全量 **CMake+ctest** 见 [`remote_build_run_datasystem.sh`](../../../scripts/build/remote_build_run_datasystem.sh)。

**采集**：对测试日志 `grep ST_OBSERVABILITY` 或解析 `event":"metrics_summary` 的 JSON 行。

合入 ST 后，在 **MR 描述** 中写清：**显式 bazel target**、是否依赖 `manual`、以及 **远程/多机** 前提（若有）。

---

## 8. 小结

- **用例** = 明确 **偏斜、M、K、故障方式、URMA 开断** + **开/关 Po2** 成对。  
- **验证** = **时间窗内** per-Worker 连接数 **序列** + **K 次** 的**统计量** + **选路日志** + **etcd 陈旧**；URMA 另加**握手/重试/断开** 证据链。  
- **里程碑 B** 在 **A** 跑通后，把 **L** 换成 **UB 近窗**，**复用** 同一套实验框架，**换** 负载发生方式与**观测** counter。  
- **ST + Bazel**：见 **§7**。
