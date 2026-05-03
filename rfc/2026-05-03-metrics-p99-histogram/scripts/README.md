# P99 metrics 远端脚本说明

在 **`yuanrong-datasystem-agent-workbench`** 仓库里本机执行；编译与测试在远端 SSH 主机上跑（默认 **`root@xqyun-32c32g`**）。使用前确认本机已配置好到远端的 **SSH** 与 **rsync**。

---

## 脚本一览

| 脚本 | 作用 |
|------|------|
| `rsync_datasystem.sh` | 将本机 `yuanrong-datasystem` 同步到远端（排除列表见 workbench `scripts/build/remote_build_run_datasystem.rsyncignore`） |
| `bazel_build.sh` | 远端进入 datasystem 目录：`DS_OPENSOURCE_DIR` + `bazel build`（metrics UT 包 + ST `histogram_p99_perf_test`） |
| `bazel_run_tests.sh` | 远端 `bazel test`：UT metrics 包 + ST **`histogram_p99_perf_test`**（**4×`TEST_F`**；**不依赖 `st_common`**）；日志 tee |
| `run_full_tests_remote.sh` | 按顺序执行上述三步（rsync → build → test） |

---

## 推荐用法

在本机进入本 RFC 目录：

```bash
cd yuanrong-datasystem-agent-workbench/rfc/2026-05-03-metrics-p99-histogram
```

**一键全流程**（可选第一个参数覆盖 `REMOTE`，与其它脚本里 `REMOTE` 环境变量二选一）：

```bash
bash scripts/run_full_tests_remote.sh
bash scripts/run_full_tests_remote.sh root@另一台主机
```

**分步**（例如只改代码后想跳过 rsync 之前的某步）：

```bash
bash scripts/rsync_datasystem.sh
bash scripts/bazel_build.sh
bash scripts/bazel_run_tests.sh
```

---

## 环境变量（按需覆盖）

| 变量 | 默认 | 含义 |
|------|------|------|
| `REMOTE` | `root@xqyun-32c32g` | SSH 目标（`user@host`） |
| `LOCAL_DS` | `/home/t14s/workspace/git-repos/yuanrong-datasystem` | 本机 datasystem 根目录 |
| `REMOTE_DS` | `/root/workspace/git-repos/yuanrong-datasystem` | 远端 datasystem 根目录 |
| `REMOTE_WB` | `/root/workspace/git-repos/yuanrong-datasystem-agent-workbench` | 远端 workbench 根（用于写测试结果日志） |
| `LOCAL_RESULTS` | `<本 RFC 目录>/results` | **`bazel_run_tests.sh` 结束**时用 `rsync` 拉回 `bazel_test_*_${STAMP}.log` 的本地目录（相对于脚本为 `../results`） |
| `DS_OPENSOURCE_DIR_REMOTE` | `/root/.cache/yuanrong-datasystem-third-party` | 远端三方依赖缓存（与 CMake 规范一致，勿放在每次清掉的 build 目录里） |
| `BAZEL_JOBS` | `32` | 远端 `bazel` 并行度 |
| `BAZEL_TEST_OUTPUT` | `all` | 传给 `bazel test --test_output=…`；**`all`** 时通过的测试也会打出 stdout（ST perf 里的 `std::cout` / gtest 用例名）；改为 **`errors`** 则仅失败时有详细输出，日志更短 |
| `BAZEL_EXTRA_OPTS` | 空 | 追加到 `bazel build` / `bazel test` 的额外参数 |

`bazel_build.sh` 与 `bazel_run_tests.sh` 会带上 **`--define=enable_urma=false`**，避免依赖 URMA SDK；一般无需再改。

示例：

```bash
LOCAL_DS=/path/to/yuanrong-datasystem \
REMOTE=me@my-build-host \
BAZEL_JOBS=16 \
bash scripts/run_full_tests_remote.sh
```

---

## 观察终端输出

### `rsync_datasystem.sh`

- 打印 `LOCAL_DS`、`REMOTE`、`REMOTE_DS`，结束时 **`rsync done.`**
- 若报错 **rsyncignore not found**：确认本机 workbench 路径下存在 `scripts/build/remote_build_run_datasystem.rsyncignore`（脚本通过相对路径定位 RFC 上级 workbench）。

### `bazel_build.sh`

- 打印 `REMOTE`、`REMOTE_DS`、`DS_OPENSOURCE_DIR_REMOTE`、`BAZEL_JOBS`、`BAZEL_NO_URMA`（应为 `--define=enable_urma=false`）。
- 远端会执行 `bazel info release` 等，最后 **`build done.`**
- 成功时远端日志里应有 **`Build completed successfully`**（由 Bazel 打印）。

### `bazel_run_tests.sh`

- 打印 `REMOTE`、`REMOTE_DS`、`STAMP`、`BAZEL_TEST_OUTPUT`（默认 **`all`**），以及一行说明 URMA 已关。
- 远端开始会先打印 **`=== host ===`**（`hostname`、`date`）、**`=== datasystem git ===`**（`git rev-parse`、`git status -sb`），便于确认跑的是哪次提交。
- 接着先 **`=== bazel test metrics UT (package) ===`**（**`--keep_going`**，仍是 4 个 UT 目标），再 **`=== bazel test ST //tests/st/common/metrics:histogram_p99_perf_test ===`**（**不带 `--keep_going`**）。若曾与 UT 合并在一次命令里且加 **`--keep_going`**，ST 在**分析失败**时可能被 Bazel **静默跳过**，终端只剩 4 个 UT。拆成两段后，ST 缺失或 BUILD 错误会单独报错。
- 最后 **`=== rsync remote bazel log(s) → … ===`**：把远端 **`bazel_test_*_<STAMP>.log`**（先试 workbench **`${REMOTE_WB}/rfc/…/results/`**，再试 **`/tmp/`** 兜底）同步到本 RFC 下 **`results/`**（可用 **`LOCAL_RESULTS`** 覆盖）。

### `run_full_tests_remote.sh`

- 依次打印 **`>>> [1/3] rsync`**、**`[2/3] build`**、**`[3/3] test`**，最后 **`run_full_tests_remote.sh finished`**。

---

## 测试日志文件（重点）

`bazel_run_tests.sh` 会把 **`bazel test` 的完整标准输出** 用 **`tee`** 写入远端文件：

1. 若远端存在目录 **`${REMOTE_WB}/rfc/2026-05-03-metrics-p99-histogram/results`**  
   日志路径形如：  
   **`${REMOTE_WB}/rfc/2026-05-03-metrics-p99-histogram/results/bazel_test_<git短哈希>_<STAMP>.log`**

2. 若远端 **没有** 克隆 workbench，则日志在 **`/tmp/yuanrong-datasystem_metrics_bazel_<git短哈希>_<STAMP>.log`**，脚本会打印 **WARN**。

**脚本结束时会自动 `rsync`**：先按文件名过滤从 workbench **`results/`** 同步（避免远端 **`zsh` 在通配符无匹配时 NOMATCH 导致 rsync 断连**）；若本地仍未出现 **`bazel_test_*_<STAMP>.log`**，再用 **`ssh … sh -c ls`** 尝试 **`/tmp/`** 兜底并逐个拉回。默认目录 **`LOCAL_RESULTS=<RFC根>/results`**（**`LOCAL_RESULTS`** 可覆盖）。若仍未拉回会 **WARN**，也可手动 **scp**（`STAMP` 与启动时打印的一致）。

手动拉取示例（按需替换 `REMOTE`、`STAMP`）：

```bash
scp 'root@xqyun-32c32g:~/workspace/git-repos/yuanrong-datasystem-agent-workbench/rfc/2026-05-03-metrics-p99-histogram/results/bazel_test_*_20260101T120000Z.log' ./results/
```

（将 `20260101T120000Z` 换成你这次运行终端里看到的 `STAMP`。）

---

## ST perf 与脚本关系

`histogram_p99_perf_test` 为 **`ds_cc_test`**，fixture 继承 **`testing::Test`**（日志目录初始化对齐 **`CommonTest`**，不拉 **`//tests/st:st_common`**）；与 `bazel_run_tests.sh` 一并执行。仍可在远端直接跑可执行文件查看实时输出：

```bash
cd /root/workspace/git-repos/yuanrong-datasystem
./bazel-bin/tests/st/common/metrics/histogram_p99_perf_test
```

关注：**gtest** 的 **`[ RUN ]` / `[ OK ]`**，以及 **`[Fixed 100us]`**、**`[Random 4 buckets]`**、**`[99x10+1x10000]`**、**`[Race]`** 等吞吐/正确性输出。失败时进程返回 **非 0**，`bazel test` 会判为失败。

---

## 常见问题

- **只想跑 UT、不跑 ST perf**：在远端执行  
  `bazel test '//tests/ut/common/metrics/...' --define=enable_urma=false ...`（不要加 `histogram_p99_perf_test`）。
- **只重跑 test、已同步 datasystem**：本机 **`bash scripts/bazel_run_tests.sh`** 即可；结束前会把本次日志 **rsync** 到 **`results/`**。
- **ST 日志里没有四个用例 / Bazel 报 `0 test targets` / `No test targets were found`**：当前 tree 里该目标 **不是 `cc_test`**（常见于 **`tests/st/common/metrics/BUILD.bazel`** 仍用 **`cc_binary`** 或未 **rsync**）。在远端执行 **`bazel query 'kind(cc_test, //tests/st/common/metrics:all)'`**，应含 **`histogram_p99_perf_test`**。脚本在跑 ST 前也会用 **`bazel query`** 校验；通过后 **`--test_output=all`** 的日志里会有 gtest 的 **`[ RUN ]` / `[ OK ]`** 以及 **`[Fixed 100us]`** 等行。
- **链接或分析失败**：先看远端 `git rev-parse` 是否与预期一致；再打开 **`results/` 下对应 `bazel_test_*.log`** 全文检索 **ERROR**。
