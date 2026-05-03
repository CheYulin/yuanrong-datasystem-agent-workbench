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
find bazel-bin -name "openyuanrong_datasystem-*.whl" | head -1'
```

**Wheel** 落在 **`bazel-bin/bazel/`** 下，应对 **`bazel-bin` 递归查找**；不要用对仓库根过小的 **`maxdepth`**，否则 **`find` 结果为空**。

以 **`bazel build` 退出码 0** 且 **`find` 打印出 `.whl` 路径**为准。

## 2. 远端安装 whl

```bash
ssh xqyun-32c32g 'WHL=$(find "${HOME}/workspace/git-repos/yuanrong-datasystem/bazel-bin" \
  -name "openyuanrong_datasystem-*.whl" 2>/dev/null | head -1)
  echo "WHL=${WHL}"
  test -n "${WHL}" && python3 -m pip install --user --force-reinstall "${WHL}"'
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

短跑若 **ZMQ 七项门禁 FAIL**（client 侧 histogram 在日志里 **MISSING**），可先加长负载，例如：  
`bash .../run_smoke_metrics_30s.sh --read-loop-sec 45 --keys 200`。

## 一键参考（本机编排）

- 更长的一键回归（含 DS rsync、单测、编 whl、smoke）：`nightly_zmq_regression.sh`
- 仅 ZMQ harness（本机/远端模式）：`harness_zmq_metrics_e2e.sh`
