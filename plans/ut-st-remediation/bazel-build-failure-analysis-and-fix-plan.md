# Bazel 构建失败：原因归类与修复计划

本文档基于在 **远端 `xqyun-32c32g`** 与本机上的多次尝试，把失败拆成 **「仓库 Bazel 配置 / 依赖」** 与 **「运行环境」** 两类，并给出分阶段修复顺序。

---

## 1. 观测到的失败现象（按出现阶段）

| 阶段 | 典型报错摘要 | 常见触发条件 |
|------|----------------|--------------|
| **A. 主仓库映射 / 外部依赖加载** | `com_google_protobuf/.../bazel/private/native.bzl: name 'ProtoInfo' is not defined` | `bazel build` 在解析 `WORKSPACE` 拉起的 `com_google_protobuf`、`rules_java` 等时尚未编译业务代码 |
| **B. Starlark 加载 `bazel/build_defs.bzl`** | `Unable to find package for @rules_cc//cc:defs.bzl`（或同类） | `load("@rules_cc//...")` 时 **`@rules_cc` 未注入**（WORKSPACE 未完整生效 / Bzlmod 与 WORKSPACE 组合问题） |
| **C. 展开 `native.cc_test` / `native.cc_library`** | `native.cc_test` / `cc_test` **已从 Bazel 核心删除** | **Bazel 9+** 且仍使用 `native.cc_*` 而未从 `rules_cc` 引入 |
| **D. 运行时资源** | 进程被 **OOM killer** 杀掉（如 `cc1plus`） | **并行编译**、**无 swap**、内存紧张（属环境容量，不是 Starlark 报错） |
| **E. 磁盘** | `No space left on device` | 根分区或 `/tmp` 写满（当前观测为 **空间偏紧** 但未在 Bazel 解析阶段表现为该错误） |

**当前阻塞「能跑通 `bazel build //tests/...`」的主因**：以 **A** 为主（**protobuf / rules_java / Bazel** 的 **Starlark API 与加载顺序** 不匹配）；**C** 会在升级到 **Bazel 9** 且无 `rules_cc` 改造时单独爆。**D/E** 不解释 `ProtoInfo` 类错误。

---

## 2. 归因：配置问题 vs 环境问题

### 2.1 主要属于 **仓库 Bazel 配置与依赖矩阵**（应通过改仓库/依赖修复）

1. **`WORKSPACE` 固定的 `com_google_protobuf`（当前为 protobuf **28.3**）**  
   - 其自带的 `bazel/private/native.bzl`、`bazel/common/proto_info.bzl` 与 **当前拉取的 `rules_java` / `compatibility_proxy`** 在 **Bazel 8.4.x** 下加载时，会走到 **`ProtoInfo` 未定义** 的路径。  
   - 这是典型的 **protobuf 版本 × rules_java × Bazel 大版本** 组合未在官方矩阵里验证的问题，**不是**「远端机器坏了」。

2. **`bazel/build_defs.bzl` 仍使用 `native.cc_library` / `native.cc_test`**  
   - 在 **Bazel 9** 上 **C++ 规则已从 core 移除**，必须 `load("@rules_cc//cc:defs.bzl", ...)`。  
   - 属于 **仓库 Starlark 与 Bazel 大版本策略** 未对齐（**配置/代码** 问题）。

3. **`.bazelrc`：`common --enable_bzlmod=false` + `common --enable_workspace=true`**  
   - 对 **Bazel 8 + 纯 WORKSPACE** 是合理组合；但若未来只开 Bzlmod 或只开 Workspace 之一，需与 **MODULE.bazel** 迁移配套，否则会出现 **B 类** 错误。属 **配置策略**，不是单机特例。

4. **第三方 patch**（如 `protobuf_remove_deps_28_3.patch`）  
   - 减轻了 protobuf 根 `BUILD.bazel` 对部分语言的依赖，但 **未消除** `bazel/private/native.bzl` 与工具链的 **API 演进** 冲突。后续要么 **升级 protobuf**，要么 **追加 patch** 修正 `ProtoInfo` 引用。

### 2.2 主要属于 **运行环境**（应通过机器/资源/命令习惯缓解）

1. **Bazel 可执行文件版本**：本机/CI 用 **系统 `bazel` 9.x**，仓库 **`.bazelversion` 钉 8.4.2** → 需 **bazelisk** 统一，否则「同一仓库两种错误」（A+C 交替出现）。  
2. **内存**：并行编译 C++ 时 **OOM kill `cc1plus`** → 降 `-j`、加 swap、或限制并发。  
3. **磁盘**：根分区 **使用率 >90%** 时，大 `bazel-*` 输出与缓存易触发写失败；需监控 **`df`**。

**结论**：  
- **`ProtoInfo` / 主仓库映射失败** → **优先判定为（1）依赖与 Bazel 配置矩阵**，不是「换台机器就能好」的纯环境问题。  
- **OOM / 磁盘满** → **环境容量**，与 A 类错误需分开排查。

---

## 3. 修复计划（建议顺序）

### 阶段 0：止血与统一入口（1～2 天）

| 动作 | 目的 |
|------|------|
| CI/文档明确 **默认构建路径为 CMake**（与当前可跑通路径一致） | 避免团队误以为 Bazel 已绿 |
| 开发机安装 **bazelisk**，以 **`.bazelversion`** 为准执行 Bazel | 消除「本机 Bazel 9 / 仓库期望 8」的错位 |
| 远端/CI 执行 Bazel 前 **`df -h`、预留 ≥10GiB 可用** | 排除 E 类干扰 |

### 阶段 1：解除 `ProtoInfo` / protobuf 加载失败（核心，优先级最高）

| 选项 | 做法 | 风险 |
|------|------|------|
| **1a. 升级 protobuf 与配套 patch** | 在 `ds_deps.bzl` 的 `setup_protobuf()` 中换用 **与 Bazel 8.4.x + grpc 1.68.x 官方或社区验证过** 的组合；必要时更新 `protobuf_remove_deps_*` patch | 需全量编译与少量 API 差异排查 |
| **1b. 锁定 `rules_java` / `rules_proto`** | 在 `WORKSPACE` 中 **显式 `http_archive`  pin** 与 protobuf 28.3 文档一致的版本，避免传递依赖拉到过新 `rules_java` | 需对照 grpc/protobuf 发行说明 |
| **1c. 追加 patch** | 对 `com_google_protobuf` 的 `bazel/private/native.bzl`（或 `proto_info.bzl`）按 **Bazel 8** 的 `ProtoInfo` 提供方式打补丁 | 维护成本高，适合短期 unblock |

**验收**：在干净 `bazel clean --expunge` 后，  
`bazel build //tests/ut/common/rpc:zmq_metrics_test //tests/st/common/rpc/zmq:zmq_metrics_fault_test` **无 Starlark 加载错误**。

### 阶段 2：Bazel 9 就绪 — `rules_cc` 迁移（与阶段 1 可并行）

| 动作 | 说明 |
|------|------|
| `bazel/build_defs.bzl` 顶部 `load("@rules_cc//cc:defs.bzl", "cc_library", "cc_test")`，`native.cc_*` 改为 `cc_library` / `cc_test` | **必须**保证 `WORKSPACE` 中 **`rules_cc` 在任何加载 `build_defs.bzl` 的包之前已 `http_archive`**（当前 `setup_rules()` 已有，需验证顺序与 Bazel 9 下可见性） |
| 在 **Bazel 9** 上跑最小 `bazel build //tests/ut/common/rpc:zmq_metrics_test` | 验证 C |

### 阶段 3：Bzlmod 迁移（中长期）

| 动作 | 说明 |
|------|------|
| 按 [Bazel external migration](https://bazel.build/external/migration) 将 `com_google_protobuf`、`grpc` 等迁入 **`MODULE.bazel` + lockfile** | 减少 WORKSPACE 手搓矩阵；需单独里程碑与回归 |

### 阶段 4：回归与门禁

| 动作 | 说明 |
|------|------|
| 将 **`bazel build` + 关键 `bazel test`/`bazel run`** 接入 CI（可先仅 ZMQ metrics 相关 target） | 防止回退 |
| 保留 **CMake** 门禁直至 Bazel 全绿 | 双轨降低风险 |

---

## 4. 一句话结论

| 问题 | 归类 |
|------|------|
| **`ProtoInfo` is not defined**（当前阻塞 Bazel 的主因） | **以仓库依赖与 Bazel 配置为主**（protobuf / rules_java / 加载顺序），**非**单机「环境坏了」独有现象。 |
| **OOM / 磁盘紧** | **环境资源**，与 A 类应分开处理。 |
| **`native.cc_*` 在 Bazel 9** | **仓库 Starlark 未跟进 Bazel 大版本**，属配置/代码层面。 |

---

## 5. 远端已验证结论与运行要点（`xqyun-32c32g`）

在仓库路径 **`/root/workspace/git-repos/yuanrong-datasystem`** 上，用 **bazelisk** 固定 **Bazel 7.6.2**（与仓库 **`.bazelversion`** 一致）后，下列目标 **分析、编译、`bazel test` 均通过**（2026-04-17）：

| 目标 | 说明 |
|------|------|
| `//tests/ut/common/rpc:zmq_metrics_test` | 可选：`--test_arg=--gtest_filter=ZmqMetricsTest.*` |
| `//tests/st/common/rpc/zmq:zmq_metrics_fault_test` | ST 场景 |

**配置关键点（后续本机对齐时照此做即可）：**

1. **Bazel 大版本**：**不要用 8.4.2 直接打 protobuf 28.3 的 WORKSPACE 组合**（会触发上文 **A 类** `ProtoInfo`）。当前工程侧 **止血方案** 为钉 **7.6.2**（见根目录 **`.bazelversion`**）；中长期仍可按 §3 阶段 1 升级 protobuf / 规则集以恢复较新 Bazel。
2. **入口统一**：安装 **bazelisk**，让 `bazel` 解析 `.bazelversion`；若环境变量覆盖，使用 `USE_BAZEL_VERSION=7.6.2` 显式对齐。
3. **验证命令（远端/本机通用）**：
   - `bazelisk build //tests/ut/common/rpc:zmq_metrics_test //tests/st/common/rpc/zmq:zmq_metrics_fault_test`
   - `bazelisk test //tests/ut/common/rpc:zmq_metrics_test --test_arg=--gtest_filter=ZmqMetricsTest.* --test_output=errors`
   - `bazelisk test //tests/st/common/rpc/zmq:zmq_metrics_fault_test --test_output=errors`
4. **Shell**：默认 **zsh** 会展开 `ZmqMetricsTest.*`，请用 **`bash -lc '...'`** 或对 `--gtest_filter='...'` **加引号**。
5. **`bazel test` vs `bazel run`**：回归与 CI 请优先 **`bazel test`**。若 **`bazel run`** 出现 **`datasystem_mallctl` 等动态链接符号** 报错，多为 **与系统/其他路径下的 `libcommon_shared_memory.so` 混用** 或 **未在 Bazel 管理的链接闭包内**；在同一仓库内用 **`bazel test`** 可避免该类路径污染。

---

## 6. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-17 | 初稿：基于远端 Bazel 8.4.2 + WORKSPACE 实测与仓库依赖阅读整理 |
| 2026-04-17 | 补充：远端 Bazel **7.6.2** + bazelisk 下 ZMQ metrics UT/ST **`bazel test` 通过**；仓库 **`.bazelversion` 改为 7.6.2** |
