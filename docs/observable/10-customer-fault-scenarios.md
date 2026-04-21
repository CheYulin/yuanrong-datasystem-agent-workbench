# 10 · 客户侧故障定位定界手册（场景化）

> **谁用**：使用 DataSystem 的业务方、现场交付 / 运维同学、集成测试同学。
> **目标**：拿到业务异常现象 → 按本手册**自证清白**或**定位到责任方** → 直接联系对应团队（**自运维 / 主机运维 / UB 运维 / etcd 运维 / 华为 DataSystem**）；无需每次都先找华为支持。
> **怎么用**：
> 1. 从 **§一 总体流程**确认现象分类。
> 2. 翻 **§三 场景目录**找最贴近的现象，按场景章节一步步走。
> 3. 若判给自己（业务/主机/网络），按**自助恢复**处理；若判给华为，按**§4.x 华为证据包**打包上报。
> 4. 本文与 [08-fault-triage-consolidated.md](08-fault-triage-consolidated.md)（华为值班内部手册）互补；本文不依赖源码知识。

---

## 一、总体定位定界流程

```
                     业务感知异常
           （接口报错 / 延迟升高 / 连不上 / 容量告警）
                           │
                           ▼
               ┌──────────────────────┐
               │ 步骤 0 · 现象分类      │
               │ Put / Get 失败？      │
               │ 延迟高？进程连不上？    │
               │ 容量满？扩缩容 / 宕机？  │
               └───────────┬──────────┘
                           │
                           ▼
               ┌──────────────────────┐
               │ 步骤 1 · 自证清白      │
               │ 业务侧先排除（§中对应）│
               │ - 参数合法 / Init 正确│
               │ - 业务 key / batch    │
               └───────────┬──────────┘
                           │
                    未复现 / 已自证
                           │
                           ▼
               ┌──────────────────────┐
               │ 步骤 2 · 边界判定      │
               │ 按场景章节出「明确证据」│
               └───────────┬──────────┘
                           │
     ┌────────┬───────────┼───────────┬────────┐
     ▼        ▼           ▼           ▼        ▼
   用户    华为          etcd         UB/URMA   主机 /
   业务    DataSystem    运维          运维       网络 / 内核
                           │
                           ▼
               ┌──────────────────────┐
               │ 步骤 3 · 交付证据包    │
               │ 按 §4.x 各场景清单     │
               │ 或 §五 通用模板        │
               └──────────────────────┘
```

**责任边界一句话**

| 边界 | 由谁排查 | 常见触发（场景入口） |
|------|---------|--------------------|
| **用户业务** | 业务方 | 参数非法 / Init 未做 / 对象不存在 / 批次超限 |
| **华为 DataSystem**（DS 进程内） | 华为 DS 支持 | Worker/Master/SDK 内部逻辑 —— RPC 处理、线程池、心跳、扩缩容 |
| **etcd 集群** | 客户 etcd 运维 | `etcd is timeout` / `etcd is unavailable` / etcd 集群不健康 |
| **UB / URMA** | 客户 UB / UMDK 运维 | UB 端口 down、UMDK 驱动异常、JFS 重建失败 |
| **主机 / 网络 / 内核** | 客户主机运维 | iptables、路由、fd / mmap / ulimit、磁盘满、OOM、分布式网盘 |

---

## 二、准备工作（日志路径 / 观测面）

### 2.1 日志位置

| 日志 | 路径 | 看什么 |
|------|------|------|
| **Client access log** | `$log_dir/ds_client_access_<pid>.log` | **首选观测面**：第一列 `code`（成功率）、第三列 `microseconds`（时延） |
| Client 运行日志 | `$log_dir/ds_client_<pid>.INFO.log` | SDK 内部 `[PREFIX]` 标签、Init 失败 |
| Worker 运行日志 | `$log_dir/datasystem_worker.INFO.log` | Worker 内部标签、`[HealthCheck]`、`etcd is ...` |
| Worker 资源 | `$log_dir/resource.log` | 周期聚合：`SHARED_MEMORY` / `ETCD_*` / 线程池 |
| Worker 三方请求 | `$log_dir/request_out.log` | Worker 访问 etcd 的轨迹 |

> `$log_dir` 由启动参数 `--log_dir` 指定，具体路径请向本地交付同事确认。

### 2.2 Access log 字段

```
code | handleName | microseconds | dataSize | reqMsg | respMsg
  ↑      ↑            ↑            ↑         ↑         ↑
 错误码  接口名        耗时(μs)     数据大小   请求参数   响应信息
```

**看错误码分布**（调任一 handle 名）：
```bash
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log \
  | awk -F'|' '{print $1}' | sort | uniq -c
```

⚠️ **陷阱**：`Get` 的 `K_NOT_FOUND` 在 access log 会被记成 `code=0`；业务「查不到」场景需看 `respMsg` 是否含 `NOT_FOUND` / `Can't find object`，不能只数非 0。

### 2.3 错误码速查

| 错误码 | 枚举 | 主责 |
|-------|------|------|
| 0 | `K_OK` | 用户；**若 Get 查不到，看 `respMsg`** |
| 2 / 3 / 8 | `K_INVALID` / `K_NOT_FOUND` / `K_NOT_READY` | **用户业务** |
| 5 | `K_RUNTIME_ERROR` | 看日志：`Get mmap entry failed`→主机；`etcd is ...`→etcd |
| 6 / 7 / 13 / 18 | `K_OUT_OF_MEMORY` / `K_IO_ERROR` / `K_NO_SPACE` / `K_FILE_LIMIT_REACHED` | **主机 / 内核** |
| 19 | `K_TRY_AGAIN` | 通常瞬时；持续出现联系 DS |
| 23 | `K_CLIENT_WORKER_DISCONNECT` | 看 Worker 进程与节点；进程在 → DS，进程没 → 主机 / 编排 |
| 25 | `K_MASTER_TIMEOUT` | **etcd** 为主，兼查网络 |
| 29 / 31 / 32 | `K_SERVER_FD_CLOSED` / `K_SCALE_DOWN` / `K_SCALING` | **DataSystem**（多为扩缩容 / 生命周期，SDK 自重试通常自愈） |
| 1001 / 1002 | `K_RPC_DEADLINE_EXCEEDED` / `K_RPC_UNAVAILABLE` | **桶码**：按 §4.1 步骤 2b 看日志前缀分 DS / 主机 / etcd |
| 1004 / 1006 / 1008 / 1009 / 1010 | `K_URMA_*` | **UB / URMA** |

---

## 三、场景目录

| # | 场景 | 典型现象 | 跳转 |
|---|-----|---------|------|
| 1 | **Put / Create / Publish 失败** | 业务调用返回非 0，成功率下跌 | [§4.1](#41-put--create--publish-失败) |
| 2 | **Get 失败或「查不到」** | Get 返回非 0；或 code=0 但业务说查不到 | [§4.2](#42-get-失败或查不到) |
| 3 | **Put / Get 延迟异常（P99↑）** | code 多为 0，但接口变慢 | [§4.3](#43-put--get-延迟异常p99) |
| 4 | **Client Init / 连接 Worker 失败** | 业务进程起不来，或首次 Put/Get 就失败 | [§4.4](#44-client-init--连接-worker-失败) |
| 5 | **SHM 容量 / 内存不足 / 泄漏** | Put 返回 `K_OUT_OF_MEMORY`；`SHARED_MEMORY` 飙升 | [§4.5](#45-shm-容量--内存不足--泄漏) |
| 6 | **扩缩容 / Worker 升级 / 重启期间业务中断** | `K_SCALING(32)` / `K_SCALE_DOWN(31)` / 短暂 1002 | [§4.6](#46-扩缩容--worker-升级--重启期间业务中断) |
| 7 | **机器 / 节点级故障** | Worker 进程消失、节点 NotReady、心跳断 | [§4.7](#47-机器--节点级故障) |

---

## 四、场景详解

### 4.1 Put / Create / Publish 失败

#### 故障现象

- 业务调用 `Put / Create / Publish` 返回非 0 `Status`
- `ds_client_access_<pid>.log` 第一列 `code` 频繁出现非 0
- 业务监控看到 Put 成功率下跌

#### 定位流程图

```
             Put 返回非 0（或成功率下跌）
                       │
                       ▼
          ┌────────────────────────────┐
          │ 步骤 1 · 看错误码分布       │
          │ grep access log awk 汇总   │
          └──────────────┬─────────────┘
                         │
   ┌─────────┬───────────┼───────────┬─────────┐
   ▼         ▼           ▼           ▼         ▼
 2/3/8    6/7/13/18   1004+       25         1001/1002
 用户     主机 / 内核  UB/URMA    etcd       需进一步定界
   │         │           │           │         │
   ▼         ▼           ▼           ▼         ▼
§4.1.1   §4.1.2       §4.1.3     §4.1.4    §4.1.5
自证     主机运维     UB 运维     etcd 运维  看前缀分流
```

#### 步骤 1 · 自证清白（业务侧先排除）

| 检查 | 命令 | 若出现 | 结论 |
|------|------|-------|------|
| 参数非法 | `grep '^2 \|' $LOG/ds_client_access_*.log` 或 `grep "K_INVALID" $LOG/ds_client_*.INFO.log` | 大量 `The objectKey is empty` / `dataSize should be bigger than zero` / `length not match` | **用户业务** — 业务侧检查参数 |
| 未 Init | grep `ConnectOptions was not configured` | 有 | **用户业务** — 检查 SDK Init 调用顺序 |
| 重复 Publish | grep `Client object is already sealed` | 有 | **用户业务** — 同 buffer 不要重复 Publish |
| 批次超限 | grep `OBJECT_KEYS_MAX_SIZE_LIMIT` | 有 | **用户业务** — 拆小 batch |

若以上都不是，进入**步骤 2**。

#### 步骤 2 · 边界判定（看错误码分布）

```bash
LOG=${log_dir:-/var/log/datasystem}
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log \
  | awk -F'|' '{print $1}' | sort | uniq -c
```

对照下表判**边界**：

##### 4.1.1 code=2 / 3 / 8 → 用户业务
上面步骤 1 已覆盖。

##### 4.1.2 code=6 / 7 / 13 / 18 → 主机 / 内核

| code | 枚举 | 确认命令 | 自助恢复 |
|-----|------|---------|---------|
| 6 | `K_OUT_OF_MEMORY` | `dmesg \| grep -i 'Out of memory'`；`free -h` | 扩内存 / 调 cgroup 上限 |
| 7 | `K_IO_ERROR` | `dmesg`；检查挂载点（本地盘或分布式网盘） | 修文件系统 / 挂载；分布式网盘故障联系存储运维 |
| 13 | `K_NO_SPACE` | `df -h`；查 `resource.log` 里 `SPILL_HARD_DISK` / `SHARED_DISK` | 清理盘 / 扩容 |
| 18 | `K_FILE_LIMIT_REACHED` | `ls /proc/<worker_pid>/fd \| wc -l` vs `ulimit -n` | `ulimit -n 65535`（永久改 /etc/security/limits.conf） |

**边界**：**主机 / 内核** — 联系主机运维；若分布式网盘故障联系存储运维。

##### 4.1.3 code=1004 / 1006 / 1008 / 1009 / 1010 → UB / URMA

| code | 枚举 | 初步命令 |
|-----|------|---------|
| 1004 / 1006 / 1008 | `K_URMA_ERROR / NEED_CONNECT / TRY_AGAIN` | `grep '\[URMA_' $LOG/datasystem_worker.INFO.log \| tail -20` |
| 1009 | `K_URMA_CONNECT_FAILED` | `ifconfig ub0`；`ubinfo`；看 UB 端口是否 up |
| 1010 | `K_URMA_WAIT_TIMEOUT` | `grep '\[URMA_WAIT_TIMEOUT\]' $LOG/datasystem_worker.INFO.log` |

**进一步分辨**：

| 证据 | 含义 |
|------|------|
| `[URMA_NEED_CONNECT]` 伴 `remoteInstanceId` 变化 | 对端 Worker 重启（查 Worker 新 pid） |
| `[URMA_NEED_CONNECT]` 持续 + `instanceId` 不变 | **UB 链路不稳** |
| `[URMA_RECREATE_JFS_FAILED]` 连续 | **UMDK / 驱动异常** |
| `[URMA_POLL_ERROR]` | **UB 硬件 / 驱动** |

**边界**：**UB / URMA 运维** — 附证据包（见 §4.1.末 证据包）。

**自助恢复**：UB 端口 DOWN → `ifconfig ub0 up`。

##### 4.1.4 code=25 → etcd

```bash
grep -E 'etcd is (timeout|unavailable)' $LOG/*.INFO.log | tail -20
grep 'ETCD_REQUEST_SUCCESS_RATE\|ETCD_QUEUE' $LOG/resource.log | tail -5
```

**是 etcd 问题的铁证**：
- `etcd is timeout` / `etcd is unavailable` 高频出现
- `resource.log` `ETCD_REQUEST_SUCCESS_RATE` 明显下跌
- `ETCD_QUEUE.CURRENT_SIZE` 堆积

**边界**：**etcd 集群运维** — 客户自运维；查 etcd 集群健康：`etcdctl endpoint status -w table`。

##### 4.1.5 code=1001 / 1002 → 看日志前缀分流

```bash
grep -E '\[(TCP|UDS|ZMQ|RPC|SHM_FD)_' $LOG/datasystem_worker.INFO.log $LOG/ds_client_*.INFO.log | head -20
```

| 第一条前缀 | 对端 Worker 进程 | 边界 | 说明 |
|-----------|-----------------|------|------|
| `[TCP_CONNECT_FAILED]` | **活** | **主机 / 网络** | 防火墙 / 路由不通 |
| `[TCP_CONNECT_FAILED]` | **无** | **DataSystem 或机器** | Worker 未拉起 |
| `[TCP_CONNECT_RESET]` / `[TCP_NETWORK_UNREACHABLE]` | — | **主机 / 网络** | 网络闪断 |
| `[UDS_CONNECT_FAILED]` / `[SHM_FD_TRANSFER_FAILED]` | — | **主机** | 同机 UDS 路径 / SCM_RIGHTS / fd / `ulimit -l` |
| `[RPC_RECV_TIMEOUT]` + 所有 ZMQ fault counter=0 | 活 | **DataSystem** | 对端处理慢 |
| `[RPC_SERVICE_UNAVAILABLE]` | 活 | **DataSystem** | 对端主动拒绝 |
| `[ZMQ_SEND_FAILURE_TOTAL]` / `[ZMQ_RECEIVE_FAILURE_TOTAL]` | — | **主机 / 网络** | ZMQ 硬失败，看 `zmq_last_error_number` 对 errno |
| 同屏 `etcd is ...` | — | **etcd** | 见 §4.1.4 |

**ZMQ errno 对照**：`111=ECONNREFUSED`、`113=EHOSTUNREACH`、`110=ETIMEDOUT`、`104=ECONNRESET`、`101=ENETUNREACH`。

**判「对端 Worker 活」的命令**（在 Worker 节点上执行）：
```bash
pgrep -af datasystem_worker
ss -tnlp | grep <worker_port>
```

#### 步骤 3 · 自助恢复速查

| 问题 | 恢复命令 | 验证 |
|------|---------|------|
| iptables 误屏蔽 | `iptables -L -n`；`iptables -D ...` | `zmq_send_failure_total` delta 归零 |
| TCP qdisc 残留（netem 注入） | `tc qdisc del dev eth0 root netem` | 延迟回基线 |
| ulimit -n 太小 | `ulimit -n 65535` | code=18 消失 |
| ulimit -l 太低 | `ulimit -l unlimited` | `Get mmap entry failed` 消失 |
| UB 端口 DOWN | `ifconfig ub0 up` | `[URMA_NEED_CONNECT]` 消失 |
| 磁盘满 | 清理 / 扩容；`df -h` 确认 | `SPILL_HARD_DISK` 回落 |

#### 华为证据包（判给 DataSystem 时）

```bash
# 在故障 Worker / Client 节点分别执行
D=ds-evidence-$(hostname)-$(date +%Y%m%d-%H%M%S)
mkdir -p $D
cp $LOG/datasystem_worker.INFO.log* $D/
cp $LOG/ds_client_*.log $D/   # client pid 相关
cp $LOG/resource.log* $D/
cp $LOG/request_out.log $D/ 2>/dev/null
pgrep -af datasystem_worker > $D/ps.txt
ss -tnlp > $D/ss.txt
ulimit -a > $D/ulimit.txt
free -h > $D/mem.txt
df -h > $D/disk.txt
uname -a > $D/uname.txt
tar -czf $D.tar.gz $D && echo "请上传 $D.tar.gz"
```

同时附**业务侧**：
- 故障时间窗（精确到分钟）
- 调用的接口名（Put/Create/Publish…）与预期行为
- access log 的错误码分布输出（`awk ... sort | uniq -c`）

---

### 4.2 Get 失败或「查不到」

#### 故障现象

- Get 返回非 0；或 **`code=0` 但业务说查不到**
- access log 里大量 `code=0` + `respMsg` 含 `Can't find object`

#### 定位流程图

```
            Get 调用失败 / 「查不到」
                       │
                       ▼
         ┌─────────────────────────────┐
         │ 先看 respMsg + code         │
         │ awk '{print $1, $NF}'       │
         └──────────────┬──────────────┘
                        │
      ┌─────────────────┼─────────────────┐
      ▼                 ▼                 ▼
 code=0               code=3            code≠0, ≠3
 respMsg含            K_NOT_FOUND       走 §4.1
 Can't find                             (Put/Create 失败流程)
 → 用户业务            → 用户业务
   对象真的不在         对象真的不在
```

#### 步骤 1 · 自证清白

```bash
# 看 Get 的 respMsg 分布
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log \
  | awk -F'|' '{print $1, $NF}' | sort | uniq -c | head
```

**关键判据**：

| 现象 | 结论 | 处置 |
|------|------|------|
| `code=0` + `respMsg` 含 `NOT_FOUND` 或 `Can't find object` | **对象不存在**（用户业务） | 检查业务是否先 Put 再 Get；key 生成逻辑；TTL 是否提前过期 |
| `code=3` `K_NOT_FOUND` | 同上 | 同上 |
| `code=8` `K_NOT_READY` | 未 Init / 正在 shutdown | 业务检查 SDK 生命周期 |
| 其它非 0 | 不是查不到问题 | 跳 §4.1 |

> **重要**：`K_NOT_FOUND` 在 access log 会被记成 **`code=0`**，**不要只看 `code` 非 0 判断**。

#### 步骤 2 · 如果 Put 确实做了但 Get 查不到

| 排查点 | 命令 / 方法 |
|--------|------------|
| **Put 是否真成功**（业务侧核对） | 查同 key 在 Put 时的 access log：`grep "<objectKey>" $LOG/ds_client_access_*.log` — Put 成功（code=0）且 Get 失败？ |
| **TTL 提前过期** | 业务确认调用 Put 时设的 TTL；查是否过了 |
| **跨 Worker 迁移 / 扩缩容**（少量窗口） | 查 Worker `etcd is ...` 或 `meta_is_moving` —— 若在扩缩容期，SDK 应自动重试；持续失败见 §4.6 |
| **对象被主动 Delete** | 业务方或清理任务 |

**边界**：以上都是**用户业务**边界。若业务流程核对无误仍查不到，判给 **DataSystem**（华为）；按 §4.1 末证据包上报，**额外附业务 Put/Get 的调用时间、key 列表**。

---

### 4.3 Put / Get 延迟异常（P99↑）

#### 故障现象

- access log 的 `microseconds` 整体右移；监控上业务 P99 超 SLA
- `code` 多为 0，不是错误类

#### 定位流程图

```
      延迟 P99↑ / microseconds 变差（code 多为 0）
                       │
                       ▼
        ┌────────────────────────────────┐
        │ Step 1  是否真变慢？            │
        │ access 聚合 P99 与基线比        │
        │ Metrics Summary delta max      │
        └─────────────┬──────────────────┘
                      │
                      ▼
        ┌────────────────────────────────┐
        │ Step 2  Client vs Worker 侧慢   │
        │ client_rpc_*_latency           │
        │   vs worker_process_*_latency  │
        └─────────────┬──────────────────┘
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   同幅飙升       Client 更慢     Worker 快/Client 慢
   DataSystem    中间链路       用户 SDK / 本地
                 → Step 3
                       │
                       ▼
        ┌────────────────────────────────┐
        │ Step 3  中间链路拆：URMA / TCP  │
        │ fallback to TCP? ZMQ fault?    │
        │ ping 抖 / tc netem?            │
        └─────────────┬──────────────────┘
                      ▼
            UB 运维 / 主机网络 / DS
```

#### 步骤 1 · 确认真变慢

**业务侧聚合 P99**：用现有业务监控平台，或：
```bash
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log \
  | awk -F'|' '{print $3}' | sort -n | \
  awk 'BEGIN{c=0} {a[c++]=$1} END{p99=a[int(c*0.99)]; print "P99="p99" us"}'
```

和基线对比；若 < 2× 基线视为正常抖动。

**看 Metrics Summary delta max**：
```bash
grep 'Compare with' $LOG/datasystem_worker.INFO.log | tail -3
# 之后 grep 对应接口的 client_rpc_get_latency / worker_process_get_latency 看 max
```

#### 步骤 2 · Client vs Worker 侧

| 对比 | 边界 |
|------|------|
| `client_rpc_get_latency` max **显著 > `worker_process_get_latency` max** | **中间链路慢** → Step 3 |
| 两者 max **同幅飙升** | **DataSystem**（Worker 业务慢、线程池打满、或写 etcd 慢） |
| Worker 快、Client 慢，且 `worker_to_client_total_bytes` delta 正常 | **用户 / SDK 本地**（反序列化、用户线程阻塞） |

#### 步骤 3 · 中间链路拆

| 证据 | 边界 |
|------|------|
| `worker_urma_write_latency` max↑ | **UB / URMA** |
| `worker_tcp_write_latency` max↑ + `client_*_urma_*_bytes` delta=0 + 日志 `fallback to TCP/IP payload` | **UB / URMA**（UB 降级 TCP） |
| `zmq_send_io_latency` / `zmq_receive_io_latency` max↑ + `zmq_send_failure_total` / `zmq_receive_failure_total` 有 delta | **主机 / 网络**（ZMQ 硬失败） |
| `zmq_*_io_latency` max↑ + 所有 fault counter=0 | 框架占比公式，< 5% 多为对端慢或中间网络 → 看 `worker_process_*` |
| `ping` 对端 RTT 抖；`tc qdisc show dev eth0` 有 netem 残留；`nstat` 重传 | **主机 / 网络** |

**自助恢复**：
| 问题 | 命令 |
|------|------|
| tc netem 残留 | `tc qdisc del dev eth0 root netem` |
| UB 降级 | 查 UB 端口 `ifconfig ub0`；修 UMDK；`ifconfig ub0 up` |

#### 步骤 4 · DataSystem 同幅飙升 → 华为 DS 支持

**证据**：
- `client_rpc_get_latency` max 与 `worker_process_get_latency` max 同时飙升
- `resource.log` 的 `*_OC_SERVICE_THREAD_POOL.WAITING_TASK_NUM` 堆积
- 或 `worker_rpc_create_meta_latency` max 飙升且 `ETCD_REQUEST_SUCCESS_RATE` **正常** —— 排除 etcd 慢

→ 交华为 DS 支持，按 §4.1 末证据包，**额外附**：
- Metrics Summary 连续 3 个 delta 段（`grep 'Compare with' ... | tail -30`）
- access log 延迟分布（P50 / P99 / max）
- 对端 Worker 的 `pidstat -p <pid> 1 10` 与 `gstack <pid>` 抓一次

---

### 4.4 Client Init / 连接 Worker 失败

#### 故障现象

- 业务进程调用 `Init` / 首次 Put/Get 就失败
- SDK 日志出现 `[TCP_CONNECT_FAILED]` / `[UDS_CONNECT_FAILED]` / `[SHM_FD_TRANSFER_FAILED]`
- 返回 `K_RPC_UNAVAILABLE(1002)` / `K_NOT_READY(8)`

#### 定位流程图

```
                Init / 首次调用失败
                       │
                       ▼
        ┌────────────────────────────────┐
        │ 步骤 1 · 确认 Worker 进程      │
        │ pgrep -af datasystem_worker    │
        └─────────────┬──────────────────┘
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   Worker 没起    Worker 活 + 端口    Worker 活 + 端口
   → DataSystem   未 LISTEN          LISTEN
     / 编排          → DataSystem              │
                                              ▼
                                  ┌───────────────────┐
                                  │ 步骤 2 · 看 SDK   │
                                  │ INFO log 第一条   │
                                  │ [...]  前缀       │
                                  └─────────┬─────────┘
                        ┌───────────────────┼───────────────────┐
                        ▼                   ▼                   ▼
                [TCP_CONNECT_FAILED]   [UDS_*]              [SHM_FD_
                + 对端 LISTEN          [SHM_FD_TRANSFER_     TRANSFER_
                → 主机 / 防火墙         FAILED]              FAILED]
                                       → 主机                 → 主机
                                       UDS 路径 / 权限         (fd / 权限)
```

#### 步骤 1 · 确认 Worker 进程与端口

在 Worker 节点：
```bash
pgrep -af datasystem_worker
ss -tnlp | grep <worker_port>    # 默认端口见部署配置
```

| 结果 | 边界 |
|------|------|
| `datasystem_worker` 进程**不存在** | **DataSystem / 编排** — 联系华为 DS 支持或编排侧拉起（systemd/k8s 查重启原因：`kubectl describe` / `journalctl -u ...`） |
| 进程在但端口**未 LISTEN** | **DataSystem** — 华为 DS 支持 |
| 进程在、端口 LISTEN | 进入步骤 2 |

#### 步骤 2 · 看 SDK INFO log 第一条结构化标签

```bash
grep -E '\[(TCP|UDS|SHM_FD)_' $LOG/ds_client_<pid>.INFO.log | head
```

| 首条前缀 | 边界 | 具体检查 | 自助恢复 |
|---------|------|---------|---------|
| `[TCP_CONNECT_FAILED]` | **主机 / 网络** | `iptables -L -n`；`nc -zv <worker> <port>` | 删除 iptables DROP 规则；检查安全组 |
| `[TCP_CONNECT_RESET]` | **主机 / 网络** | `dmesg`；`netstat -s \| grep reset` | 修网络 |
| `[UDS_CONNECT_FAILED]` | **主机** | `ls -la <uds_path>` 路径与权限；tenant_id 一致吗 | 改权限 / 按部署文档挂载 |
| `[SHM_FD_TRANSFER_FAILED]` | **主机** | `ulimit -n`（fd 数）；SELinux / AppArmor；`/proc/sys/fs/file-max` | `ulimit -n 65535`；放行 SELinux |

#### 步骤 3 · 客户侧「未配置」类

| 现象 | 边界 |
|------|------|
| SDK 日志 `ConnectOptions was not configured` | **用户业务** — 检查 Init 参数 |
| `K_NOT_READY(8)` | SDK 未完成 Init 就被调用 / 正在 shutdown | **用户业务** |

#### 华为证据包（判给 DataSystem 时）

在 Worker **和** Client 节点分别执行 §4.1 证据包命令，额外附：
- `ds_client_<pid>.INFO.log` 全部
- `kubectl describe pod <worker-pod>` 或 `journalctl -u <worker-service> -n 500`
- Init 调用的参数（IP / port / tenant_id / UDS path）

---

### 4.5 SHM 容量 / 内存不足 / 泄漏

#### 故障现象

- Put 返回 `K_OUT_OF_MEMORY(6)`
- `resource.log` 中 `SHARED_MEMORY` 迅速上涨
- Worker 进程内存 RSS 异常增长

#### 定位流程图

```
            Put 返回 6 / 内存告警
                   │
                   ▼
       ┌────────────────────────────────┐
       │ 看 resource.log SHARED_MEMORY │
       │ 和 SHM 泄漏指标               │
       └─────────────┬──────────────────┘
                     │
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
 主机 OOM          容量满          SHM 钉住泄漏
 (cgroup 超限)    (业务写入量大)   (ref 未释放)
 dmesg / free     业务正常使用     worker_shm_ref_table_bytes↑
 → 主机 / 业务     → 业务 / 容量     + object_count 持平
   扩容            规划              → DataSystem
```

#### 步骤 1 · 看 `resource.log`

```bash
grep -E 'SHARED_MEMORY|OBJECT_COUNT|OBJECT_SIZE' $LOG/resource.log | tail -5
```

**典型内存泄漏特征**：
```
SHARED_MEMORY.MEMORY_USAGE: 3.58GB → 37.5GB（短时间内飙升）
OBJECT_COUNT: 438 → 37（反而降）
OBJECT_SIZE: 降到 100MB 但 SHARED_MEMORY 没跟着降
```

这意味着：**元数据已删但物理 shm 仍被 ref 钉住** → **DataSystem 内存泄漏**。

#### 步骤 2 · 区分：主机 OOM / 容量满 / SHM 钉住

| 证据 | 边界 | 处置 |
|------|------|------|
| `dmesg \| grep -i 'Out of memory'` 有 OOM killer 记录 | **主机** | 扩物理内存 / 调 cgroup 上限 |
| `SHARED_MEMORY.MEMORY_USAGE` 稳定接近 `TOTAL_LIMIT`，`OBJECT_COUNT` 也相应多 | **业务容量规划** | 扩 SHM 池上限（部署参数）或降业务 QPS / 对象大小 |
| `SHARED_MEMORY` 持续涨 + `OBJECT_COUNT` **持平或下降** + `worker_shm_ref_table_bytes` Gauge 持续涨 | **DataSystem**（SHM ref 未释放 / 泄漏） | 见步骤 3 |

#### 步骤 3 · SHM 钉住泄漏（判给华为）

```bash
grep -E 'worker_shm_ref_table_bytes|worker_allocator_(alloc|free)_bytes_total|worker_object_count' \
  $LOG/datasystem_worker.INFO.log | tail -20
```

**判据**：
- `worker_shm_ref_table_bytes` Gauge 持续涨
- `worker_allocator_alloc_bytes_total` delta > `worker_allocator_free_bytes_total` delta
- `worker_object_count` Gauge 持平

→ 交华为 DS 支持；证据包按 §4.1 末；**额外附**：
- 连续 10 个 Metrics Summary 周期（`grep 'Compare with' $LOG/*.INFO.log | tail -100`）
- Client 侧是否有未调用 `DecRef` 的路径（业务方提供调用顺序说明）

#### 自助恢复

| 问题 | 恢复 |
|------|------|
| 主机 OOM | 扩内存 / 调 cgroup；重启 Worker 让编排拉起 |
| 容量满（正常业务） | 调大 Worker SHM 池上限（部署参数） |
| SHM 钉住（短期缓解） | 重启 Worker，让编排拉起（丢失 shm 对象数据，慎用） |

---

### 4.6 扩缩容 / Worker 升级 / 重启期间业务中断

#### 故障现象

- 扩缩容 / 升级操作期间业务看到短暂 `K_SCALING(32)` / `K_SCALE_DOWN(31)` / `K_CLIENT_WORKER_DISCONNECT(23)` / `K_RPC_UNAVAILABLE(1002)`
- SDK 通常会自重试，业务侧**偶发**失败
- Worker 日志 `meta_is_moving = true` / `[HealthCheck] Worker is exiting now`

#### 定位流程图

```
            扩缩容 / 升级期间报错
                       │
                       ▼
         ┌──────────────────────────┐
         │ 当前是否有维护操作在进行？│
         └────────────┬─────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
        是          否          否
        操作期间    维护后仍     从无维护
        偶发失败    持续失败    但频繁失败
          │           │           │
          ▼           ▼           ▼
        正常        DataSystem  非维护类
        (SDK自重试) 未恢复      按 §4.1 走
                    → 华为
```

#### 步骤 1 · 确认维护窗口

- 是否正在扩缩容 / 升级 / 灰度？（与运维核对操作记录）
- 查 Worker 日志窗口是否对应：
  ```bash
  grep -E '\[HealthCheck\] Worker is exiting now|meta_is_moving|Scaling' $LOG/datasystem_worker.INFO.log | tail -20
  ```

#### 步骤 2 · 业务侧期望行为

| code | 含义 | 业务应做 |
|------|------|---------|
| `K_SCALING(32)` | 正在扩缩容 | **SDK 通常自动重试**；应用侧若未处理请开启重试（或忍受短暂失败） |
| `K_SCALE_DOWN(31)` | Worker 正在退出 | 同上；SDK 会切到新 Worker |
| `K_CLIENT_WORKER_DISCONNECT(23)` | Worker 心跳断 | 等待重连；若持续不恢复见步骤 3 |
| 短暂 `K_RPC_UNAVAILABLE(1002)` | Worker 端口窗口期 | 同上 |

**边界**：维护窗口内、持续 < 几分钟、业务侧 SDK 自重试成功 → 属正常，**无需上报**。

#### 步骤 3 · 维护完成但业务仍持续失败

→ **DataSystem**（华为）— Worker 拉起失败 / 扩缩容卡死。

**证据**：
```bash
pgrep -af datasystem_worker                   # 新 Worker 是否起来
grep '\[HealthCheck\]' $LOG/datasystem_worker.INFO.log | tail
grep 'etcd is' $LOG/datasystem_worker.INFO.log | tail  # 扩缩容过程依赖 etcd
```

- 新 Worker 没起来 → DataSystem + 编排（`kubectl describe` / `journalctl`）
- 起来了但业务仍报 1002 持续 → 走 §4.1 步骤 2b 看日志前缀

---

### 4.7 机器 / 节点级故障

#### 故障现象

- 某节点上 Worker 进程**消失**
- 节点 NotReady / 无法 SSH / `ping` 对端节点不通
- 业务看到大量 `K_CLIENT_WORKER_DISCONNECT(23)` / `K_RPC_UNAVAILABLE(1002)` / `Cannot receive heartbeat from worker.`

#### 定位流程图

```
         心跳断 / 1002 聚集发生在某节点
                       │
                       ▼
         ┌──────────────────────────┐
         │ 该节点是否可达？          │
         │ ping / ssh              │
         └────────────┬─────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼
        不可达       可达
        → 主机 / 基础  │
          设施 / 机房   ▼
                   ┌───────────────────┐
                   │ Worker 进程在吗？  │
                   └─────────┬─────────┘
                             │
                 ┌───────────┼───────────┐
                 ▼           ▼
               没了         在但心跳不通
               → 见下        → 见下
```

#### 步骤 1 · 节点级自查

```bash
ping -c 3 <node_ip>
ssh <node> 'uptime; uname -a; free -h; df -h'
```

| 现象 | 边界 |
|------|------|
| 不可达 / SSH 不通 | **主机 / 基础设施** — 联系机房 / 云平台 |
| 节点 NotReady（k8s） | **编排 / 主机** — `kubectl describe node <n>` 看 taints / conditions |
| 节点健康 | 进步骤 2 |

#### 步骤 2 · Worker 进程级自查

```bash
ssh <node> 'pgrep -af datasystem_worker'
ssh <node> 'dmesg | tail -100'
ssh <node> 'journalctl -u <worker-service> -n 200'  # 或 kubectl logs --previous
```

| 现象 | 边界 | 处置 |
|------|------|------|
| 进程没了，dmesg 有 OOM killer 杀掉 | **主机** — 扩内存 / 调 cgroup | 补内存后编排拉起 |
| 进程没了，`journalctl` 有非零退出码但无 OOM | **DataSystem** — 进程 crash | 上报华为 + 附 journalctl / core dump |
| 进程在，但对外端口不 LISTEN | **DataSystem** | 上报华为 |
| 进程在、端口 LISTEN 但业务心跳断 | **主机 / 网络**（中间网络路径） | 查 iptables / 路由 / MTU |

#### 步骤 3 · 批量节点级（多节点同时故障）

→ 几乎必然是**基础设施 / 网络 / 编排** — 联系机房 / SRE。

#### 华为证据包（判给 DataSystem 时）

- 节点 dmesg（最近 500 行）
- `journalctl -u <worker-service> -n 500`
- `kubectl describe pod <worker-pod>` / `kubectl logs <worker-pod> --previous`
- 如有 core dump：`/var/lib/systemd/coredump/` 或 `coredumpctl list`
- `resource.log` 故障前 30 分钟
- `datasystem_worker.INFO.log`（可能已不存在 → 从编排的持久卷里取）

---

## 五、证据包通用模板

### 5.1 一键打包脚本

把下面另存为 `collect-ds-evidence.sh`，在故障节点执行：

```bash
#!/bin/bash
set -u
LOG=${log_dir:-/var/log/datasystem}
D=ds-evidence-$(hostname)-$(date +%Y%m%d-%H%M%S)
mkdir -p $D/logs $D/system

# 日志
cp -a $LOG/datasystem_worker.INFO.log*      $D/logs/ 2>/dev/null
cp -a $LOG/ds_client_*.log                  $D/logs/ 2>/dev/null
cp -a $LOG/resource.log*                    $D/logs/ 2>/dev/null
cp -a $LOG/request_out.log*                 $D/logs/ 2>/dev/null
cp -a $LOG/access.log*                      $D/logs/ 2>/dev/null

# 系统状态
{
  echo "=== uname ===";  uname -a
  echo "=== uptime ===";  uptime
  echo "=== free  ===";  free -h
  echo "=== df    ===";  df -h
  echo "=== ulimit===";  ulimit -a
  echo "=== ps    ===";  pgrep -af datasystem_worker
  echo "=== ss    ===";  ss -tnlp
  echo "=== iptables ==="; iptables -L -n 2>/dev/null
  echo "=== tc    ===";  tc qdisc show 2>/dev/null
  echo "=== ub    ===";  ifconfig ub0 2>/dev/null; ubinfo 2>/dev/null; ibstat 2>/dev/null
  echo "=== dmesg ===";  dmesg | tail -500
} > $D/system/env.txt

# 编排（按需启用）
# kubectl describe pod <worker-pod>   > $D/system/kube-describe.txt 2>/dev/null
# kubectl logs <worker-pod> --previous > $D/system/kube-logs-prev.txt 2>/dev/null
# journalctl -u <worker-service> -n 1000 > $D/system/journal.txt 2>/dev/null

tar -czf $D.tar.gz $D && rm -rf $D
echo "证据包：$(pwd)/$D.tar.gz"
```

### 5.2 业务侧需同时附

| 项 | 说明 |
|----|------|
| 故障时间窗 | 精确到分钟，带时区 |
| 业务调用概况 | 接口名、QPS、对象大小、batch 大小 |
| access log 错误码分布 | `awk ... sort | uniq -c` 的输出 |
| 对比基线 | 正常时段同指标（便于对照） |
| 业务 SDK 版本 | `ldd` / `strings` 查或运维侧给 |
| 最近变更 | 升级、扩缩容、iptables / tc 规则、限流变更 |

---

## 六、FAQ

**Q1**：看到 `code=0` 的业务日志也能算失败吗？
A：`Get` 的 `K_NOT_FOUND` 会被记成 `code=0`。业务「查不到」场景必须看 `respMsg`。见 §4.2。

**Q2**：`K_SCALING(32)` / `K_SCALE_DOWN(31)` 是故障吗？
A：通常不是。这是扩缩容 / 升级窗口，SDK 通常自动重试即恢复。持续不恢复才是问题，见 §4.6。

**Q3**：看到 `code=1002`，是华为的问题吗？
A：**不一定**。1002 是桶码，华为 DS、主机网络、etcd 都可能触发，必须按 §4.1 步骤 2b 看日志前缀。

**Q4**：看到 `[URMA_NEED_CONNECT]` 就要找华为吗？
A：不是。`[URMA_NEED_CONNECT]` 伴随 `remoteInstanceId` 变化是**对端 Worker 重启**（正常）；持续 + `instanceId` 不变是 **UB 链路问题**（UB 运维）；只有同屏还有 `[URMA_RECREATE_JFS_FAILED]` 连续出现才可能需要华为一起看。

**Q5**：`etcd is timeout` 是华为 DS 的 bug 吗？
A：**不是**。DS 只是 etcd 的客户端，日志只是反映访问 etcd 失败的事实。根因在 etcd 集群或到 etcd 的网络，**联系客户 etcd 运维**。

**Q6**：什么时候必须找华为？
A：
- 错误码是 `K_RUNTIME_ERROR(5)` / `K_DATA_INCONSISTENCY(20)` 且无法用本手册定到其它边界
- `[RPC_RECV_TIMEOUT]` + 所有 ZMQ fault=0 + 对端 Worker 活 + 线程池 `WAITING_TASK_NUM` 堆积
- `worker_shm_ref_table_bytes` 持续涨 + `worker_object_count` 持平（SHM 钉住）
- Worker 进程消失 + dmesg 无 OOM + journalctl 非零退出
- 业务逻辑核对无误但 Get 长期查不到 Put 已成功的对象

找华为时**务必附 §五 证据包**，否则会增加多次沟通。

---

## 附录 · 常用命令速查

### 日志 grep

```bash
LOG=${log_dir:-/var/log/datasystem}

# 错误码分布（换 handle 名即可看其它接口）
grep "DS_KV_CLIENT_GET"  $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_PUT"  $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c

# 结构化日志前缀（通断主抓手）
grep -E '\[(TCP|UDS|ZMQ|RPC|SOCK|REMOTE|SHM_FD|URMA)_' \
  $LOG/datasystem_worker.INFO.log $LOG/ds_client_*.INFO.log | head -50

# Metrics delta 段（时延必看）
grep 'Compare with' $LOG/datasystem_worker.INFO.log | tail -3

# etcd 两种字符串
grep -E 'etcd is (timeout|unavailable)' $LOG/*.INFO.log | tail

# Worker 退出 / 心跳
grep -E '\[HealthCheck\] Worker is exiting now|Cannot receive heartbeat from worker' $LOG/*.INFO.log

# URMA 降级
grep 'fallback to TCP/IP payload' $LOG/*.INFO.log

# resource.log 核心
grep -E 'SHARED_MEMORY|ETCD_QUEUE|ETCD_REQUEST_SUCCESS_RATE|WAITING_TASK_NUM' $LOG/resource.log | tail
```

### 主机 / 网络

```bash
ulimit -a
free -h; df -h; dmesg | tail -200
ls /proc/<pid>/fd | wc -l
ss -tnlp | grep <worker_port>
iptables -L -n
tc qdisc show dev eth0
ping -c 5 <peer>
nc -zv <peer> <port>
```

### UB / URMA

```bash
ifconfig ub0
ubinfo 2>/dev/null || ibstat
ls /dev/ub*
```

### etcd

```bash
systemctl status etcd
etcdctl endpoint status -w table
etcdctl endpoint health
```

### DataSystem 进程

```bash
pgrep -af datasystem_worker
pidstat -p <pid> 1 5
gstack <pid>        # 或 pstack
```

---

> **变更反馈**：若在现场遇到本手册未覆盖的场景，请按 §五 证据包提交华为 DS 同事，我们会把新场景补进本文。
