# /kind refactor

**这是什么类型的 PR？**

/kind refactor（含可靠性语义修复与可观测性增强；不改变对外 API）

---

**这个 PR 是做什么的 / 我们为什么需要它**

- 修复 URMA wait timeout 错误码语义，避免误落入 RPC 语义码。
- 补齐 TCP/URMA 关键故障日志，统一可检索前缀与上下文字段。
- 对 `K_RPC_UNAVAILABLE(1002)` 做子分类前缀，提升 TCP/RPC 问题定界效率。
- 提供自动化日志验收脚本与远端流程接入，降低人工漏检与回归成本。

---

**此 PR 修复了哪些问题**

关联：KVClient URMA/TCP 定界与可观测性整改专项。  
Fixes #<ISSUE_ID>

---

**PR 对程序接口进行了哪些修改？**

- 无客户可见 API 签名变化。
- 内部错误语义与日志增强：
  - URMA timeout 统一返回 `K_URMA_WAIT_TIMEOUT`
  - TCP/RPC 1002 前缀子分类
  - URMA 故障日志统一关联字段并限频

---

**关键信息**

- **可靠性语义**
  - `Event::WaitFor` / `EventWaiter::WaitAny` / `UrmaManager::WaitToFinish` 超时语义统一。
  - reconnect / poll / recreate 场景日志可直接检索。

- **可观测性**
  - TCP 与 URMA 双域定界关键字体系可独立检索。
  - URMA 故障日志关联字段：`remoteAddress`、`remoteInstanceId`、`operationType`。
  - worker 侧补充：`remoteWorkerId`（`remoteAddress -> workerId` 映射）。

- **性能开销控制**
  - 新增日志仅故障路径输出。
  - 使用 `LOG_EVERY_N` 限频，避免故障风暴时日志反压。

---

**实现思路（摘要）**

- **URMA 核心路径**
  - `rdma_util.h` / `urma_manager.cpp`：修复超时错误码语义。
  - `urma_manager.cpp`：`URMA_POLL_ERROR`、`URMA_NEED_CONNECT`、`URMA_RECREATE_JFS*` 日志增强。
  - `urma_resource.h/.cpp`：`UrmaEvent` 增加只读上下文，JFS 重建日志增强。

- **TCP/RPC 路径**
  - `zmq_msg_queue.h`、`zmq_stub_impl.h`、`zmq_stub_conn.cpp`、`unix_sock_fd.cpp`、`client_worker_common_api.cpp`：
    1002 子分类前缀统一。

- **验收流程**
  - 新增：`scripts/testing/verify/validate_urma_tcp_observability_logs.sh`
  - 新增：`./ops test.urma_tcp_logs`
  - 接入：`scripts/build/remote_build_run_datasystem.sh` 可选日志验收参数。

---

**验证结果（简版）**

- 构建通过：
  - `common_rdma`
  - `worker_object_cache`
- 定向回归：
  - `UnixSockFdStatusTest.*` 通过（3/3）
- 脚本校验：
  - 新增脚本与 `ops` 入口语法校验通过
  - 文档与验收清单已同步更新

---

**Commit 提交信息说明**

**PR 标题示例**：  
`refactor(urma,tcp): improve fault observability boundaries and timeout semantics`

**Commit 信息建议**：

```text
refactor(urma,tcp): improve fault observability and timeout semantics

- return K_URMA_WAIT_TIMEOUT for urma wait timeout paths
- add structured URMA error logs with rate limiting
- add 1002 subclassification prefixes for TCP/RPC errors
- add automated URMA/TCP log validation script and ops entry
- sync verification docs and acceptance checklist
```

---

**Self-checklist**

- [ ] URMA wait timeout 语义一致（无 RPC 语义回退）
- [ ] `URMA_NEED_CONNECT` / `URMA_POLL_ERROR` / `URMA_RECREATE_JFS*` 日志可检索
- [ ] 1002 子分类前缀覆盖核心场景
- [ ] 自动与手工验收流程均可执行
- [ ] 无未声明的对外 API 行为变化
