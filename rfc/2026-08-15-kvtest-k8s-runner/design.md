# KVTest K8s 场景编排器设计

## 1. 背景与目标

当前 KVTest over K8s 的手工流程由多条 `deploy_*.py` 命令组成，覆盖 Pod 创建、wheel 安装、
Coordinator/Worker 生命周期、Client 配置生成与启动、双 Worker 故障注入、日志收集和报告生成。
这些命令本身已有明确职责，但场景参数散落在命令行中，重复运行时容易遗漏旧进程、误用宽泛前缀、
覆盖上一次证据，或者在容器时钟偏差未知的情况下分析故障隔离时间线。

本设计增加一个轻量 Python 编排器。它不重写现有部署脚本，而是按手工流程调用：

- `deploy_pods.py`
- `deploy_worker.py`
- `deploy_coordinator.py`
- `deploy_client.py`
- 仓外 `rolling_upgrade.py`
- 可选的仓外日志报告工具

第一版只支持 K8s。未来裸机或 SSH 模式通过执行后端扩展，不进入本次实现范围。

### 1.1 目标

1. 默认复用现有 Pod，支持多次独立运行。
2. 通过显式危险参数支持重建精确前缀的 Pod。
3. 从手工命令中提取固定环境配置和易变场景配置。
4. 用单个、易读、仅依赖 Python 标准库的脚本完成编排。
5. 在故障注入前后校验容器墙上时钟偏差，并生成可追溯的运行事件记录。
6. 每次运行使用独立结果目录；失败时优先保留现场和收集证据。
7. 所有破坏性操作限定到配置中声明的精确 Pod 前缀。

### 1.2 非目标

- 不替代或重构 DataSystem 仓内现有 `deploy_*.py`。
- 不在第一版中自动 SSH 到 K8s 节点或修改节点时间。
- 不在容器中执行 `date -s`。
- 不自动执行高权限 `ctr image import`。
- 不在第一版中解析全部 DataSystem 日志并推导完整因果链。
- 不支持非 K8s transport。
- 不实现通用 DAG 工作流引擎。

## 2. 交付物和代码边界

设计产物和最终外围自动化放在 `yuanrong-datasystem-agent-workbench`：

```text
rfc/2026-08-15-kvtest-k8s-runner/
└── design.md

scripts/testing/kvtest/
├── kvtest_k8s_runner.py
├── cluster.example.toml
├── scenario.example.toml
├── README.md
└── tests/
    └── test_kvtest_k8s_runner.py
```

运行时只需将编排器和两份实际配置复制到 K8s master。编排器从 `cluster.toml` 的
`workspace.kvtest_dir` 进入 KVTest 工作目录，然后调用该目录中的现有脚本。

DataSystem 产品仓不新增编排逻辑。若实现过程中发现现有 `deploy_*.py` 存在阻断性缺陷，单独形成小改动，
不把其修复混入本外围编排器交付。

## 3. 配置模型

### 3.1 三层优先级

```text
cluster.toml（固定环境）
    < scenario.toml（本次场景）
    < CLI 安全开关（本次动作）
```

同名键由后层覆盖前层。每次运行将合并后的配置写入
`runs/<run-id>/resolved-config.json`，用于复现实验。

实现必须拒绝：

- 未知配置段或字段；
- 类型错误；
- 缺少必填字段；
- 三个角色前缀为空、相同或存在前缀包含关系；
- 相对路径逃逸到 KVTest 工作目录之外的危险输出位置；
- wheel glob 匹配零个或多个文件。

### 3.2 固定环境配置 `cluster.toml`

固定配置描述 K8s 环境和长期稳定的部署拓扑：

- KVTest 工作目录、Python 命令和运行结果根目录；
- namespace、Pod YAML 和 Pod Ready 超时；
- Worker、Client、Coordinator 的精确前缀；
- 节点与副本数、CPU 和内存；
- Worker/Coordinator 端口、远端配置位置和 procmon 策略；
- Client 远端工作目录、SDK 目录、writer 数量和 cluster name；
- 时钟采样次数、RTT 上限和最大允许时钟偏差。

示例文件必须使用占位节点、占位镜像和占位路径，不提交实际内网地址。

### 3.3 易变场景配置 `scenario.toml`

易变配置描述本次实验：

- 场景名称；
- 镜像和 wheel；
- 期望 `dscli --version` 字符串；
- Coordinator 和 Worker 配置模板；
- Client 服务发现类型与地址；
- Client case 配置、生成目录和 deploy JSON；
- Client 启动成功/失败日志模式；
- workload 预热时间、无故障持续时间和故障后观察时间；
- `rolling_upgrade.py` 的 batch、stop method、wait 和观察窗口；
- 收集策略和可选报告命令。

`fault.enabled` 仅表示场景具备故障配置，不授权执行故障。真正注入故障仍需要显式 CLI 动作。

### 3.4 只允许通过 CLI 指定的危险动作

以下行为不得由 TOML 默认开启：

- `--recreate-pods`
- `--kill`
- `--delete-pods`
- `--force`
- `run --with-fault`

故障注入、无条件强杀进程或删除 Pod 必须同时看到动作参数和 `--force`。普通 `run` 不隐式注入故障。
正常 `prepare` 中允许一种边界更窄的 kill fallback：只有先执行优雅 stop、确认超时或残留、配置显式允许，
并且目标属于精确角色前缀时才执行；它不等同于独立的 `cleanup --kill --force`。

## 4. 命令行界面

统一入口：

```bash
python3 kvtest_k8s_runner.py \
  --cluster cluster.toml \
  --scenario scenario.toml \
  <command>
```

第一版命令：

| 命令 | 作用 | 是否修改集群 |
| --- | --- | --- |
| `doctor` | 检查配置、文件、kubectl context、Pod、wheel 和时钟 | 否 |
| `plan` | 打印本次将执行的阶段和命令，不运行 | 否 |
| `prepare` | 准备 Pod、安装包、重启 Coordinator/Worker、生成 Client 配置 | 是 |
| `run` | `prepare` 后启动 Client；可选显式故障注入与收集 | 是 |
| `fault --force` | 对正在运行的场景注入配置中的 Worker 故障 | 是，破坏性 |
| `collect` | 收集 Client、Worker、Coordinator 证据 | 只读远端，写本地 |
| `clock check` | 多轮采样目标 Pod 的墙上时钟 | 否 |
| `cleanup` | 优雅停止和清理目标服务 | 是 |
| `rebuild-pods --force` | 删除并重建三个精确前缀的 Pod | 是，破坏性 |

常用路径：

```bash
# 默认复用 Pod
python3 kvtest_k8s_runner.py --cluster cluster.toml --scenario scenario.toml run

# 显式重建 Pod 后运行
python3 kvtest_k8s_runner.py --cluster cluster.toml --scenario scenario.toml \
  run --recreate-pods --force

# 启动业务并注入场景故障
python3 kvtest_k8s_runner.py --cluster cluster.toml --scenario scenario.toml \
  run --with-fault --force
```

## 5. 手工流程到自动化阶段的映射

### 5.1 阶段 0：`doctor`

在任何修改集群的动作前执行：

1. 加载并校验两份 TOML。
2. 确认 Python 版本支持 `tomllib`。
3. 确认 `kubectl` 可用，打印当前 context 和 namespace。
4. 确认 KVTest 工作目录以及四个 `deploy_*.py` 存在。
5. 确认 Worker、Coordinator、Client 配置文件可读取，JSON 可解析。
6. 确认 Pod YAML、kvtest binary、wheel 和可选仓外工具存在。
7. 确认 wheel glob 唯一匹配。
8. 确认精确前缀互不重叠。
9. 查询三个角色现有 Pod 数量、phase、Ready 状态和所在节点。
10. 检查配置期望数量与实际数量。
11. 运行时钟检查；超过阈值则阻止业务启动。

仓外 `rolling_upgrade.py` 只在执行 `fault` 或 `run --with-fault` 时作为必需依赖。报告工具若配置为
`required = false`，缺失只告警。

### 5.2 阶段 1：Pod 复用或重建

#### 默认复用

- 三类 Pod 数量与配置一致且全部 Ready：直接复用。
- 角色完全不存在：提示使用 `--recreate-pods --force`，不默认创建一半环境。
- 数量不一致、非 Ready 或混合旧环境：停止并打印差异。
- 不使用宽泛前缀兜底。

#### 显式重建

仅在同时出现 `--recreate-pods --force` 时：

1. 将三个精确前缀和将删除的 Pod 列表写入事件日志。
2. 按 Client、Worker、Coordinator 顺序删除精确前缀 Pod。
3. 等待目标 Pod 消失。
4. 使用 `deploy_pods.py deploy --wait` 创建 Worker 和 Client Pod。
5. 使用 `deploy_coordinator.py deploy` 创建 Coordinator Pod；该命令同时安装 wheel 并启动 Coordinator。
6. 等待所有 Pod Ready 并重新核对数量。

Coordinator 的组合式 `deploy` 是现有脚本行为。编排器在本次 run state 中标记 Coordinator wheel 已安装，
避免紧接着重复安装。随后仍执行一次显式 Coordinator 生命周期归一化，以保证复用和重建路径进入相同状态。

如果发现 `ImagePullBackOff`、sandbox 创建失败或 pause/base 镜像缺失，脚本输出相关 Pod、节点和建议的
节点侧镜像检查命令，但不自动运行 `ctr`。高权限镜像导入由操作者在确认目标节点后执行。

### 5.3 阶段 2：替换版本和校验产物

对应手工步骤中的新 wheel 和 KVTest `output`/`build` 替换：

1. 检查 wheel 唯一匹配并记录 SHA-256、大小、mtime。
2. 检查 `output/kvtest` 存在且可执行，记录 SHA-256、大小、mtime。
3. 检查 `build` 和 `output` 是否满足部署脚本约定，不自动复制来源未知的目录。
4. 对需要安装的精确角色前缀调用 `deploy_worker.py install`；刚由
   `deploy_coordinator.py deploy` 安装过的 Coordinator 在本次状态中跳过重复安装。
5. 在全部目标 Pod 执行 `dscli --version`。
6. 若配置了 `expected_version`，所有输出必须包含该字符串；否则停止。

安装过程中任何 Pod 失败都使阶段失败，不允许以部分成功继续启动集群。

### 5.4 阶段 3：Coordinator 归一化

严格参考手工顺序：

1. 若旧 Coordinator 进程存在，先收集到当前 run 的 `preflight/coordinator-old/`。
2. 尝试 `deploy_coordinator.py stop`。
3. stop 超时或失败且配置允许 fallback 时，记录原因后调用精确前缀 kill；普通错误不直接吞掉。
4. `deploy_coordinator.py clean` 清理旧运行目录。
5. `deploy_coordinator.py start` 使用场景配置启动。
6. `deploy_coordinator.py check` 验证进程。
7. 校验端口监听和配置上传成功。

本阶段不会删除 Coordinator Pod。

### 5.5 阶段 4：Worker 归一化

对应手工收集、停止、清理和启动：

1. 收集旧 Worker 日志到 `preflight/worker-old/`。
2. 尝试优雅 stop。
3. 若允许且确有残留进程，再对精确 Worker 前缀执行 kill fallback。
4. clean 旧日志和远端配置。
5. 使用 Worker 配置模板和端口启动全部 Worker。
6. 对每个 Worker 运行 check，并记录启动耗时统计。
7. 任一 Worker 未启动则停止，不启动 Client。

### 5.6 阶段 5：生成 Client 部署配置

对应手工 `gen-config` 和复制 `deploy.json`：

1. 通过精确 Client 前缀发现 Running Pod。
2. 数量必须等于固定配置中的 Client 副本总数。
3. 调用 `deploy_client.py gen-config` 写入独立 generated 目录。
4. 将生成的 `deploy.json` 原子复制到场景指定的 deploy JSON。
5. 保留生成的 `config.json` 作为证据，但业务启动仍使用场景指定 case config。
6. 校验 deploy JSON 的 Pod 名称唯一、instance id 唯一、数量正确。
7. 校验 case JSON 可解析，并把 SDK 日志目录通过 `env.DATASYSTEM_CLIENT_LOG_DIR` 显式注入；
   收集时使用相同目录。

如果 `regenerate_deploy_json = false`，跳过生成，但仍执行相同的内容校验。

### 5.7 阶段 6：启动 Client 作业

1. 若发现旧 kvtest 进程，先停止、收集到 `preflight/client-old/`，再 clean。
2. 运行 `deploy_client.py deploy <deploy-json> <case-json>`。
3. 在启动超时内轮询所有 Client 的 `run.log` 和进程状态。
4. 任一 Client 命中失败模式、提前退出或缺失 `run.log`，本阶段失败。
5. 如果配置了成功模式，所有 Client 必须命中成功模式；为空时以进程存活且无失败模式为成功。
6. 写入 `client.workload.ready` 事件后才允许执行故障注入。

Client Ready 后按场景中的 `[workflow]` 控制时间：

- `warmup_seconds`：业务 Ready 后先稳定运行的时间；`run --with-fault` 在此之后注入故障；
- `post_fault_observe_seconds`：故障工具退出后继续保留业务的观察时间，之后停止并收集；
- `duration_seconds`：无故障 `run` 的运行时间；大于 0 时到期后停止并收集；
- `duration_seconds = 0`：无故障 `run` 一直前台运行，收到 Ctrl+C 后优雅停止并收集。

时长统一使用本地 monotonic clock 计算。Ctrl+C 只中断等待阶段，不跳过后续收集。

### 5.8 阶段 7：双 Worker 故障注入

只由 `fault --force` 或 `run --with-fault --force` 触发：

1. 再次执行容器时钟检查，保存 `clock-before-fault.json`。
2. 记录 `fault.command.starting` 的墙上时间和本地 monotonic 时间。
3. 以参数数组启动 `rolling_upgrade.py`，不使用 `shell=True`。
4. 等待配置的观察窗口，例如 5 秒。
5. 向子进程发送 SIGINT，等待其在限定时间退出。
6. 如果 SIGINT 后仍未退出，记录并终止该工具进程；不扩大 kill 到其他进程。
7. 保存 stdout、stderr、退出状态和实际运行时间。
8. 再次执行时钟检查，保存 `clock-after-fault.json`。
9. 立即执行证据收集，不自动 clean 或恢复 Worker。

`wait_stop` 和 `wait_start` 原样传递给仓外工具。编排器不猜测其单位，只在 `plan` 中显示最终参数，
并在设计的 README 中要求操作者用当前工具 `--help` 核对。

### 5.9 阶段 8：停止、收集和报告

正常无故障运行：

1. 停止 Client。
2. 收集 Client 输出和 SDK 日志。
3. 收集 Worker 日志。
4. 收集 Coordinator 日志。
5. 运行可选报告工具。
6. 默认不 clean，便于复核；显式 cleanup 再清理。

故障运行或中途失败：

1. 不先停止或恢复故障进程。
2. 以 best effort 收集 Client、Worker、Coordinator 和 kubectl 状态。
3. 某一类收集失败不阻止其他类收集。
4. 报告原始失败和各收集子步骤结果。
5. 不自动删除 Pod、日志或远端目录。

## 6. 时钟检查和时间线证据

### 6.1 测量方法

对每个目标 Pod 进行多轮并发采样：

1. 记录本地发送时间 `t0_wall` 和 `t0_mono`。
2. 通过 `kubectl exec` 获取容器 `date +%s%N`。
3. 记录本地接收时间 `t1_wall` 和 `t1_mono`。
4. RTT 为 `t1_mono - t0_mono`。
5. 估计 Pod 偏差为 `pod_wall - midpoint(t0_wall, t1_wall)`。
6. 丢弃 RTT 超过阈值的样本。
7. 每个 Pod 选有效样本中 RTT 最小者作为偏差估计。
8. 计算有效 Pod 偏差的最大值与最小值之差。

超过 `max_skew_ms` 时，`run` 和 `fault` 停止。`clock check` 仅报告非零状态。

这种方式是有界估算，不宣称纳秒级真值。输出必须同时保留 RTT、样本数和无效样本原因。

### 6.2 不自动校时

容器通常使用节点墙上时钟。容器内校时可能要求 `CAP_SYS_TIME` 并影响节点上的其他工作负载。
第一版只定位偏差 Pod 及其所在节点，建议操作者在节点侧检查 chrony/NTP。不会执行 `date -s`，也不会
默认 SSH 到节点修改时间。

### 6.3 事件日志

编排器将自身事件写入 `events.jsonl`，每条至少包含：

- `event`
- `wall_time_ns`
- `monotonic_ns`
- `phase`
- `target_role`
- `target_name`
- `command_id`
- `status`
- `detail`

并生成 `timeline.csv`。这些事件证明编排器何时发出命令、何时收到返回，不直接等同于 Coordinator
内部判定、拓扑提交或 Client 切流时间。后者必须从收集日志中提取并结合时钟偏差评估。

## 7. 结果目录

每次执行 `run` 创建唯一目录：

```text
runs/<UTC时间>-<scenario-name>/
├── manifest.json
├── resolved-config.json
├── commands.jsonl
├── events.jsonl
├── timeline.csv
├── clock-before-run.json
├── clock-before-fault.json
├── clock-after-fault.json
├── preflight/
│   ├── client-old/
│   ├── worker-old/
│   └── coordinator-old/
├── client/
├── worker/
├── coordinator/
├── kubernetes/
└── reports/
```

`manifest.json` 记录：

- 场景名和 run id；
- 开始/结束时间和最终状态；
- wheel 与 kvtest binary 的 SHA-256；
- kubectl context、namespace 和 Pod 清单；
- 每个阶段的状态与耗时；
- 原始失败；
- 收集完整性；
- 报告工具状态。

## 8. 重复运行、幂等性和失败恢复

### 8.1 重复运行

- 每次 `run` 使用新 run id，不覆盖旧结果。
- Pod 默认复用。
- Coordinator、Worker、Client 进程进入运行前都执行“收集旧证据、优雅停止、必要时 kill fallback、clean、start”。
- 安装 wheel 不是幂等假设，而是每次 run 显式安装并验证版本。
- Client deploy JSON 默认重新生成，避免 Pod 名称和 IP 漂移。

### 8.2 失败原则

- 配置、时钟、Pod 数量、版本或进程检查失败：停止后续业务阶段。
- 部分安装成功：失败，不继续。
- 部分 Worker 启动成功：失败，不启动 Client。
- Client 部分启动成功：失败并收集。
- 故障工具异常：保留其输出并立即收集，不自动恢复现场。
- 收集阶段 best effort，不因一个角色失败而跳过其他角色。

### 8.3 不提供跨进程自动 resume

第一版不实现从状态文件自动续跑。失败后可先运行 `collect`，修复环境，再重新执行新的 `run`。
这比在不明确远端真实状态时自动跳过阶段更安全，也保持脚本简单。

## 9. 安全设计

1. 所有子命令用参数数组执行，禁止 `shell=True`。
2. 配置值不拼接成 shell 片段；传给现有脚本的自由命令字段仅允许白名单用途。
3. 删除、无条件 kill 和故障注入需要 `--force`；优雅 stop 失败后的精确目标 fallback 按第 3.4 节约束。
4. 破坏性动作前重新解析精确 Pod 列表，并拒绝空前缀、重叠前缀和超出期望数量的目标。
5. 不允许 `kvchach` 这类未在角色配置中声明的临时宽泛前缀。
6. 日志不记录凭据和环境变量秘密。
7. 示例配置不提交内网 IP、账户和真实绝对路径。
8. 不自动修改 K8s 节点时钟，不自动导入节点镜像。
9. 普通失败不触发自动 Pod 删除。

## 10. 代码结构

保持单文件，但按职责使用小型数据结构和函数分区：

```text
ConfigLoader
  load + deep_merge + schema validation + path resolution

RunContext
  run id + result paths + manifest + event/command recording

CommandRunner
  subprocess execution + timeout + stdout/stderr files + timing

K8sInspector
  kubectl context + Pod inventory + Ready/node/image status

ClockChecker
  multi-sample RTT/skew estimation

KvtestWorkflow
  doctor/prepare/run/fault/collect/cleanup/rebuild orchestration
```

这些类只用于组织代码，不构建插件框架。预计主脚本保持在可一次阅读理解的规模；如果实现明显超过单文件
可维护范围，再在独立评审后拆分模块，不在第一版预先抽象。

## 11. 验证计划

### 11.1 不依赖集群的单元测试

使用 `unittest` 和 mock 覆盖：

1. 两层 TOML 合并和优先级。
2. 未知字段、缺失字段和类型错误。
3. 前缀相同、包含、为空时拒绝。
4. wheel glob 零匹配、多匹配和唯一匹配。
5. 危险动作缺少 `--force` 时拒绝。
6. 复用 Pod、缺失 Pod、Pod 数量不一致和非 Ready 分支。
7. Coordinator 重建路径避免重复安装。
8. 优雅 stop 成功与 kill fallback。
9. Client 旧进程预收集和重新生成 deploy JSON。
10. 时钟 RTT 中点估算、异常样本剔除和超阈值失败。
11. rolling tool SIGINT、超时终止和输出留档。
12. 主阶段失败后 best-effort collect。
13. run 目录唯一且不覆盖旧结果。
14. 所有子进程调用均不使用 shell。

### 11.2 静态与 dry-run 验证

- `python3 -m py_compile`。
- `python3 -m unittest`。
- `plan` 对一份脱敏示例配置输出预期阶段和命令。
- `doctor` 在无 kubectl 或无仓外工具时给出可行动错误，不产生集群写操作。

### 11.3 K8s 手工验收

按风险由低到高：

1. 在现有环境运行 `doctor` 和 `clock check`。
2. `plan` 核对不会出现宽泛前缀。
3. 复用已有 Pod 执行 `prepare`，确认版本和进程。
4. 执行一次无故障 `run`，检查独立结果目录和收集完整性。
5. 在专用测试环境执行 `run --recreate-pods --force`。
6. 在专用测试环境执行 `run --with-fault --force`，核对故障前后时钟证据和原始日志。
7. 连续运行两次，确认不覆盖前一轮并能清理旧进程。

## 12. 后续扩展

非 K8s 支持延后。届时只将下列边界抽象为 transport：

- 节点/实例发现；
- 命令执行；
- 文件复制；
- 进程检查；
- 日志收集。

工作流阶段、两层配置、危险动作授权、run manifest 和时钟证据模型保持不变。第一版不提前实现 SSH 类，
以实际非 K8s 场景需求驱动接口。

## 13. 已确认设计决策

- 默认复用已有 Pod。
- 支持显式重建 Pod。
- 破坏性目标限定为配置中的三个精确前缀。
- 固定环境和易变场景拆成两份 TOML。
- 危险动作只能通过 CLI 显式授权。
- 第一版是单个易读 Python 脚本，复用现有部署脚本。
- 第一版只支持 K8s。
- 容器时钟只检查、不自动修改。
- 中途失败收集证据但不自动销毁现场。
- 中间设计与外围脚本放在 `yuanrong-datasystem-agent-workbench`。
