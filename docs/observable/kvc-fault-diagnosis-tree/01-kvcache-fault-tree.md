# KVCache 定位定界故障模式树

> **目标**：以故障树形式组织 KVCache 故障模式，支持值班/客户按"现象 → 日志/命令 → 定界 → 根因 → 处置"链条定位问题。
>
> **五边界**：用户业务 / 数据系统进程内 / etcd 三方 / URMA 硬件 / OS-网络-内核
>
> **版本**：基于代码 `status.h` (v1.0+) 和 `kv_metrics.h` 最新定义

---

## 1 KVCache 中断异常

### 1.1 错误码 K_RUNTIME_ERROR (5)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'K_RUNTIME_ERROR|Get mmap entry failed|etcd is|urma' $LOG/datasystem_worker.INFO.log | tail -50`
        * 关键字：返回非空，含 K_RUNTIME_ERROR
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.1.1 Get mmap entry failed

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'Get mmap entry failed' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：OS，客户业务运维负责；内存锁定限制（mlock）
* 解决方法：`ulimit -l unlimited`；验证：`cat /proc/$(pgrep datasystem_worker)/limits | grep 'Max locked memory'`

### 1.1.2 etcd is timeout/unavailable

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'etcd is timeout|etcd is unavailable' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：etcd 三方，客户中台运维负责
* 解决方法：`etcdctl endpoint status -w table`；`systemctl status etcd`

### 1.1.3 urma ... payload ...

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'urma' $LOG/datasystem_worker.INFO.log | tail -50`
        * 关键字：返回非空
* 故障原因：URMA，分布式并行实验室负责
* 解决方法：检查 UB 链路状态

---

### 1.2 错误码 K_TRY_AGAIN (19)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'zmq_send_failure_total|zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：delta=0 表示对端处理慢；delta>0 表示网络丢包
* 故障原因：ZMQ failure delta=0 → 数据系统对端处理慢；ZMQ failure delta>0 → OS/网络丢包
* 解决方法：delta=0 检查对端负载；delta>0 检查网络

### 1.2.1 ZMQ failure delta=0（对端处理慢）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'WAITING_TASK_NUM' $LOG/resource.log`；`top -bn1 | head -20`
        * 关键字：WAITING_TASK_NUM 堆积
* 故障原因：数据系统，对端处理慢
* 解决方法：检查对端服务负载和线程池状态

### 1.2.2 ZMQ failure delta>0（网络丢包/断连）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_last_error_number' $LOG/datasystem_worker.INFO.log`
        * 关键字：errno 值
* 故障原因：OS/网络，按 errno 细分（ECONNREFUSED/ECONNRESET 等）
* 解决方法：按 errno 表细分排查

---

### 1.3 错误码 K_CLIENT_WORKER_DISCONNECT (23)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'Cannot receive heartbeat' $LOG/datasystem_worker.INFO.log`；`ssh <peer_ip> "pgrep -af datasystem_worker"`
        * 关键字：对端进程是否存在
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.3.1 对端进程不存在（Worker 崩溃）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'Worker is exiting' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：数据系统，Worker 崩溃
* 解决方法：检查 Worker 崩溃原因（OOM/dmesg/编排状态）

### 1.3.2 对端存活 + ping 不通

* 故障现象：
    * case1: 通过调用命令行识别
        * 执行命令行：`ping <peer_ip>`
        * 关键字：ping 不通
* 故障原因：OS/网络，防火墙/路由问题
* 解决方法：`iptables -L -n` 检查防火墙

### 1.3.3 对端存活 + ping 通

* 故障现象：
    * case1: 通过调用命令行识别
        * 执行命令行：`ping <peer_ip>`；`grep 'WAITING_TASK_NUM' $LOG/resource.log`
        * 关键字：ping 通但有延迟或对端负载高
* 故障原因：OS/网络，对端负载高/网络抖
* 解决方法：检查对端服务状态和网络质量

---

### 1.4 错误码 K_RPC_DEADLINE_EXCEEDED (1001)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'RPC_SERVICE_UNAVAILABLE' $LOG/datasystem_worker.INFO.log`；`ssh <peer_ip> "pgrep -af datasystem_worker"`
        * 关键字：对端拒绝或超时
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.4.1 含 [RPC_SERVICE_UNAVAILABLE]

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'RPC_SERVICE_UNAVAILABLE' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：数据系统，对端拒绝服务
* 解决方法：检查对端服务状态

### 1.4.2 delta=0 + 对端存活（对端处理慢）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'WAITING_TASK_NUM' $LOG/resource.log`
        * 关键字：队列堆积
* 故障原因：数据系统，对端处理慢
* 解决方法：检查对端线程池和服务负载

### 1.4.3 delta>0 或对端不在（网络问题）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_last_error_number' $LOG/datasystem_worker.INFO.log`
        * 关键字：errno 非零
* 故障原因：OS/网络，网络问题
* 解决方法：检查网络链路和设备状态

---

### 1.5 错误码 K_RPC_UNAVAILABLE (1002)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E '\[TCP_CONNECT_FAILED\]|\[TCP_CONNECT_RESET\]|zmq_event_handshake_failure_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：多种前缀（TCP/ZMQ/etcd/TLS 等）
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.5.1 日志含 etcd

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'etcd is' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：etcd 三方依赖
* 解决方法：`etcdctl endpoint status -w table`

### 1.5.2 日志含 TLS/handshake

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_event_handshake_failure_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：握手失败计数增长
* 故障原因：数据系统，证书问题
* 解决方法：检查 TLS 证书配置

### 1.5.3 日志含 TCP_CONNECT + 对端不在

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'Worker is exiting' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：数据系统，Worker 崩溃
* 解决方法：检查 Worker 崩溃原因

### 1.5.4 日志含 TCP_CONNECT + 对端在（防火墙/端口）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`ss -tnlp | grep <port>`；`iptables -L -n`
        * 关键字：端口被阻止
* 故障原因：OS/网络，防火墙/端口问题
* 解决方法：检查防火墙规则和端口状态

### 1.5.5 日志含 TCP_CONNECT_RESET

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`dmesg | tail -50`
        * 关键字：连接被重置
* 故障原因：OS/网络，网络闪断
* 解决方法：检查网络链路状态

### 1.5.6 日志含 UDS/SHM_FD

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E '\[UDS_CONNECT_FAILED\]|\[SHM_FD_TRANSFER_FAILED\]' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：OS，UDS/权限/fd 问题
* 解决方法：`ulimit -n`；检查 UDS 路径权限

### 1.5.7 ZMQ failure delta>0

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_last_error_number' $LOG/datasystem_worker.INFO.log`
        * 关键字：errno 非零
* 故障原因：OS/网络，按 errno 表细分
* 解决方法：按网络 errno 细分排查

### 1.5.8 ZMQ failure delta=0 + 对端在

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'WAITING_TASK_NUM' $LOG/resource.log`
        * 关键字：对端处理慢/拒绝
* 故障原因：数据系统，对端处理慢/拒绝
* 解决方法：检查对端服务负载

---

### 1.6 错误码 K_URMA_NEED_CONNECT (1006)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E '\[URMA_NEED_CONNECT\]|\[URMA_POLL_ERROR\]' $LOG/datasystem_worker.INFO.log`
        * 关键字：URMA 需要重连
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.6.1 remoteInstanceId 变化（对端 Worker 重启）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'URMA_NEED_CONNECT' $LOG/datasystem_worker.INFO.log`
        * 关键字：remoteInstanceId 变化
* 故障原因：数据系统，对端 Worker 重启（正常）
* 解决方法：SDK 自重连，无需干预

### 1.6.2 instanceId 不变 + 连接断开

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'URMA_NEED_CONNECT' $LOG/datasystem_worker.INFO.log`
        * 关键字：instanceId 不变但连接断
* 故障原因：URMA，连接断开需重建
* 解决方法：检查 UB 链路状态

### 1.6.3 instanceId 不变 + 持续出现 + [URMA_POLL_ERROR] 并存

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E 'URMA_NEED_CONNECT|URMA_POLL_ERROR' $LOG/datasystem_worker.INFO.log`；`ifconfig ub0`
        * 关键字：持续出现且伴 poll 错误
* 故障原因：URMA，UB 链路不稳
* 解决方法：检查 UB 端口和驱动状态

---

### 1.7 错误码 K_URMA_TRY_AGAIN (1008)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep -E '\[URMA_RECREATE_JETTY\]|\[URMA_RECREATE_JETTY_FAILED\]|\[URMA_RECREATE_JETTY_SKIP\]' $LOG/datasystem_worker.INFO.log`
        * 关键字：JETTY 重建相关日志（代码中为 JETTY，非 JFS）
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 1.7.1 [URMA_RECREATE_JETTY] + cqeStatus=9 (ACK TIMEOUT)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'URMA_RECREATE_JETTY' $LOG/datasystem_worker.INFO.log`
        * 关键字：cqeStatus=9
* 故障原因：自动重建中（继续观察是否有 FAILED）
* 解决方法：观察是否出现 FAILED

### 1.7.2 [URMA_RECREATE_JETTY_FAILED] 连续

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'URMA_RECREATE_JETTY_FAILED' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：URMA，JETTY 重建失败
* 解决方法：联系 URMA 团队

### 1.7.3 [URMA_RECREATE_JETTY_SKIP]

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'URMA_RECREATE_JETTY_SKIP' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
* 故障原因：连接过期跳过，正常
* 解决方法：无需处置

---

### 1.8 错误码 K_MASTER_TIMEOUT (25)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'etcd is' $LOG/datasystem_worker.INFO.log`
        * 关键字：etcd 相关错误
* 故障原因：etcd，Master 超时
* 解决方法：`etcdctl endpoint status -w table`；`systemctl status etcd`

---

### 1.9 错误码 K_SERVER_FD_CLOSED (29)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：access log 错误码
        * 关键字：返回时转为 K_TRY_AGAIN
* 故障原因：数据系统，Worker 退出
* 解决方法：检查 Worker 状态

---

### 1.10 错误码 K_SCALE_DOWN (31)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：access log 错误码
        * 关键字：K_SCALE_DOWN
* 故障原因：数据系统，缩容中
* 解决方法：SDK 自重试

---

### 1.11 错误码 K_WORKER_ABNORMAL (22)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：access log；`grep 'Worker is exiting' $LOG/datasystem_worker.INFO.log`
        * 关键字：K_WORKER_ABNORMAL
* 故障原因：数据系统，Worker 异常
* 解决方法：检查 Worker 状态和崩溃日志

---

### 1.12 错误码 K_SHUTTING_DOWN (21)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：access log
        * 关键字：K_SHUTTING_DOWN
* 故障原因：数据系统，服务正在关闭
* 解决方法：等待服务重启或 SDK 自重试

---

### 1.13 错误码 K_RECOVERY_ERROR (15) / K_RECOVERY_IN_PROGRESS (16)

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：access log；`grep 'etcd is' $LOG/datasystem_worker.INFO.log`
        * 关键字：K_RECOVERY_ERROR / K_RECOVERY_IN_PROGRESS
* 故障原因：数据系统，恢复中/恢复错误
* 解决方法：等待恢复完成或检查 etcd 状态

---

## 2 KVCache 性能异常（P99 延迟升高）

### 2.1 URMA 降级检查

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'fallback to TCP/IP payload' $LOG/datasystem_worker.INFO.log`
        * 关键字：频繁出现（>10次/分钟）
    * case2: 通过命令行识别
        * 执行命令行：`grep 'urma.*total_bytes\|tcp.*total_bytes' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：urma 字节 delta=0，tcp 字节 delta>0
* 故障原因：URMA 降级到 TCP
* 解决方法：联系 URMA 和底软团队

### 2.1.1 URMA 正常

* 故障现象：
    * case1: 通过命令行识别
        * 执行命令行：`grep 'urma.*total_bytes' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：delta>0
* 故障原因：URMA 正常
* 解决方法：继续步骤 4（规格检查）

---

### 2.2 规格/流量超标检查

* 故障现象：
    * case1: 通过命令行识别
        * 执行命令行：`grep 'WAITING_TASK_NUM\|MAX_THREAD_NUM' $LOG/resource.log | tail -3`；`grep 'ACTIVE_CLIENT_COUNT' $LOG/resource.log | tail -3`
        * 关键字：WAITING_TASK_NUM 接近 MAX_THREAD_NUM；ACTIVE_CLIENT_COUNT 超过上限
* 故障原因：客户业务侧，线程池打满/并发超标
* 解决方法：扩容或降业务量

### 2.2.1 数据系统问题（线程池堆积但未达上限）

* 故障现象：
    * case1: 通过命令行识别
        * 执行命令行：`grep 'WAITING_TASK_NUM' $LOG/resource.log | tail -3`
        * 关键字：堆积但未达上限
* 故障原因：数据系统问题
* 解决方法：继续分段定位

---

### 2.3 数据系统内部性能分段定位

#### 2.3.1 ① Client SDK 段

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'client_rpc_get_latency\|client_rpc_publish_latency\|client_rpc_create_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：max 高
* 故障原因：客户业务侧，业务代码慢/prefetcher 配置问题
* 解决方法：检查业务代码和 prefetcher 配置

#### 2.3.2 ② Client→Worker 段（ZMQ RPC）

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'zmq_client_queuing_latency\|zmq_server_queue_wait_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：max 高
* 故障原因：KVC 或客户运维侧（需自证清白）
* 解决方法：使用附录 B 公式自证清白

#### 2.3.3 ③ 元数据访问段（Worker↔Worker）

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'worker_rpc_create_meta_latency\|worker_rpc_query_meta_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：max 高
* 故障原因：KVC，元数据服务慢/锁冲突
* 解决方法：检查元数据服务和锁竞争

#### 2.3.4 ④ 数据访问段（Worker↔Worker，URMA/TCP）

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'worker_urma_write_latency\|worker_urma_wait_latency\|worker_tcp_write_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：max 高
* 故障原因：URMA 或客户运维侧（需自证清白）
* 解决方法：自证清白后定位

---

### 2.4 自证清白（ZMQ RPC）

* 故障现象：
    * case1: 通过指标计算
        * 日志入口：`grep 'zmq_rpc_e2e_latency\|zmq_rpc_network_latency\|zmq_server_exec_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：network_latency = e2e - exec
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 2.4.1 zmq_server_exec_latency 高

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'zmq_server_exec_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：值高
* 故障原因：Server 业务慢
* 解决方法：优化业务代码

### 2.4.2 zmq_client_queuing_latency 高

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'zmq_client_queuing_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：值高
* 故障原因：Client 端框架慢
* 解决方法：检查 prefetcher 配置

### 2.4.3 zmq_server_queue_wait_latency 高

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'zmq_server_queue_wait_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：值高
* 故障原因：Server 队列堆积
* 解决方法：检查 Server 处理能力

### 2.4.4 zmq_rpc_network_latency 高 + 框架正常

* 故障现象：
    * case1: 通过指标识别
        * 日志入口：`grep 'zmq_rpc_network_latency' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：值高但框架正常
* 故障原因：客户运维侧，网络本身慢
* 解决方法：检查网络设备和链路

---

### 2.5 ZMQ/OS 网络故障检查

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_send_failure_total\|zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：delta>0
    * case2: 通过命令行识别
        * 执行命令行：`ping -c 100 <peer_ip>`；`tc qdisc show dev eth0`
        * 关键字：ping 抖动/tc 有残留
* 故障原因：向下级匹配
* 解决方法：向下级匹配

### 2.5.1 ZMQ 发送/接收失败

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_send_failure_total\|zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：delta>0
* 故障原因：客户运维侧，网络/防火墙问题
* 解决方法：检查网络和防火墙

### 2.5.2 背压（非故障）

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'zmq_send_try_again_total' $LOG/datasystem_worker.INFO.log | tail -3`
        * 关键字：delta>0 但 failure=0
* 故障原因：正常现象
* 解决方法：无需处置

### 2.5.3 网络抖动

* 故障现象：
    * case1: 通过命令行识别
        * 执行命令行：`ping -c 100 <peer_ip>`
        * 关键字：抖动
* 故障原因：客户运维侧，网络设备问题
* 解决方法：检查网络设备

### 2.5.4 tc qdisc 有残留

* 故障现象：
    * case1: 通过命令行识别
        * 执行命令行：`tc qdisc show dev eth0`
        * 关键字：有 netem 配置残留
* 故障原因：客户运维侧，网络配置问题
* 解决方法：清理 tc 配置

---

### 2.6 用户/SDK 本地问题

* 故障现象：
    * case1: 通过计算识别
        * 执行命令行：`grep 'DS_KV_CLIENT_GET' $LOG/ds_client_access_*.log | awk -F'|' '{print $3}' | sort -n | awk 'END{print "P99="$1}'`
        * 关键字：单请求 P99 高但系统指标正常
* 故障原因：客户业务侧，业务代码问题
* 解决方法：检查业务代码

---

## 3 用户业务边界故障

### 3.1 K_INVALID (2)：参数非法

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：SDK 返回值；`grep '^2|' $LOG/ds_client_access_*.log`
        * 关键字：respMsg 含 "empty"/"should be bigger than zero"/"length not match"
* 故障原因：用户业务，参数非法
* 解决方法：修正业务代码参数

### 3.2 K_NOT_FOUND (3)：对象不存在

* 故障现象：
    * case1: 通过错误码和响应消息识别
        * 日志入口：SDK 返回值；access log respMsg
        * 关键字：rc.GetCode()==K_NOT_FOUND 或 respMsg 含 "NOT_FOUND"/"Can't find object"
    * case2: 陷阱：access log 可能 code=0
        * 说明：Get 查不到时 access log 可能记为 code=0，需看 respMsg
* 故障原因：用户业务，对象不存在或 key 不匹配
* 解决方法：核对业务 Put/Get 顺序、key、TTL

### 3.3 K_NOT_READY (8)：未 Init

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：SDK 返回值；`grep 'ConnectOptions was not configured' $LOG/ds_client_*.INFO.log`
        * 关键字：K_NOT_READY(8)
* 故障原因：用户业务，未调用 Init
* 解决方法：业务代码添加 Init 调用

### 3.4 K_DUPLICATED (1)：重复操作

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：SDK 返回值；access log
        * 关键字：K_DUPLICATED(1)
* 故障原因：用户业务，重复创建已存在的对象
* 解决方法：检查业务逻辑是否预期重复操作

### 3.5 K_NOT_AUTHORIZED (9)：未授权

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：SDK 返回值；access log
        * 关键字：K_NOT_AUTHORIZED(9)
* 故障原因：用户业务，权限不足
* 解决方法：检查 ACL 配置和认证信息

### 3.6 K_NOT_SUPPORTED (36)：不支持的操作

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：SDK 返回值；access log
        * 关键字：K_NOT_SUPPORTED(36)
* 故障原因：用户业务，调用了不支持的操作
* 解决方法：检查 SDK 版本和操作类型

---

## 4 资源/容量故障

### 4.1 K_OUT_OF_MEMORY (6)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：`grep 'K_OUT_OF_MEMORY' $LOG/ds_client_access_*.log`
        * 关键字：code=6
    * case2: 通过系统命令
        * 执行命令行：`dmesg | grep -i oom`
        * 关键字：OOM killer 记录
* 故障原因：OS，内存不足
* 解决方法：`free -h`；检查内存使用；扩容或优化业务

### 4.2 K_NO_SPACE (13)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：`grep 'K_NO_SPACE' $LOG/ds_client_access_*.log`
        * 关键字：code=13
    * case2: 通过系统命令
        * 执行命令行：`df -h`
        * 关键字：磁盘空间满
* 故障原因：OS，磁盘空间不足
* 解决方法：清理磁盘或扩容

### 4.3 K_FILE_LIMIT_REACHED (18)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：`grep 'K_FILE_LIMIT_REACHED' $LOG/ds_client_access_*.log`
        * 关键字：code=18
    * case2: 通过系统命令
        * 执行命令行：`ls /proc/<pid>/fd | wc -l`；`ulimit -n`
        * 关键字：fd 数接近上限
* 故障原因：OS，fd 耗尽
* 解决方法：`ulimit -n` 调整；优化 fd 泄漏

### 4.4 K_IO_ERROR (7)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：`grep 'K_IO_ERROR' $LOG/ds_client_access_*.log`
        * 关键字：code=7
    * case2: 通过系统命令
        * 执行命令行：`dmesg`；磁盘 smartctl 检查
        * 关键字：IO error
* 故障原因：OS，IO 错误
* 解决方法：检查磁盘健康状态

### 4.5 K_LRU_HARD_LIMIT (34) / K_LRU_SOFT_LIMIT (35)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：access log
        * 关键字：K_LRU_HARD_LIMIT(34) / K_LRU_SOFT_LIMIT(35)
    * case2: 通过 resource.log
        * 执行命令行：`grep 'SHARED_MEMORY' $LOG/resource.log`
        * 关键字：内存使用率达到限制
* 故障原因：OS/数据系统，LRU 缓存达到限制
* 解决方法：扩容内存或清理缓存

---

## 5 扩缩容/维护期间故障

### 5.1 K_SCALE_DOWN (31) / K_SCALING (32)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：access log
        * 关键字：维护期间偶发
* 故障原因：数据系统，缩容中/维护窗口
* 解决方法：SDK 自重试；等待维护结束

### 5.2 Worker 优雅退出

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'HealthCheck.*Worker is exiting' $LOG/datasystem_worker.INFO.log`
        * 关键字：返回非空
    * case2: 通过错误码
        * 日志入口：access log K_SCALE_DOWN
        * 关键字：31
* 故障原因：数据系统，正常维护流程
* 解决方法：SDK 自重连；等待编排拉起新 Worker

### 5.3 心跳超时未恢复

* 故障现象：
    * case1: 通过日志关键字识别
        * 日志入口：`grep 'Cannot receive heartbeat' $LOG/datasystem_worker.INFO.log`
        * 关键字：持续出现
* 故障原因：数据系统，心跳超时未自恢复
* 解决方法：检查 Worker 进程状态；联系支持

### 5.4 K_CLIENT_WORKER_VERSION_MISMATCH (28)

* 故障现象：
    * case1: 通过错误码识别
        * 日志入口：access log；`grep 'version mismatch' $LOG/datasystem_worker.INFO.log`
        * 关键字：K_CLIENT_WORKER_VERSION_MISMATCH(28)
* 故障原因：数据系统，Client/Worker 版本不匹配
* 解决方法：升级/降级 SDK 或 Worker 到兼容版本

---

## 附录 A：日志关键字速查

### A.1 OS/ZMQ 日志关键字

| 日志关键字 | 错误消息 | errno/ret | 责任方 |
|-----------|----------|-----------|--------|
| `[TCP_CONNECT_FAILED]` | TCP connect failed | errno | OS/网络 |
| `[TCP_CONNECT_RESET]` | Connect reset | errno | OS/网络 |
| `[TCP_NETWORK_UNREACHABLE]` | Network unreachable | - | OS/网络 |
| `[UDS_CONNECT_FAILED]` | UDS connect failed | - | OS/网络 |
| `[SHM_FD_TRANSFER_FAILED]` | SHM fd transfer failed | - | OS |
| `[ZMQ_SEND_FAILURE_TOTAL]` | ZMQ send failed | errno | OS/网络 |
| `[ZMQ_RECEIVE_FAILURE_TOTAL]` | ZMQ recv failed | errno | OS/网络 |
| `[ZMQ_RECV_TIMEOUT]` | ZMQ recv timeout | - | 数据系统 |
| `[RPC_RECV_TIMEOUT]` | RPC recv timeout | - | 数据系统/OS |
| `[RPC_SERVICE_UNAVAILABLE]` | Service unavailable | - | 数据系统 |
| `[SOCK_CONN_WAIT_TIMEOUT]` | Sock conn wait timeout | - | OS/网络 |
| `zmq_event_handshake_failure_total`↑ | TLS handshake failed | - | 数据系统 |

### A.2 URMA 日志关键字

| 日志关键字 | 错误消息 | errno/ret | 责任方 |
|-----------|----------|-----------|--------|
| `[URMA_NEED_CONNECT]` | Urma needs to reconnet | remoteInstanceId/instanceId | 数据系统/URMA |
| `[URMA_POLL_ERROR]` | PollJfcWait failed | ret | URMA |
| `[URMA_WAIT_TIMEOUT]` | timedout waiting for request | requestId | 数据系统 |
| `[URMA_RECREATE_JETTY]` | JETTY recreating | cqeStatus | URMA |
| `[URMA_RECREATE_JETTY_FAILED]` | JETTY recreate failed | ret | URMA |
| `[URMA_RECREATE_JETTY_SKIP]` | JETTY skip (connection expired) | - | 正常 |
| `[URMA_AE]` | URMA async event | - | URMA |
| `[URMA_AE_JETTY_ERR]` | URMA async event jetty error | - | URMA |
| `[URMA_AE_JFC_ERR]` | URMA async event JFC error | - | URMA |
| `[URMA_PERF]` | URMA performance log | - | URMA |
| `fallback to TCP/IP payload` | UB 降级 TCP | - | URMA |

---

## 附录 B：ZMQ failure 判断（核心定界）

**查看**：
```bash
grep 'zmq_send_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
grep 'zmq_receive_failure_total' $LOG/datasystem_worker.INFO.log | tail -3
```

**代码逻辑**（`zmq_socket_ref.cpp`）：
```
ZMQ send/recv 返回-1时：
  errno == EAGAIN  → K_TRY_AGAIN（背压，非故障）
                     → zmq_send/receive_try_again_total++
  errno == EINTR   → K_INTERRUPTED
  其他 errno       → K_RPC_UNAVAILABLE
                     → zmq_send/receive_failure_total++
                     → zmq_last_error_number = errno
                     → 网络errno → zmq_network_error_total++
```

**网络类 errno**（`IsZmqSocketNetworkErrno`）：
ECONNREFUSED / ECONNRESET / ECONNABORTED / EHOSTUNREACH / ENETUNREACH / ENETDOWN / ETIMEDOUT / EPIPE / ENOTCONN

| 判断 | 结论 |
|------|------|
| delta=0 | 无 ZMQ 层 I/O 失败 → 对端慢/数据系统 |
| delta>0 | 有 ZMQ 层 I/O 失败 → 网络/OS |

---

## 附录 C：快速定界表

### C.1 第一步：抓错误码分布

```bash
grep "DS_KV_CLIENT_PUT" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
grep "DS_KV_CLIENT_GET" $LOG/ds_client_access_*.log | awk -F'|' '{print $1}' | sort | uniq -c
```

### C.2 第二步：直接出结论

| 错误码 | 枚举 | 错误消息 | 责任方 | 证据/日志 |
|--------|------|----------|--------|-----------|
| 0 | K_OK | OK | 用户 | respMsg 含 "NOT_FOUND" |
| 1 | K_DUPLICATED | Duplicated operation | 用户 | 重复操作 |
| 2 | K_INVALID | Invalid parameter | 用户 | 空 key/大小为0 |
| 3 | K_NOT_FOUND | Key not found | 用户 | Get 时 |
| 5 | K_RUNTIME_ERROR | Runtime error | 见子错误 | mmap/etcd/urma |
| 6 | K_OUT_OF_MEMORY | Out of memory | OS | `dmesg\|grep -i oom` |
| 7 | K_IO_ERROR | IO error | OS | `dmesg`/磁盘 smart |
| 8 | K_NOT_READY | Not ready | 用户 | 未 Init |
| 9 | K_NOT_AUTHORIZED | Not authorized | 用户 | 权限问题 |
| 13 | K_NO_SPACE | No space available | OS | `df -h` |
| 14 | K_NOT_LEADER_MASTER | Not leader master | 数据系统 | Master 角色 |
| 15 | K_RECOVERY_ERROR | Recovery error | 数据系统 | 检查恢复状态 |
| 16 | K_RECOVERY_IN_PROGRESS | Recovery in progress | 数据系统 | 等待恢复完成 |
| 18 | K_FILE_LIMIT_REACHED | Limit on file descriptors reached | OS | `ulimit -n` |
| 19 | K_TRY_AGAIN | Try again | 见子错误 | delta=0对端慢/delta>0网络 |
| 20 | K_DATA_INCONSISTENCY | Data inconsistency | 数据系统 | 数据不一致 |
| 21 | K_SHUTTING_DOWN | Shutting down | 数据系统 | 服务关闭中 |
| 22 | K_WORKER_ABNORMAL | Worker abnormal | 数据系统 | Worker 异常 |
| 23 | K_CLIENT_WORKER_DISCONNECT | Client worker disconnect | 见子错误 | 心跳超时 |
| 24 | K_WORKER_TIMEOUT | Worker timeout | 数据系统 | Worker 超时 |
| 25 | K_MASTER_TIMEOUT | The master may timeout/dead | etcd | `etcdctl endpoint status` |
| 28 | K_CLIENT_WORKER_VERSION_MISMATCH | Version mismatch | 数据系统 | 版本不匹配 |
| 29 | K_SERVER_FD_CLOSED | The server fd has been closed | 数据系统 | 返回时转为 K_TRY_AGAIN |
| 31 | K_SCALE_DOWN | The worker is exiting | 数据系统 | SDK 自重试 |
| 32 | K_SCALING | Scaling in progress | 数据系统 | 扩缩容中 |
| 33 | K_CLIENT_DEADLOCK | Client deadlock | 数据系统 | 死锁检测 |
| 34 | K_LRU_HARD_LIMIT | LRU hard limit reached | OS/数据系统 | 内存限制 |
| 35 | K_LRU_SOFT_LIMIT | LRU soft limit reached | OS/数据系统 | 内存限制 |
| 36 | K_NOT_SUPPORTED | Not supported | 用户 | 不支持的操作 |
| 1001 | K_RPC_DEADLINE_EXCEEDED | RPC deadline exceeded | 见子错误 | 超时 |
| 1002 | K_RPC_UNAVAILABLE | RPC unavailable | 见子错误 | TCP/ZMQ/etcd |
| 1004 | K_URMA_ERROR | Urma operation failed | URMA | `dmesg\|grep ub`/`ibstat` |
| 1006 | K_URMA_NEED_CONNECT | Urma needs to reconnet | URMA | SDK 自重连 |
| 1008 | K_URMA_TRY_AGAIN | Urma operation failed, try again | URMA | JETTY 重建 |
| 1009 | K_URMA_CONNECT_FAILED | Urma connect failed | URMA | `ifconfig ub0` |
| 1010 | K_URMA_WAIT_TIMEOUT | Urma wait for completion timed out | 数据系统 | 无需处置 |

---

## 附录 D：Metrics 完整参考

### D.1 Client Metrics (ID 0-10, 60-61)

| ID | Metric Name | Type | Unit | 说明 |
|----|-------------|------|------|------|
| 0 | `client_put_request_total` | COUNTER | count | PUT 请求总数 |
| 1 | `client_put_error_total` | COUNTER | count | PUT 错误总数 |
| 2 | `client_get_request_total` | COUNTER | count | GET 请求总数 |
| 3 | `client_get_error_total` | COUNTER | count | GET 错误总数 |
| 4 | `client_rpc_create_latency` | HISTOGRAM | us | Create RPC 延迟 |
| 5 | `client_rpc_publish_latency` | HISTOGRAM | us | Publish RPC 延迟 |
| 6 | `client_rpc_get_latency` | HISTOGRAM | us | Get RPC 延迟 |
| 7 | `client_put_urma_write_total_bytes` | COUNTER | bytes | PUT URMA 写入字节 |
| 8 | `client_put_tcp_write_total_bytes` | COUNTER | bytes | PUT TCP 写入字节 |
| 9 | `client_get_urma_read_total_bytes` | COUNTER | bytes | GET URMA 读取字节 |
| 10 | `client_get_tcp_read_total_bytes` | COUNTER | bytes | GET TCP 读取字节 |
| 60 | `client_async_release_queue_size` | GAUGE | count | 异步释放队列大小 |
| 61 | `client_dec_ref_skipped_total` | COUNTER | count | DecRef 跳过次数 |

### D.2 Worker Metrics (ID 11-23, 44-53, 65-69)

| ID | Metric Name | Type | Unit | 说明 |
|----|-------------|------|------|------|
| 11 | `worker_rpc_create_meta_latency` | HISTOGRAM | us | 创建元数据延迟 |
| 12 | `worker_rpc_query_meta_latency` | HISTOGRAM | us | 查询元数据延迟 |
| 13 | `worker_rpc_remote_get_outbound_latency` | HISTOGRAM | us | 远程获取出站延迟 |
| 14 | `worker_process_create_latency` | HISTOGRAM | us | 处理 Create 延迟 |
| 15 | `worker_process_publish_latency` | HISTOGRAM | us | 处理 Publish 延迟 |
| 16 | `worker_process_get_latency` | HISTOGRAM | us | 处理 Get 延迟 |
| 17 | `worker_urma_write_latency` | HISTOGRAM | us | URMA 写入延迟 |
| 18 | `worker_urma_wait_latency` | HISTOGRAM | us | URMA 等待延迟 |
| 19 | `worker_tcp_write_latency` | HISTOGRAM | us | TCP 写入延迟 |
| 20 | `worker_to_client_total_bytes` | COUNTER | bytes | Worker 到客户端字节 |
| 21 | `worker_from_client_total_bytes` | COUNTER | bytes | 客户端到 Worker 字节 |
| 22 | `worker_object_count` | GAUGE | count | 对象数量 |
| 23 | `worker_allocated_memory_size` | GAUGE | bytes | 已分配内存大小 |
| 44 | `worker_allocator_alloc_bytes_total` | COUNTER | bytes | 分配字节总数 |
| 45 | `worker_allocator_free_bytes_total` | COUNTER | bytes | 释放字节总数 |
| 46 | `worker_shm_unit_created_total` | COUNTER | count | SHM 单元创建数 |
| 47 | `worker_shm_unit_destroyed_total` | COUNTER | count | SHM 单元销毁数 |
| 48 | `worker_shm_ref_add_total` | COUNTER | count | SHM 引用添加数 |
| 49 | `worker_shm_ref_remove_total` | COUNTER | count | SHM 引用移除数 |
| 50 | `worker_shm_ref_table_size` | GAUGE | count | SHM 引用表大小 |
| 51 | `worker_shm_ref_table_bytes` | GAUGE | bytes | SHM 引用表字节 |
| 52 | `worker_remove_client_refs_total` | COUNTER | count | 移除客户端引用数 |
| 53 | `worker_object_erase_total` | COUNTER | count | 对象删除数 |
| 65 | `worker_rpc_remote_get_inbound_latency` | HISTOGRAM | us | 远程获取入站延迟 |
| 66 | `worker_get_threadpool_queue_latency` | HISTOGRAM | us | Get 线程池队列延迟 |
| 67 | `worker_get_threadpool_exec_latency` | HISTOGRAM | us | Get 线程池执行延迟 |
| 68 | `worker_get_meta_addr_hashring_latency` | HISTOGRAM | us | Get 元数据地址哈希环延迟 |
| 69 | `worker_get_post_query_meta_phase_latency` | HISTOGRAM | us | Get 查询元数据阶段后延迟 |

### D.3 ZMQ Metrics (ID 24-43)

| ID | Metric Name | Type | Unit | 说明 |
|----|-------------|------|------|------|
| 24 | `zmq_send_failure_total` | COUNTER | count | ZMQ 发送失败总数 |
| 25 | `zmq_receive_failure_total` | COUNTER | count | ZMQ 接收失败总数 |
| 26 | `zmq_send_try_again_total` | COUNTER | count | ZMQ 发送重试总数 |
| 27 | `zmq_receive_try_again_total` | COUNTER | count | ZMQ 接收重试总数 |
| 28 | `zmq_network_error_total` | COUNTER | count | ZMQ 网络错误总数 |
| 29 | `zmq_last_error_number` | GAUGE | - | 上一次错误号 |
| 30 | `zmq_gateway_recreate_total` | COUNTER | count | Gateway 重创总数 |
| 31 | `zmq_event_disconnect_total` | COUNTER | count | 事件断开连接总数 |
| 32 | `zmq_event_handshake_failure_total` | COUNTER | count | 事件握手失败总数 |
| 33 | `zmq_send_io_latency` | HISTOGRAM | us | ZMQ 发送 I/O 延迟 |
| 34 | `zmq_receive_io_latency` | HISTOGRAM | us | ZMQ 接收 I/O 延迟 |
| 35 | `zmq_rpc_serialize_latency` | HISTOGRAM | us | RPC 序列化延迟 |
| 36 | `zmq_rpc_deserialize_latency` | HISTOGRAM | us | RPC 反序列化延迟 |
| 37 | `zmq_client_queuing_latency` | HISTOGRAM | ns | 客户端队列延迟 |
| 38 | `zmq_client_stub_send_latency` | HISTOGRAM | ns | 客户端 Stub 发送延迟 |
| 39 | `zmq_server_queue_wait_latency` | HISTOGRAM | ns | 服务端队列等待延迟 |
| 40 | `zmq_server_exec_latency` | HISTOGRAM | ns | 服务端执行延迟 |
| 41 | `zmq_server_reply_latency` | HISTOGRAM | ns | 服务端回复延迟 |
| 42 | `zmq_rpc_e2e_latency` | HISTOGRAM | ns | RPC 端到端延迟 |
| 43 | `zmq_rpc_network_latency` | HISTOGRAM | ns | RPC 网络延迟 |

### D.4 URMA Metrics (ID 62-64)

| ID | Metric Name | Type | Unit | 说明 |
|----|-------------|------|------|------|
| 62 | `urma_import_jfr` | HISTOGRAM | us | URMA import JFR 延迟 |
| 63 | `urma_inflight_wr_count` | HISTOGRAM | count | URMA 飞行写请求数 |
| 64 | `urma_nanosleep_latency` | HISTOGRAM | us | URMA nanosleep 延迟 |

### D.5 Master Metrics (ID 54-59)

| ID | Metric Name | Type | Unit | 说明 |
|----|-------------|------|------|------|
| 54 | `master_object_meta_table_size` | GAUGE | count | Master 元数据表大小 |
| 55 | `master_ttl_pending_size` | GAUGE | count | Master TTL 等待大小 |
| 56 | `master_ttl_fire_total` | COUNTER | count | Master TTL 触发总数 |
| 57 | `master_ttl_delete_success_total` | COUNTER | count | Master TTL 删除成功总数 |
| 58 | `master_ttl_delete_failed_total` | COUNTER | count | Master TTL 删除失败总数 |
| 59 | `master_ttl_retry_total` | COUNTER | count | Master TTL 重试总数 |

---

## 附录 E：日志位置

| 类型 | 路径 |
|------|------|
| 接口错误码 | `$LOG/ds_client_access_*.log` |
| ZMQ 指标/错误 | `$LOG/datasystem_worker.INFO.log` |
| URMA 日志 | `$LOG/*.INFO.log` 含`[URMA_]` |
| 资源监控 | `$LOG/resource.log` |
| Server 延迟 | `$LOG/datasystem_worker.INFO.log` 含`[SERVER_LATENCY]` |
| RPC 延迟 | `$LOG/datasystem_worker.INFO.log` 含`[RPC_LATENCY]` |

---

## 附录 F：资源监控 (resource.log) 字段

| 字段组 | 子字段 |
|--------|--------|
| `SHARED_MEMORY` | MEMORY_USAGE, PHYSICAL_MEMORY_USAGE, TOTAL_LIMIT, WORKER_SHARE_MEMORY_USAGE, SC_MEMORY_USAGE, SC_MEMORY_LIMIT |
| `SPILL_HARD_DISK` | SPACE_USAGE, PHYSICAL_SPACE_USAGE, TOTAL_LIMIT, WORKER_SPILL_HARD_DISK_USAGE |
| `ACTIVE_CLIENT_COUNT` | ACTIVE_CLIENT_COUNT |
| `OBJECT_COUNT` | OBJECT_COUNT |
| `OBJECT_SIZE` | OBJECT_SIZE |
| `WORKER_OC_SERVICE_THREAD_POOL` | IDLE_NUM, CURRENT_TOTAL_NUM, MAX_THREAD_NUM, WAITING_TASK_NUM, THREAD_POOL_USAGE |
| `ETCD_QUEUE` | CURRENT_SIZE, TOTAL_LIMIT, ETCD_QUEUE_USAGE |
| `ETCD_REQUEST_SUCCESS_RATE` | SUCCESS_RATE |
| `OBS_REQUEST_SUCCESS_RATE` | SUCCESS_RATE |
| `STREAM_COUNT` | STREAM_COUNT |
| `STREAM_REMOTE_SEND_SUCCESS_RATE` | SUCCESS_RATE |
| `SHARED_DISK` | USAGE, PHYSICAL_USAGE, TOTAL_LIMIT, USAGE_RATE |
| `SC_LOCAL_CACHE` | USAGE, RESERVED_USAGE, TOTAL_LIMIT, USAGE_RATE |
| `OC_HIT_NUM` | MEM_HIT_NUM, DISK_HIT_NUM, L2_HIT_NUM, REMOTE_HIT_NUM, MISS_NUM |
