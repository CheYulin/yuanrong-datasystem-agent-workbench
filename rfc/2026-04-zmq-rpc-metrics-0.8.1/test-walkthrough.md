# Test Walkthrough: ZMQ RPC Metrics 验证

## 1. 验证目标

确认 `ENABLE_PERF=false` 时，ZMQ metrics 能正常分段时间。

## 2. 验证环境

- 远程主机：`xqyun-32c32g`
- 构建：**Bazel**（`build.sh -b bazel`）+ **whl** `pip install --user`（与 Python 侧 `yr` 一致）
- E2E：**仅** `run_smoke.py`（已含 etcd、workers、客户端、`metrics_summary.txt`）

## 3. 验证步骤

### 步骤 1: 同步 + Bazel + whl

与 [README 步骤 2～2b](README.md#步骤-2-rsync-同步到远程) 相同：`sync_to_xqyun.sh` → 远程 `build.sh -b bazel -t build` → 安装 whl。

Workbench 本机快捷：`bash scripts/build/build_bazel.sh`（在 `yuanrong-datasystem` 根等价于 `build.sh -b bazel`）。

### 步骤 1b（与 PR #706 一致的前提）

- Datasystem 已含 PR #706 的 C++ 变更（cherry-pick 到 `main/0.8.1`）。
- `run_smoke.py` 中 `ZMQ_METRIC_PATTERNS` 已含 7 个 `zmq_*` 分段名（与 `kv_metrics` 小写名一致）。

---

### 步骤 2: 运行 smoke_test（脚本内含 etcd + worker）

默认会跑 **约 2 分钟/客户端** 的读循环 + 多路 get 放大，并在 `metrics_summary.txt` 里对 7 个 ZMQ 分段直方图检查 **`count=…` ≥ `--min-zmq-metric-count`（默认 50）**；不足则 **进程退出码 1**（避免“只有几条样本”也当验收通过）。

```bash
ssh xqyun-32c32g \
  'cd ~/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/testing/verify/smoke && \
   python3 run_smoke.py'
# 调负载示例：--read-loop-sec 180 --min-zmq-metric-count 80
# 仅调试（降低门槛，不作为正式验收）：--min-zmq-metric-count 5
```

### 步骤 3: 检查 metrics

```bash
cat ~/workspace/git-repos/yuanrong-datasystem-agent-workbench/results/smoke_test_*/metrics_summary.txt
# 末段应有 RESULT: PASS 与 ZMQ flow metrics gate
```

<a id="worker-only-grep"></a>

### 步骤 3b: 仅从 **Worker** glog grep（不看 client）

`run_smoke.py` 解析时会同时扫 worker 与 `clients/glog_*`。若你**只关心 worker 进程**里的打点，请**只对 worker 的 INFO glog** 做 `grep`，不要打开 `client_*`、`clients/glog_*`。

Smoke 结束后，worker 日志通常有两种落点（有其一即可）：

- 结果目录根下扁平拷贝：`worker-<port>_datasystem_worker.INFO.log`
- 或尚未整理前：`workers/worker-<port>/datasystem_worker.INFO.log`

在 **xqyun-32c32g**（路径按你实际 clone 调整）：

```bash
WB="${WB:-$HOME/workspace/git-repos/yuanrong-datasystem-agent-workbench}"
LATEST="$(ls -td "${WB}/results"/smoke_test_* 2>/dev/null | head -1)"
test -n "$LATEST" || { echo "No smoke_test_* under ${WB}/results"; exit 1; }

# 仅 worker INFO（不要带 client_glog / glog_t*）
shopt -s nullglob
W=( "${LATEST}"/worker-*_datasystem_worker*.INFO.log )
if ((${#W[@]}==0)); then W=( "${LATEST}"/workers/worker-*/*.INFO.log ); fi
if ((${#W[@]}==0)); then echo "No worker INFO logs under $LATEST"; exit 1; fi

# 整行 JSON（metrics_summary）+ server 分段 + 常见 I/O / 序列化名（均为 worker 侧观测）
grep -hE 'metrics_summary|zmq_server_|zmq_rpc_e2e_latency|zmq_rpc_network_latency|zmq_send_io_latency|zmq_receive_io_latency|zmq_rpc_serialize_latency|zmq_rpc_deserialize_latency' "${W[@]}" | tail -n 120
```

只看 **Server 分段 + E2E/Network**（更窄）：

```bash
grep -hE 'metrics_summary|zmq_server_queue_wait_latency|zmq_server_exec_latency|zmq_server_reply_latency|zmq_rpc_e2e_latency|zmq_rpc_network_latency' "${W[@]}" | tail -n 80
```

说明：

- **`zmq_client_queuing_latency` / `zmq_client_stub_send_latency`** 主要在 **client 子进程** glog；按本步骤**刻意不查**，故上列 grep **不应**依赖这两行出现在 worker 里。
- 若需要 **diag 单行**（非 JSON），可再加：`'\\[Zmq|Zmq '`（视 C++ 日志格式而定）。

## 4. 预期结果

### 4.1 新增 Metrics 有值（7 个，与 PR #706 / `kv_metrics` 一致）

```
zmq_client_queuing_latency: <value>
zmq_client_stub_send_latency: <value>
zmq_server_queue_wait_latency: <value>  # 自证 network 等待
zmq_server_exec_latency: <value>       # 自证业务逻辑
zmq_server_reply_latency: <value>      # 自证 RPC framework
zmq_rpc_e2e_latency: <value>           # 端到端
zmq_rpc_network_latency: <value>       # 网络延迟
```

### 4.2 现有 Metrics 正常

```
zmq_send_io_latency: <value>
zmq_receive_io_latency: <value>
zmq_rpc_serialize_latency: <value>
zmq_rpc_deserialize_latency: <value>
```

## 5. 故障注入验证（可选）

如需验证 TCP 故障时的 metrics 行为，可参考：
- `scripts/testing/verify/verify_zmq_metrics_fault.sh`
- `scripts/testing/verify/verify_zmq_fault_injection_logs.sh`

## 6. 验收 Checklist

- [ ] `ENABLE_PERF=false` 构建成功
- [ ] smoke_test 运行成功
- [ ] `zmq_client_queuing_latency` 有非零值
- [ ] `zmq_client_stub_send_latency` 有非零值
- [ ] `zmq_server_queue_wait_latency` 有非零值
- [ ] `zmq_server_exec_latency` 有非零值
- [ ] `zmq_server_reply_latency` 有非零值
- [ ] `zmq_rpc_e2e_latency` 有非零值
- [ ] `zmq_rpc_network_latency` 有非零值
