# 远端跑 smoke / metrics 采集：前置与顺序

默认远端：`xqyun-32c32g`，路径：`~/workspace/git-repos/`（与现有 harness / nightly 一致）。

## 0. 同步代码到远端

- **只想更新 agent-workbench（脚本、RFC、不入库日志/二进制）**：

  ```bash
  # 在本地 workbench 仓任意位置
  bash scripts/testing/verify/smoke/rsync_agent_workbench_to_remote.sh
  ```

  可选：`REMOTE=other-host`、`--dry-run`、`--delete`（严格镜像，会删远端多余文件）。

- **还要编 whl / 跑 Bazel**：请同步 **`yuanrong-datasystem`**（勿单独依赖 workbench rsync），例如：

  ```bash
  bash scripts/build/rsync_datasystem_remote_bazel.sh --inspect-only   # 先看路径
  bash scripts/build/rsync_datasystem_remote_bazel.sh -- build //...     # 示例
  ```

  或与 `scripts/development/sync/sync_to_xqyun.sh` 一次性同步两个仓（同样带排除列表）。

## 1. 远端构建 whl 并确认成功

在远端（示例）：

```bash
ssh xqyun-32c32g 'set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
mkdir -p "${DS_OPENSOURCE_DIR}"
cd "${HOME}/workspace/git-repos/yuanrong-datasystem"
bazel build --jobs="$(nproc)" //bazel:datasystem_wheel
ls -la bazel-bin/bazel/openyuanrong_datasystem-*.whl
find bazel-bin -name "openyuanrong_datasystem-*.whl" | head -1'
```

**Wheel** 由目标 **`//bazel:datasystem_wheel`** 生成；产物在 **`bazel-bin/bazel/`**（文件名含 **`cp39`** / **`manylinux_2_xx`** 等与 Python、glibc 相关后缀）。仅 **`bazel build //...` 里若从未编到该包**、或 **未先 `cd` 到 `yuanrong-datasystem` 根**、或 **`find` 加了过小的 `-maxdepth`**，都会出现「找不到 whl」。

更稳的取路径方式（不依赖 `find` 深度）：

```bash
cd "${HOME}/workspace/git-repos/yuanrong-datasystem"
bazel cquery --output=files '//bazel:datasystem_wheel' | head -1
```

以 **`bazel build //bazel:datasystem_wheel` 退出码 0** 且 **`ls` / `cquery` 能看到 `.whl`** 为准。

### 1.1 端到端核对（推荐）：固定打印「到底编到哪」+ `pip` + `import`

同级目录下在 **远端**执行（把 whl 的 **相对路径、绝对路径、`bazel-bin` 是否同一文件** 都打出来，避免 `find` 深度搞错）：

```bash
scp /path/to/yuanrong-datasystem-agent-workbench/scripts/testing/verify/smoke/e2e_verify_whl_path.sh \
  xqyun-32c32g:/tmp/e2e_verify_whl_path.sh   # 或先 rsync 整个 workbench

ssh xqyun-32c32g 'set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
export BAZEL_WHL_EXTRA_OPTS="--jobs=$(nproc) --define=enable_urma=false"
bash /tmp/e2e_verify_whl_path.sh "${HOME}/workspace/git-repos/yuanrong-datasystem"'
```

若 workbench 已在远端 `~/workspace/git-repos/yuanrong-datasystem-agent-workbench`，可直接：

```bash
ssh xqyun-32c32g 'set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
export BAZEL_WHL_EXTRA_OPTS="--jobs=$(nproc) --define=enable_urma=false"
bash "${HOME}/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/testing/verify/smoke/e2e_verify_whl_path.sh" \
  "${HOME}/workspace/git-repos/yuanrong-datasystem"'
```

**串通关系**：`cquery` 给出的 `.whl` 与 `bazel-bin/bazel/openyuanrong_datasystem-*.whl` 应为 **同一 inode**；`pip install` 后脚本会打印 **`site-packages/.../datasystem_worker`**，与 `run_smoke.py` 里**优先选 whl 内 worker** 的路径一致，再跑 §3 的 `run_smoke_metrics_30s.sh` 即全链路。

**何时必须重跑 §1 / `e2e_verify_whl_path.sh`**：凡改了 **datasystem C++ client / RPC**（含 **`ClientUnaryWriterReaderImpl`**、ZMQ Unary 侧的 `KvMetric`/`RecordRpcLatencyMetrics` 打点），Smoke 必须用 **新生成的 whl**（`datasystem_worker` + `libds_client_py.so`）；仅 rsync 源码不 `pip install --force-reinstall`，Python 仍会跑旧二进制，表现为 **客户端 glog** 里没有 `zmq_rpc_e2e_latency` / `zmq_rpc_network_latency` 等。

仅看路径、不安装：`bash e2e_verify_whl_path.sh --no-build --no-pip /path/to/DS`（需已编过 wheel）。

## 2. 远端安装 whl

```bash
ssh xqyun-32c32g 'set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
cd "${HOME}/workspace/git-repos/yuanrong-datasystem"
WHL="$(bazel cquery --output=files '\''//bazel:datasystem_wheel'\'' 2>/dev/null | head -1)"
if [[ -z "${WHL}" || ! -f "${WHL}" ]]; then
  WHL=$(find "${PWD}/bazel-bin" -name "openyuanrong_datasystem-*.whl" 2>/dev/null | head -1)
fi
echo "WHL=${WHL}"
test -n "${WHL}" && test -f "${WHL}"
python3 -m pip install --user --force-reinstall "${WHL}"'
```

安装后建议：`python3 -c "from yr.datasystem.kv_client import KVClient"` 无报错。

## 3. 远端运行 smoke / 30s metrics 脚本

```bash
ssh xqyun-32c32g 'set -euo pipefail
export DS_OPENSOURCE_DIR="${HOME}/.cache/yuanrong-datasystem-third-party"
cd "${HOME}/workspace/git-repos/yuanrong-datasystem-agent-workbench"
bash scripts/testing/verify/smoke/run_smoke_metrics_30s.sh'
```

产出目录由 `run_smoke.py` 打印的 `Log output: .../results/smoke_test_*` 决定；脚本末尾会再打印 `metrics_summary.txt` 与 `metrics_summary` JSON 命中统计。

**ZMQ queue-flow（含 `zmq_rpc_e2e_latency` / `zmq_rpc_network_latency`）** 的 **`metrics_summary` 出在客户端进程的 C++ glog**：`results/smoke_test_*/clients/glog_*/*.INFO*`；不要只 grep worker。**Unary** 打点已随 KV 读写走 **`SendAll`/`SendPayloadImpl`/`Read`**，仍须满足 §1 的 **新 whl** 才生效（见 §1.1 说明）。

短跑若 **ZMQ 七项门禁 FAIL**（client 侧 histogram 在日志里 **MISSING**），确认已 **pip 重装最新 whl** 后，可加长负载，例如：  
`bash .../run_smoke_metrics_30s.sh --read-loop-sec 45 --keys 200`。

## 一键参考（本机编排）

- 更长的一键回归（含 DS rsync、单测、编 whl、smoke）：`nightly_zmq_regression.sh`
- 仅 ZMQ harness（本机/远端模式）：`harness_zmq_metrics_e2e.sh`
