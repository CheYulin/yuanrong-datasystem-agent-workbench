# Scripts

yuanrong-datasystem-agent-workbench 脚本库。按职责分为以下目录：

## 目录结构

```
scripts/
  lib/                         # 共享 bash 库（source 使用）
  │   ├── load_nodes.sh         # 解析 config/nodes.yaml，提供 node_* 查询函数
  │   ├── remote_defaults.sh    # 远端 SSH/rsync/rsync_excludes 抽象
  │   ├── rsync_excludes.sh    # 统一 rsync 排除参数
  │   ├── build_backend.sh      # CMake / Bazel 构建命令抽象
  │   ├── timing.sh            # run_timed / banner / print_timing_report
  │   ├── cmake_test_env.sh     # 从 CMake test desc 提取 LD_LIBRARY_PATH
  │   └── common.sh             # log_info / stamp_utc / require_var / cmd_exists
  │
  config/
  │   └── nodes.yaml            # 节点统一配置

  build/
  │   ├── build_bazel.sh        # 本地 Bazel 构建入口
  │   ├── build_cmake.sh        # 本地 CMake 构建入口
  │   └── rsync_datasystem_remote_bazel.sh  # rsync DS → 远端（默认 tiantiyun）+ bazel

  harness/
  │   ├── ds_harness.py                     # build/dev/daily/perf 统一入口
  │   ├── profiles.yaml                     # profile → skill/script/evidence 路由
  │   ├── sync_workspace_to_tiantiyun.sh      # 验证前全仓 sync → tiantiyun
  │   ├── run_skill_verification_remote.sh    # TDD + harness profile 验证（tiantiyun）
  │   └── run_skill_html_verify_remote.sh     # wb-html-publish（xqyun）

  development/
  │   ├── sync/
  │   │   ├── publish_htmls_git.sh   # yche.me HTML git（xqyun）
  │   │   ├── sync_to_xqyun.sh       # 同步本地 repos 到远端（Cursor 日间用）
  │   │   └── sync_hermes_workspace.sh # 同步 datasystem 到 hermes 工作区（夜间用）
  │   ├── node/
  │   │   ├── bootstrap_new_node.sh    # 新 CentOS9 节点初始化（< 30 分钟）
  │   │   └── switch_node.sh          # 切换默认远端节点
  │   ├── code-index/
  │   │   └── refresh_urma_index_db.py # URMA/UB macro 索引数据库刷新
  │   └── lib/                        # 已迁至 scripts/lib/（仅保留 redirect README）

  testing/verify/
  │   ├── smoke/                      # 冒烟测试（< 5 分钟）
  │   │   ├── run_smoke.py           # Python 冒烟入口
  │   │   ├── harness_zmq_metrics_e2e.sh  # Bazel+whl+run_smoke（ZMQ 分段 metrics E2E）
  │   │   └── run_smoke_remote.sh
  │   ├── ut/                        # 单元测试（< 30 分钟）
  │   │   └── run_ut_remote.sh
  │   ├── st/                        # 集成测试（< 60 分钟）
  │   │   └── run_st_remote.sh
  │   ├── validate_kv_executor.sh           # KV executor 验证
  │   ├── validate_urma_tcp_observability_logs.sh  # URMA/TCP 日志验证
  │   ├── verify_zmq_metrics_fault.sh       # ZMQ 指标 fault 验证
  │   └── verify_zmq_fault_injection_logs.sh # ZMQ fault injection 日志验证

  analysis/perf/               # 性能分析工具（bpftrace/strace/perf）
  ├── perf_record_kv_lock_io.sh
  ├── trace_kv_lock_io.sh
  ├── collect_client_lock_baseline.sh
  ├── compare_client_lock_baseline.sh
  └── bpftrace/               # BPFTrace 脚本

  lint/
  └── check_cpp_line_width.sh # C++ 行宽检查（Cursor rule 调用）

  metrics/
  ├── gen_kv_perf_report.py   # glog metrics_summary + Perf Log → ASCII / Markdown
  └── README.md                 # 使用说明（含脚本与文档跳转链接）

  archive/                     # 归档文件（不再维护）
  ├── validate_brpc_kv_executor.sh.archived
  └── summarize_observability_log.sh.orphaned
```

## 快速开始

### 1. 配置节点

编辑 `config/nodes.yaml`，添加/修改节点信息。查看当前节点：

```bash
SCRIPT_DIR="$(pwd)/scripts/lib" bash -c \
  'source scripts/lib/load_nodes.sh && echo "默认节点: $(node_default)"'
```

### 2. 切换默认节点

```bash
bash scripts/development/node/switch_node.sh centos9-new
```

### 3. 初始化新节点（< 30 分钟）

```bash
bash scripts/development/node/bootstrap_new_node.sh --node centos9-new
```

### 4. 构建画像

```bash
python3 scripts/harness/ds_harness.py build --backend cmake --dry-run --json
python3 scripts/harness/ds_harness.py build --backend bazel --dry-run --json
python3 scripts/harness/ds_harness.py build --profile build.quick
```

### 5. Skill 验证（按节点）

```bash
# tiantiyun — TDD + harness profile checks
bash scripts/harness/run_skill_verification_remote.sh

# xqyun — HTML 发布
bash scripts/harness/run_skill_html_verify_remote.sh

# 本地 WSL — GitCode / commit 草稿
bash scripts/run_skill_local_verification.sh
```

### 6. 开发 / 每日 / 性能 profiles

```bash
python3 scripts/harness/ds_harness.py dev --profile dev.default --dry-run --json
python3 scripts/harness/ds_harness.py daily --profile daily.full --dry-run --json
python3 scripts/harness/ds_harness.py perf --profile perf.hotspot --dry-run --json
```

### 7. hermes 同步（夜间）

hermes agent 在执行任务前调用此脚本获取最新的 datasystem 代码：

```bash
bash scripts/development/sync/sync_hermes_workspace.sh --node centos9-new
```

### 8. KV 性能报告（glog / metrics_summary）

从 worker 日志生成 ASCII 树或 Markdown（含可选 PerfPoint）：见 [scripts/metrics/README.md](metrics/README.md)（内含 **[gen_kv_perf_report.py](metrics/gen_kv_perf_report.py)** 等跳转链接）。

## 工作空间分离

- **Cursor（白天）**：直接操作 `~/workspace/git-repos`（本地或 SSHFS）
- **hermes（夜间）**：操作 `~/agent/hermes-workspace/yuanrong-datasystem`，通过 `sync_hermes_workspace.sh` 同步
- 两者共享 `~/.cache/yuanrong-datasystem-third-party`（第三方依赖缓存）

## 归档文件

废弃脚本已移至 `archive/`，不再维护。
