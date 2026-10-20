# KVC 公平对比工具

DataSystem、Mooncake Store、LMCache 的原生 CPU/TCP KV 实验工具。当前比较固定每次调用 1、16、256、4096 个独立 keys，value 为 1024 bytes，8 个客户端并发，服务端两端点。未支持的原生能力明确记录 N/A；原生错误保留失败，不进入成功性能聚合。

## 1. 模块入口

| 模块 | 职责 |
| --- | --- |
| `kvcbench/build/` | 冻结版本、源码配方、真实构建、构建目录核验、代码包 |
| `kvcbench/run/` | 部署、隔离/恢复、产品生命周期、资格验证、epoch 和 SDK worker |
| `kvcbench/observe/` | 硬件基线、suite 级 CPU/内存/磁盘及干扰采集、ready/final |
| `kvcbench/collect/` | 原件冻结、归约、独立审计与结果索引 |
| `kvcbench/report/` | 消费已绑定 analysis/audit、ECharts、文档网页、离线包 |
| `kvcbench/delivery/` | 站点上传、注册和交付相关操作 |
| `kvcbench/core/`、`platform/` | 纯规则/统计与文件、锁、进程、cgroup、步骤回执 |
| `kvcbench/pipeline/` | 明确编排 owner 模块；步骤验收通过后才允许下游消费 |

根目录旧 `.py` 和产品目录入口用于兼容，实际新实现位于上述模块。旧实验及命令说明见 [历史流程](docs/legacy-workflows.md)，不把历史成功当成新实验成绩。

## 2. 当前工作流

构建入口：`python3 -m kvcbench build --spec <build-spec.json>`。版本与配方在 `configs/versions/kvc_20261020.json`，构建主机示例在 `configs/hosts/build.example.json`。

运行配置示例：`configs/experiments/batch-modular.example.json`。先制作完整代码包，再从该代码包的 `payload/` 执行 start；start 创建新的运行目录和独立 control 目录。后续步骤在该运行目录使用冻结的 package 执行。

```bash
python3 -m kvcbench bundle --out /absolute/frozen-bundle
cd /absolute/frozen-bundle/payload
python3 -m kvcbench workflow start --spec /absolute/run-spec.json --control /absolute/new-control
cd /absolute/new-runtime-root
python3 -m kvcbench workflow gate --control /absolute/new-control
python3 -m kvcbench workflow smoke --control /absolute/new-control
python3 -m kvcbench workflow measure --control /absolute/new-control
```

每一步记录输入、结果、产物清单、验收、摘要和 commit。`measure` 成功结束或失败后都执行环境恢复；准备部分失败也保留 intent 并恢复。现场恢复入口为 `workflow restore --control <control>`，不能用它自动重放请求。步骤或输出已存在时不续写；重新测量使用新 run 和状态目录。

W01–W11 的运行编排已接入并正在进行真实节点验证。W12 收集与 W13/W14 离线派生入口已接入，尚待这次正式矩阵结束后用新原件验收；W15–W18 交付入口已接入，仍待新原件的端到端实际验收。独立报告入口已实现；上游必须提供真实绑定产物，不能以空文件或历史结果代替新矩阵。

测量、观测停止和环境恢复均结束后，在目标节点的新冻结工具包目录执行收集；原运行目录与失败记录保持原样。每次操作使用新输出目录：

```bash
python3 -m kvcbench collect capture --control /absolute/control --out /absolute/snapshot --invocation /absolute/outer-command
python3 -m kvcbench collect receive --host tiantiyun-80c128g --snapshot /absolute/snapshot --receipt /absolute/snapshot-receipt --out /absolute/received
python3 -m kvcbench collect derive --received /absolute/received --source-bundle /absolute/current-bundle/bundle.zip --out /absolute/derived
python3 -m kvcbench report --inputs /absolute/derived/report-inputs.json --out /absolute/report
```

capture 在构建锁和共同运行锁内复制终态原件、构建物、原始回执及执行源码。receive 保留原始路径，通过显式来源映射重新核验，不改写原件。derive 从原始 SDK 窗口重算矩阵和资源，并独立检查构建、TCP、部署、恢复及采样覆盖；生成四份当前 Markdown 文档及绑定输入。原生失败仍显示失败，缺采样不填零。关闭 RocksDB 后的空目录及其权限也属于冻结证据。

## 3. 检查与文档

详细命令与边界见 [commands.md](docs/commands.md)。使用 `status --workflow <control>/workflow` 查看逐步结果和耗时，用 `verify-step` 只读核对已封存产物；验收不要求每一步人工确认。

```bash
python3 -m kvcbench status --workflow /absolute/new-control/workflow
python3 -m unittest discover -s tests -t . -p 'test_*.py'
```

默认回归不启动真实产品、SSH 或修改主机配置；现场构建、资格与压测必须显式执行。测试通过与产品性能通过分别记录。

普通测试按职责放在 tests/unit/，契约测试在 tests/contract/；独立进程安全程序为 `tests/integration/datasystem_runtime_safety.py --out <new-dir>`，不纳入自动导入。诊断实现归 kvcbench/diagnostics/，历史流程实现归 kvcbench/compat/legacy/；根旧命令仅作迁移兼容，当前入口为 python3 -m kvcbench。
