---
name: dsbench安装部署运行观测计划
overview: 基于仓库文档形成一份可执行的 dsbench 指导流程，覆盖安装编译产物、部署、运行、观测与日志分析，并给出分阶段验收点。
todos:
  - id: prep-install-path
    content: 确认编译产物安装路径与命令入口验收标准
    status: pending
  - id: deploy-playbook
    content: 整理单机/多机部署步骤与配置文件职责
    status: pending
  - id: run-playbook
    content: 整理 dsbench 三种运行模式与参数基线
    status: pending
  - id: observability-checks
    content: 整理运行后观测项与采样方法
    status: pending
  - id: log-analysis-flow
    content: 整理日志收集与分层故障定位流程
    status: pending
isProject: false
---

# dsbench 指导操作计划

## 目标与范围

- 形成一套从“编译产物安装”到“压测运行与观测”的闭环操作流程。
- 覆盖单机优先、可扩展到多机场景。
- 输出关键命令、配置文件位点、验收标准与故障定位顺序。

## 关键参考文档

- 安装与集群管理：`[/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/dscli.md](/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/dscli.md)`
- 基准工具说明：`[/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/dsbench.md](/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/dsbench.md)`
- 编译安装入口说明：`[/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/installation/installation_linux.md](/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/installation/installation_linux.md)`
- 生产/容器化可观测配置补充：`[/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/k8s_configuration.md](/home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/k8s_configuration.md)`

## 执行计划

### 1) 安装编译产物（优先源码编译 wheel）

- 准备 Python 与构建环境，执行仓库构建脚本产出 wheel（按文档“源码编译安装”）。
- 安装产物后立即验证命令入口：
  - `dscli --help`
  - `dsbench --help`
- 验收标准：两条命令均能返回帮助信息且退出码为 0。
- 常见失败分流：
  - 打包前置依赖缺失（编译工具链/系统命令）；
  - Python 环境隔离问题（建议虚拟环境）；
  - 产物路径不一致（以构建日志打印路径为准）。

### 2) 部署（先单机，再多机）

- 启动 ETCD 并做读写自检（`etcdctl put/get`）。
- 单机快速部署优先：`dscli start -w --worker_address ... --etcd_address ...`。
- 需要可复用配置时，生成并维护：
  - `worker_config.json`
  - `cluster_config.json`
  参考 `[dscli.md]( /home/t14s/workspace/git-repos/yuanrong-datasystem/docs/source_zh_cn/deployment/dscli.md )` 中 `generate_config/start/up/down/stop` 章节。
- 多机部署使用 `dscli up -f ./cluster_config.json`，前提为 SSH 免密和各节点可执行 `dscli`。
- 验收标准：worker 启动日志出现成功信息，端口可达，集群节点与配置一致。

### 3) 运行（dsbench）

- 先运行“非压测检查”：
  - `dsbench show`
  - `dsbench kv -h`
- 再执行三类压测模式：
  - SINGLE：显式参数运行单用例；
  - FULL：`--all`（注意共享内存要求）；
  - CUSTOMIZED：`-f testcase.json`。
- 核心参数基线：`-S/-G`（必填）、`-n/-s/-c/-t/-b`、`--concurrent`。
- 验收标准：压测用例完整执行，输出包含阶段结果（set/get/del 或并发模式阶段）。

### 4) 运行后观测

- 客户端侧：使用 `dsbench` 全局参数控制日志输出：
  - `--min_log_level`
  - `--log_monitor_enable`
- 服务端侧：在 `worker_config.json` 重点检查可观测相关项：
  - `log_dir`、`log_monitor`、`log_monitor_interval_ms`、`minloglevel`。
- 节点健康快照：使用 `dsbench show` 采集版本/内存/CPU/THP/HugePages 信息，形成压测前后对比。
- 验收标准：能定位到压测窗口内的客户端日志与 worker 日志，并可观察资源变化趋势。

### 5) 日志分析与问题定位流程

- 收集：执行 `dscli collect_log --cluster_config_path ./cluster_config.json` 统一归档。
- 分层排查顺序：
  1. 安装与入口问题（命令不可用/版本不一致）；
  2. 部署问题（ETCD 不可达、worker 未启动、端口冲突）；
  3. 压测参数问题（`-S/-G` 地址错误、模式不匹配、数据量过大）；
  4. 资源瓶颈（共享内存/CPU/HugePages/THP 配置）；
  5. 远程执行问题（SSH 互信、节点用户/密钥限制）。
- 输出模板：每次压测记录“命令+配置快照+日志包路径+关键指标+结论与改动项”。

## 里程碑与交付

- M1：安装可用（`dscli/dsbench --help`）
- M2：部署可用（单机 worker 启动成功）
- M3：运行可用（至少 1 组 SINGLE 压测完成）
- M4：观测可用（show 快照 + worker/client 日志可回溯）
- M5：分析可用（collect_log 归档 + 标准化复盘模板）

```mermaid
flowchart LR
    installPhase[InstallArtifacts] --> deployPhase[DeployWorkerAndEtcd]
    deployPhase --> runPhase[RunDsbenchCases]
    runPhase --> observePhase[ObserveClientAndWorker]
    observePhase --> collectPhase[CollectLogs]
    collectPhase --> analyzePhase[AnalyzeAndIterate]
```



