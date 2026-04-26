# Results: ZMQ RPC Metrics 验证记录

## 验证状态

| 项目 | 状态 | 日期 |
|------|------|------|
| RFC 与 harness 脚本 | ✅ Done | 2026-04-26 |
| 代码：cherry-pick PR #706 → 0.8.1 | ⚠️ 非必须 | 见 README「0.8.1 现状」 |
| 本地/远程 Bazel + smoke 实跑 | ⏳ 待网络/机器 | - |

---

## 自动推进记录（Agent）

1. **Cherry-pick `8772945b` → `remotes/main/0.8.1`**：与现有 `kv_metrics` / `zmq_service` / `zmq_common` 冲突，已 **abort**。结论：`main/0.8.1` 已含 **并行实现**（`zmq_constants.h` 中 `RecordTick` + 队列 metrics），不宜盲合 PR #706 单 commit。
2. **新增 harness**：[`scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh`](../../scripts/testing/verify/smoke/harness_zmq_metrics_e2e.sh)  
   - `--local`：`build.sh -b bazel` + 查找 `openyuanrong_datasystem-*.whl` + `pip install --user` + `run_smoke.py`  
   - `--remote`：SSH `xqyun-32c32g` 同样流程（路径 `~/workspace/git-repos/...`）
3. **分支**：工作区已切回 `yche`；试合用的 `rfc-zmq-metrics-0.8.1-harness` 分支可保留或删除。

---

## 构建记录

（实跑后填充）

```
构建: bash build.sh -b bazel -t build
whl: pip install --user <openyuanrong_datasystem-*.whl>
```

---

## smoke_test 结果

（实跑后填充）

---

## metrics_summary.txt（7 项验收）

```
zmq_client_queuing_latency: <TBD>
zmq_client_stub_send_latency: <TBD>
zmq_server_queue_wait_latency: <TBD>
zmq_server_exec_latency: <TBD>
zmq_server_reply_latency: <TBD>
zmq_rpc_e2e_latency: <TBD>
zmq_rpc_network_latency: <TBD>
```

---

## 验收结果

| 验收项 | 结果 | 说明 |
|--------|------|------|
| Bazel 构建成功 | ⏳ | 用 harness |
| whl 安装 | ⏳ | |
| smoke 7 指标非空 | ⏳ | |
