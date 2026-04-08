# openYuanrong datasystem 官方参考（vibe 侧高可信度入口）

本页汇总 **openEuler 站点上 openYuanrong datasystem 文档**中与 **开发、验证、部署、日志、接口示意** 直接相关的条目，作为本仓库（`vibe-coding-files`）与同级 **`yuanrong-datasystem`** 源码对照时的**外部权威入口**。全文以官方为准；此处不复制大段 API 手册，只保留**链接 + 与本仓库工作流相关的要点**。

## 主入口

| 资源 | URL |
|------|-----|
| **入门（安装、部署进程/K8s、开发指南入口）** | [入门 — openYuanrong datasystem documentation](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/getting-started/getting_started.html) |
| **附录 · 日志**（与 `resource.log`、排障对照） | [openYuanrong datasystem 日志](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/appendix/log_guide.html) |
| **Bazel 集成**（若上游用 Bazel） | 官方目录 [附录](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/appendix/) 中「openYuanrong datasystem + Bazel 集成指南」 |

---

## 安装与验证（与发布物 / PyPI 对齐）

摘自[入门 · 安装](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/getting-started/getting_started.html)的**可核对要点**（版本以官方页面为准）：

- **Python**：3.9 / 3.10 / 3.11（文档所列）。
- **OS / 架构**：Linux（文档建议 glibc 2.34+）、x86-64。
- **PyPI 安装完整发行版**（含 Python SDK、C++ SDK、命令行工具）：
  - `pip install openyuanrong-datasystem`
- **安装后验证**（官方示例）：
  - `python -c "import yr.datasystem; print('openYuanrong datasystem installed successfully')"`
  - `dscli --version`

> 本仓库日常 **CMake 构建与 CTest** 仍以同级 **`yuanrong-datasystem`** 为准时，见 [`verification/cmake-non-bazel.md`](../verification/cmake-non-bazel.md)；与 pip 发行版是两条线，勿混用路径。

---

## 部署（进程与 Kubernetes）

### 进程部署（etcd + dscli）

官方步骤摘要：

1. **先起 etcd**（示例单节点监听 `2379`）：
   - `etcd --listen-client-urls http://0.0.0.0:2379 --advertise-client-urls http://localhost:2379 &`
2. **一键起 Worker**（示例）：
   - `dscli start -w --worker_address "127.0.0.1:31501" --etcd_address "127.0.0.1:2379"`
3. **停止**：
   - `dscli stop --worker_address "127.0.0.1:31501"`

更细的进程部署参数见官方「openYuanrong datasystem 进程部署」子页（从入门左侧导航进入 **部署**）。

### Kubernetes

官方摘要：

- `dscli generate_helm_chart -o ./` 获取 chart；
- 编辑 `./datasystem/values.yaml`（镜像、`etcdAddress` 等）；
- `helm install openyuanrong_datasystem ./datasystem` / `helm uninstall openyuanrong_datasystem`。

与 **可观测性 / K8s 配置项** 相关的详细说明见官方 **部署 → openYuanrong datasystem Kubernetes 配置项**（同站导航）。

---

## 开发指南与接口层级（示意）

入门页在 **开发指南** 下给出三类主接口的**定位**（详细 API 在同站 **编程接口** 树中）：

| 能力 | 官方定位（一句话） |
|------|---------------------|
| **异构对象** | HBM 抽象、D2D/H2D/D2H、训推与 KVCache 等场景。 |
| **KV** | 共享内存免拷贝 KV、DRAM/SSD/二级缓存置换、Checkpoint 等。 |
| **Object** | 共享内存 Object 语义、引用计数、Buffer 映射。 |

Python 侧入口类为文档中的 **`DsClient`**（`init` 后 `kv()` / `object()` / `hetero()` 等）；C++/Java API 树见同站 **编程接口 → API**。

> **官方案例代码**（入门内嵌的短示例）已摘录索引到 [`../feature-tree/openyuanrong-official-case-examples-index.md`](../feature-tree/openyuanrong-official-case-examples-index.md)，便于与「接口用途」后续做特性树映射，**不替代**官方完整示例与 API 页。

---

## 日志与排障（与本仓库运维文档的关系）

- 官方 **附录 · 日志**：说明运行日志、资源日志等**分类与字段语义**（与 [`operations/kv-client-worker-resource-log-triage.md`](operations/kv-client-worker-resource-log-triage.md) 中「以源码 `res_metrics.def` 顺序为准」**配合阅读**）。
- 客户端 access 日志、错误码分层仍以本仓库 **`plans/kv_client_triage/`** 下 Playbook 与 **`docs/reliability/operations/`** 为准（工程化排障路径）。

---

## 修订

- 链接与要点随官方站点更新；若导航变更，以 [入门首页](https://pages.openeuler.openatom.cn/openyuanrong-datasystem/docs/zh-cn/latest/getting-started/getting_started.html) 侧栏为准。
