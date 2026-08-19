# Worker QueryAndGet RFC 多代理评审记录

| 项目 | 值 |
|---|---|
| 基线 | `71fada0780e4f3d5475c7d7a9df1f5ae8e1bd042` |
| 评审 | 架构数据流 / PB 与 SHM 安全 / 测试与性能 |
| 结论 | 原草案有阻断项；全部接受并已修订为复用 `GetReqPb/GetRspPb` 的方案 A |

## 1. 阻断项与处置

| 严重度 | 问题 | 处置 |
|---|---|---|
| Critical | 新 request 缺 AK/SK 100-102，无法使用现有签名模板 | 删除新 request；直接复用完整 `GetReqPb` |
| Critical | 新 response 丢失多 key 失败占位和 object index | 删除新 response；直接复用完整 `GetRspPb` |
| Critical | 新 request 缺 `request_timeout`，改变 SHM ref 回收 | 复用 `GetReqPb.request_timeout`，deadline 不重置 |
| Critical | 简单 PB 适配不能强制现有 Get core transport | 不重写 carrier；Client 通过 Shm marker 显式声明同节点，Worker与 session 状态交叉校验；UB/TCP沿用既有字段 |
| Critical | 远端目标 Worker 未注册，现有 Get 鉴权会失败 | `GetReqPb` 追加 `is_routed`；SHM 走 session 鉴权，UB/TCP routed 走签名鉴权 |
| Critical | ZMQ RPC 方法用 ordinal，插入 service 中部会错调 | 新方法只能追加到 `WorkerOCService` 末尾；补 ZMQ/bRPC 混版测试 |
| High | Get core 与 typed stream 强耦合，所谓 adapter 不具体 | 两个 RPC 使用相同 Req/Rsp writer；`Get(serverApi, GetRpcKind)` 共享完整 handler |
| High | capability/fallback 没有可执行状态机 | capability 请求前确认并缓存；仅未执行时回退；timeout/cancel/write failure 不 replay |
| High | RPC 计数可能混入 Worker 内部 RPC | 新增 QAG 与 phase2 single/batch 独立计数；Worker QueryMeta/RemoteGet 单列 |
| High | metadata-affinity hit/miss 测试前置不完整 | writer 显式 `PREFERRED_META_OWNER`；miss 显式非 owner placement，并用注入计数证明 |

## 2. 重要非阻断项

| 问题 | 处置 |
|---|---|
| `ObjectInfoPb` 也可表示 not-found/RemoteH2D，不能包装成 `ShmInfoPb` | 删除包装；只由既有严格校验识别有效 SHM |
| SHM fd 需 side-channel，不能直接 mmap store_fd | 明确只在 `ShmSession` 内复用 fd lookup、mmap 和 Buffer owner |
| UB success 与 TCP fallback 都使用 payload_info | 保留既有 mixed payload 语义，不新造请求级 response oneof |
| URMA Mock 构建会漏 TCP 分支 | Tiantiyun 建立非 URMA 与 URMA Mock 两套 CMake build |
| 三方缓存变量未写成可执行形式 | 明确 `DS_OPENSOURCE_DIR=/home/cache/ds-thirdparty-cache` |
| 性能门槛与样本不完整 | 使用 dsbench，固定 size/batch/concurrency/owner，5 轮 AB/BA，报告 P99/PMax/TPS/MiB/s |

## 3. 收敛后的关键协议

```protobuf
message QueryAndGetShmPb {}

message GetReqPb {
  // fields 1 through 14 unchanged
  QueryAndGetShmPb query_and_get_shm = 15;
  bool is_routed = 16;
  // fields 100 through 102 unchanged
}

service WorkerOCService {
  // all existing methods keep their ordinals
  // append as the last method only
  rpc QueryAndGet(GetReqPb) returns (GetRspPb) {
    option (datasystem.unary_socket_option) = true;
    option (datasystem.recv_payload_option) = true;
  }
}
```

评审后没有保留第二套 QueryAndGet request/response、`ShmInfoPb` wrapper 或 response conversion。

## 4. CodeGraph 与源码边界

主代理在 exact-HEAD 独立 worktree 创建了新索引并运行 query/callers/impact；索引状态为 pending changes 0，
56,972 nodes、180,633 edges。评审代理看到的共享主 checkout 索引曾指向旧 HEAD，因此它们只把图用于发现，
所有结论均在 `71fada078...` 源码重新核对。实现完成后必须在实现 worktree sync/index，并重新执行
query/callers/impact/affected。

