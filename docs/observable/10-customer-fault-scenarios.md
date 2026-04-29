# 定位定界场景案例

> **谁用**：使用 yuanrong-datasystem 的业务方、现场交付与运维、集成测试。
> **与主手册的关系**：请先阅读 [定位定界手册](fault_triage_consolidated.md)（四边界、失败/时延分流、指标与附录）。本文从**客户视角**补充：**现象分类 → 自证清白 → 责任边界 → 证据包**，与主手册互补。

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
               │ 业务侧先排除            │
               │ 参数合法 / Init 正确    │
               └───────────┬──────────┘
                           │
                           ▼
               ┌──────────────────────┐
               │ 步骤 2 · 边界判定      │
               │ 按场景章节出明确证据    │
               └───────────┬──────────┘
                           │
     ┌────────┬───────────────┼───────────┬────────┐
     ▼        ▼               ▼           ▼        ▼
   用户     华为            etcd         UB/URMA   主机 /
   业务   yuanrong-       运维          运维       运维       网络 / 内核
          datasystem
          (进程内)
```

**责任边界一览**

| 边界 | 由谁排查 | 常见触发（场景入口） |
|------|---------|----------------------|
| **用户业务** | 业务方 | 参数非法、Init 未做、对象不存在、批次超限 |
| **华为 yuanrong-datasystem**（进程内） | 华为 yuanrong-datasystem 支持 | RPC、线程池、心跳、扩缩容、SHM 钉住等 |
| **etcd 集群** | 客户 etcd 运维 | `etcd is timeout` / `unavailable`、集群不健康 |
| **UB / URMA** | 客户 UB / UMDK 运维 | UB 端口 down、UMDK 异常、JFS 重建失败 |
| **主机 / 网络 / 内核** | 客户主机运维 | iptables、路由、fd/mmap/ulimit、磁盘满、OOM |

详细错误码与日志前缀分流见主手册 [第二节](fault_triage_consolidated.md#二四边界速记) 与本文 [第二节](#二准备工作日志路径--观测面)。

---

## 二、准备工作（日志路径 / 观测面）

> **说明**：本文 **§X** 表示"第X节"，如下方 **§1** 指"第一节"，**§4.1** 指"第四节第1小节"。

`$log_dir` 由启动参数 `--log_dir` 指定，具体路径以现场交付为准。

| 日志 | 路径 | 看什么 |
|------|------|--------|
| **Client access log** | `$log_dir/ds_client_access_<pid>.log` | 第一列 `code`、第三列 `microseconds`（**首选**） |
| Client 运行日志 | `$log_dir/ds_client_<pid>.INFO.log` | SDK `[PREFIX]`、Init 失败 |
| Worker 运行日志 | `$log_dir/datasystem_worker.INFO.log` | Worker 标签、`etcd is ...`、URMA/TCP/UDS |
| Worker 资源 | `$log_dir/resource.log` | `SHARED_MEMORY`、`ETCD_*`、线程池 |
| Worker 三方请求 | `$log_dir/request_out.log` | 访问 etcd 轨迹 |

**Access log 字段**：

```
code | handleName | microseconds | dataSize | reqMsg | respMsg
```

**陷阱**：`Get` 的 `K_NOT_FOUND` 在 access log 常记为 **`code=0`**；「查不到」须看 `respMsg` 是否含 `NOT_FOUND` / `Can't find object`。

**错误码速查（主责）**

| 错误码 | 含义摘要 | 主责 |
|--------|----------|------|
| 0 | `K_OK`；Get 查不到时可能仍为 0 | 用户；看 `respMsg` |
| 2 / 3 / 8 | 非法参数 / 未找到 / 未就绪 | **用户业务** |
| 5 | `K_RUNTIME_ERROR` | 看日志区分 mmap / etcd |
| 6 / 7 / 13 / 18 | 内存 / IO / 空间 / fd 上限 | **主机 / 内核** |
| 19 / 23 / 29 / 31 / 32 | 重试、断连、扩缩容相关 | 多为 **yuanrong-datasystem** 或维护窗口；见 [场景 6](#46-扩缩容--worker-升级--重启期间业务中断) |
| 25 | Master 超时 | **etcd** 为主，兼查网络 |
| 1001 / 1002 | RPC 超时 / 不可用 | **桶码**：按日志前缀分 yuanrong-datasystem / 主机 / etcd |
| 1004–1010 | `K_URMA_*` | **UB / URMA** |

---

## 三、场景目录

| # | 场景 | 典型现象 | 章节 |
|---|------|----------|------|
| 1 | Put / Create / Publish 失败 | 非 0、成功率跌 | [第四节·1](#41-put--create--publish-失败) |
| 2 | Get 失败或「查不到」 | 非 0 或 code=0 但业务查不到 | [第四节·2](#42-get-失败或查不到) |
| 3 | Put / Get 延迟异常（P99↑） | code 多为 0，变慢 | [第四节·3](#43-put--get-延迟异常p99) |
| 4 | Client Init / 连接 Worker 失败 | Init 或首次调用失败 | [第四节·4](#44-client-init--连接-worker-失败) |
| 5 | SHM 容量 / 内存不足 / 泄漏 | `K_OUT_OF_MEMORY(6)`、`SHARED_MEMORY` 升 | [第四节·5](#45-shm-容量--内存不足--泄漏) |
| 6 | 扩缩容 / 升级 / 重启期间中断 | 31/32/23/1002 偶发 | [第四节·6](#46-扩缩容--worker-升级--重启期间业务中断) |
| 7 | 机器 / 节点级故障 | Worker 消失、节点不可达 | [第四节·7](#47-机器--节点级故障) |

---

## 四、场景详解（要点）

### 4.1 Put / Create / Publish 失败

**现象**：接口返回非 0；access log 第一列非 0 增多；Put 成功率下跌。

**定位思路**（与主手册 [失败](fault_triage_consolidated.md) 定界一致）：

1. **自证**：参数非法、`ConnectOptions was not configured`、重复 Publish、批次超限 → **用户业务**。
2. **按错误码分边界**：2/3/8 → 用户；6/7/13/18 → 主机/内核；1004–1010 → UB/URMA；25 → etcd；1001/1002 → 用日志前缀分流（`TCP_*` / `UDS_*` / `ZMQ_*` / `RPC_*` / `etcd`）。

**UB/URMA 进一步分辨**（摘自现场案例）：

| 证据 | 含义 |
|------|------|
| `[URMA_NEED_CONNECT]` 且 `remoteInstanceId` 变化 | 对端 Worker 重启 |
| `[URMA_NEED_CONNECT]` 持续且 `instanceId` 不变 | UB 链路不稳 |
| `[URMA_RECREATE_JFS_FAILED]` 连续 | UMDK / 驱动异常 |

> **JFS 重建期间业务影响**：JFS 重建过程中，业务侧可能短暂感知到 `code=19`（`K_TRY_AGAIN`）或 P99 抖动，属于预期现象；若重建**失败**且连续出现，则需联系 URMA/UB 运维。
| `[URMA_POLL_ERROR]` | UB 硬件 / 驱动 |

**etcd 铁证示例**：`grep -E 'etcd is (timeout|unavailable)'`；`resource.log` 中 `ETCD_REQUEST_SUCCESS_RATE` 下跌、`ETCD_QUEUE` 堆积。

**自助恢复速查**：iptables 误拦、`tc netem` 残留、`ulimit -n`/`ulimit -l`、UB `ifconfig ub0 up`、磁盘满清理等（见主手册 OS/网络小节）。

**判给华为 yuanrong-datasystem 时的证据包**：见 [第五节](#五证据包通用模板)。

---

### 4.2 Get 失败或「查不到」

**现象**：Get 非 0；或 **code=0 但业务认为查不到**。

**要点**：

```bash
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log \
  | awk -F'|' '{print $1, $NF}' | sort | uniq -c | head
```

| 现象 | 结论 |
|------|------|
| `code=0` 且 `respMsg` 含 `NOT_FOUND` / `Can't find object` | 对象不存在 → **核对业务 Put/Get 顺序、key、TTL** |
| `code=3` | 同上 |
| 其它非 0 | 按 [4.1](#41-put--create--publish-失败) 失败定界流程 |

若 Put 已成功仍长期查不到且业务逻辑已核对 → 判 **yuanrong-datasystem**，附 Put/Get 时间与 key 列表及 [证据包](#五证据包通用模板)。

---

### 4.3 Put / Get 延迟异常（P99↑）

**现象**：`microseconds` 右移或监控 P99 超 SLA；`code` 多为 0。

**步骤摘要**（与主手册 [时延](fault_triage_consolidated.md) 定界互补）：

1. 用 access log 或平台确认 **P99 相对基线** 真变差；看 Metrics Summary `Compare with` 段与 histogram **max**。
2. **Client vs Worker**：`client_rpc_*_latency` max 与 `worker_process_*_latency` max 对比——同幅飙升多属 yuanrong-datasystem 内；Client 侧明显更慢则拆中间链路或用户侧。
3. **中间链路**：`worker_urma_*`、`fallback to TCP/IP payload`、`zmq_*_io_latency` 与 fault counter、ping/tc/nstat 等 → UB 运维或主机网络。
4. 同幅飙升且 etcd 指标正常、线程池 `WAITING_TASK_NUM` 堆积等 → 交华为 yuanrong-datasystem，附连续多周期 Metrics 与延迟分布。

---

### 4.4 Client Init / 连接 Worker 失败

**现象**：`Init` 或首次 Put/Get 失败；日志出现 `[TCP_CONNECT_FAILED]`、`[UDS_*]`、`[SHM_FD_TRANSFER_FAILED]`；1002/8 等。

**步骤**：

1. Worker 节点：`pgrep -af datasystem_worker`；`ss -tnlp` 看端口是否 LISTEN。进程无 → 编排/yuanrong-datasystem；在但未监听 → yuanrong-datasystem 支持。
2. Client 侧：`grep -E '\[(TCP|UDS|SHM_FD)_' $LOG/ds_client_<pid>.INFO.log | head` —— TCP/防火墙、UDS 路径权限、fd/SELinux 等归 **主机/网络**；`ConnectOptions was not configured` → **用户业务**。

判给华为时：Worker 与 Client 节点分别打 [证据包](#五证据包通用模板)，并附 Init 参数与编排 `describe`/`journalctl` 片段。

---

### 4.5 SHM 容量 / 内存不足 / 泄漏

**现象**：Put 返回 `6`；`resource.log` 中 `SHARED_MEMORY` 急涨；Worker RSS 异常。

**典型「钉住泄漏」特征（案例）**：`SHARED_MEMORY` 暴涨而 `OBJECT_COUNT` 反而下降、或对象大小已降但 `SHARED_MEMORY` 不降 → 元数据已删但物理 shm 仍被 ref 钉住 → **yuanrong-datasystem** 侧排查。

辅助 grep：

```bash
grep -E 'SHARED_MEMORY|OBJECT_COUNT|OBJECT_SIZE' $LOG/resource.log | tail -5
grep -E 'worker_shm_ref_table_bytes|worker_allocator_(alloc|free)_bytes_total|worker_object_count' \
  $LOG/datasystem_worker.INFO.log | tail -20
```

**边界**：dmesg OOM → 主机；容量顶满且对象数匹配 → 容量规划；`worker_shm_ref_table_bytes` 持续涨且 `worker_object_count` 持平 → yuanrong-datasystem。短期缓解慎用重启 Worker（丢数据风险）。

---

### 4.6 扩缩容 / Worker 升级 / 重启期间业务中断

**现象**：维护窗口内出现 31/32/23/短暂 1002；日志可能有 `meta_is_moving`、`Worker is exiting now`。

**要点**：维护期间偶发、SDK 重试后恢复 → 通常属**预期**；维护结束仍**持续**失败 → 查 Worker 是否拉起、etcd、再按 [4.1](#41-put--create--publish-失败) 前缀分流。

---

### 4.7 机器 / 节点级故障

**现象**：某节点 Worker 消失；节点 NotReady；大量 23/1002/心跳类日志。

**步骤**：先 `ping`/`ssh` 判基础设施；再节点上 `pgrep`、`dmesg`、`journalctl`。OOM 杀进程 → 主机；无 OOM 而非零退出或 crash → yuanrong-datasystem + 证据包。多节点同时故障 → 优先基础设施/网络/编排。

---

## 五、证据包通用模板

### 5.1 一键打包（示例脚本）

在故障节点将 `$log_dir` 换为实际路径后执行。脚本默认只收集**最近 2 小时**修改过的日志文件（避免收集过多压缩备份导致包过大）；如需完整历史日志请去掉 `-mmin -120` 条件。

```bash
#!/bin/bash
set -u
LOG=${log_dir:-/var/log/datasystem}
D=ds-evidence-$(hostname)-$(date +%Y%m%d-%H%M%S)
mkdir -p $D/logs $D/system

# 只取最近 2 小时修改的日志文件，避免压缩备份（.gz）过大
find $LOG -maxdepth 1 \( -name "*.log" -o -name "*.log.*" \) -mmin -120 -exec cp -a {} $D/logs/ \; 2>/dev/null

# 补充：最近 2 小时外的核心日志（如 worker.INFO，不含 .gz）
find $LOG -maxdepth 1 -name "datasystem_worker.INFO*" -mmin +120 ! -name "*.gz" -exec cp -a {} $D/logs/ \; 2>/dev/null

{
  echo "=== uname ===";  uname -a
  echo "=== uptime ==="; uptime
  echo "=== free  ==="; free -h
  echo "=== df    ==="; df -h
  echo "=== ulimit==="; ulimit -a
  echo "=== ps    ==="; pgrep -af datasystem_worker
  echo "=== ss    ==="; ss -tnlp
  echo "=== iptables ==="; iptables -L -n 2>/dev/null
  echo "=== tc    ==="; tc qdisc show 2>/dev/null
  echo "=== ub    ==="; ifconfig ub0 2>/dev/null; ubinfo 2>/dev/null
  echo "=== dmesg ==="; dmesg | tail -500
} > $D/system/env.txt

tar -czf $D.tar.gz $D && rm -rf $D
echo "证据包：$(pwd)/$D.tar.gz"
```

### 5.2 业务侧请同时提供

| 项 | 说明 |
|----|------|
| 故障时间窗 | 精确到分钟，带时区 |
| 调用概况 | 接口名、QPS、对象大小、batch |
| access log 错误码分布 | `awk -F'|' '{print $1}' ... \| sort \| uniq -c` |
| 对比基线 | 正常时段同指标 |
| SDK 版本与近期变更 | 升级、扩缩容、iptables/tc、限流等 |

---

## 六、FAQ（节选）

**Q：`Get` 的 `code=0` 也算失败吗？**  
A：会。`K_NOT_FOUND` 可能记为 0，须看 `respMsg`。

**Q：`K_SCALING(32)` / `K_SCALE_DOWN(31)` 算故障吗？**  
A：维护窗口内偶发、重试恢复多为正常；持续失败再按 [4.6](#46-扩缩容--worker-升级--重启期间业务中断) / [4.1](#41-put--create--publish-失败)。

**Q：`code=1002` 一定是华为问题吗？**  
A：不一定。必须结合 `[TCP|UDS|ZMQ|RPC|...]` 前缀与对端进程是否存活定界（与主手册一致）。

**Q：`etcd is timeout` 是 yuanrong-datasystem bug 吗？**  
A：不是。根因多在 etcd 集群或网络，由 **etcd 运维** 侧排查。

**Q：何时需要找华为并务必附证据包？**  
A：例如：定界仍落在 `K_RUNTIME_ERROR(5)`/`K_DATA_INCONSISTENCY(20)`；`RPC_RECV_TIMEOUT` + ZMQ fault 为 0 + 对端活 + 线程池堆积；SHM 钉住特征明显；Worker 异常退出且无 OOM；业务核对后仍长期数据不一致等。

---

## 附录 · 常用命令速查

```bash
LOG=${log_dir:-/var/log/datasystem}

# 错误码分布（替换 handle 名）
grep "DS_KV_CLIENT_GET"  $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_PUT"  $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c

# 结构化前缀
grep -E '\[(TCP|UDS|ZMQ|RPC|SHM_FD|URMA)_' \
  $LOG/datasystem_worker.INFO.log $LOG/ds_client_*.INFO.log | head -50

# Metrics delta（时延）
grep 'Compare with' $LOG/datasystem_worker.INFO.log | tail -3

grep -E 'etcd is (timeout|unavailable)' $LOG/*.INFO.log | tail
grep -E 'SHARED_MEMORY|ETCD_QUEUE|ETCD_REQUEST_SUCCESS_RATE|WAITING_TASK_NUM' $LOG/resource.log | tail

# 主机 / 网络
ulimit -a; free -h; df -h; dmesg | tail -200
iptables -L -n; tc qdisc show dev eth0 2>/dev/null
ping -c 5 <peer>; nc -zv <peer> <port>

# UB
ifconfig ub0 2>/dev/null; ubinfo 2>/dev/null

# etcd
etcdctl endpoint status -w table 2>/dev/null
etcdctl endpoint health 2>/dev/null

# yuanrong-datasystem 进程
pgrep -af datasystem_worker
```

---

> 若现场遇到本文未覆盖的场景，请按 [第五节](#五证据包通用模板) 打包提交，便于补充到后续版本。
