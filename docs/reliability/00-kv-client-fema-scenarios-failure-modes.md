# KV Client FEMA：业务场景与故障模式清单

> 与 [`KV_CLIENT_CLIENT_PERSPECTIVE_REPORTS.md`](../../plans/kv_client_triage/KV_CLIENT_CLIENT_PERSPECTIVE_REPORTS.md) 中的部署与 DryRun 表对照使用。入口总览：[00-kv-client-fema-index.md](00-kv-client-fema-index.md)。

---

## 业务架构与术语（DryRun 上下文）

典型调用链：

```text
负载均衡器 → 召回 / 精排 业务请求处理实例（集成 KVC SDK 或 client）→ KVC Worker（分布式缓存）
```

| 组件 | 职责 |
|------|------|
| **负载均衡器** | 选择业务请求处理实例（如 prefill、decode 路径）、负载均衡；可监控各实例上的**成功率**与**时延**。 |
| **业务请求处理实例** | 业务侧进程，集成 **KVC SDK**，发起 KV 读/写。 |
| **KVC Worker** | 核心缓存服务进程，承载元数据与数据面。 |

**业务类型与失败影响（对外指标口径）**

| 业务 | KV 读失败时的典型影响 |
|------|------------------------|
| **精排** | 往往导致 **请求 E2E 失败**（强依赖 KV 结果）。 |
| **召排** | 单次读失败不一定拖垮整请求，主要拉高**失败子路径时延**，体现为 **TP99** 等尾部指标劣化。 |

> 同一类基础设施故障（如 UB 端口）在精排与召排上的「现象」可能不同：精排更敏感于**成功率**；召排更敏感于**长尾时延**。DryRun 时需写明业务线。

---

## 业务流程

| 编号  | 场景                          |
| --- | --------------------------- |
| 1   | 业务实例部署，本地有 KVCache worker   |
| 2   | 业务实例部署，本地无 KVCache worker   |
| 3   | KVCache worker 实例部署         |
| 4   | 业务实例本节点 KVCache             |
| 5   | 业务实例本节点未命中，跨节点 worker 读数据   |
| 6   | 业务实例节点无 worker，跨节点读 KVCache |
| 7   | 业务实例扩容                      |
| 8   | 业务实例缩容                      |
| 9   | KVCache worker 实例扩容         |
| 10  | KVCache worker 实例缩容         |
| 11  | KVCache worker 故障，数据自动恢复    |

---

## 故障模式

| 编号  | 类别     | 故障模式            |
| --- | ------ | --------------- |
| 1   | 主机/OS  | OS 重启           |
| 2   | 主机/OS  | OS Panic        |
| 3   | 主机/OS  | BMC 强制上下电       |
| 4   | 主机资源   | 主机资源不足（Jetty）   |
| 5   | 主机资源   | 主机资源不足（UB 带宽）   |
| 6   | 主机资源   | 主机资源不足（CPU）     |
| 7   | 主机资源   | 主机资源不足（存储空间）    |
| 8   | 主机资源   | 主机资源不足（硬盘 IO 慢） |
| 9   | 主机资源   | 主机内存故障          |
| 10  | 时间     | 时间往前跳变          |
| 11  | 时间     | 时间往后跳变          |
| 12  | 容器     | Client 容器异常退出   |
| 13  | 容器     | Worker 容器异常退出   |
| 14  | 容器     | 容器资源不足（内存 / FD） |
| 15  | 容器     | 容器资源不足（CPU）     |
| 16  | 容器     | 容器资源不足（存储空间）    |
| 17  | 进程     | UBSE 进程故障       |
| 18  | 进程     | UBM 进程故障        |
| 19  | 进程     | Client 进程异常退出   |
| 20  | 进程     | Worker 进程异常退出   |
| 21  | 进程     | Client 进程反复重启   |
| 22  | 进程     | Worker 进程反复重启   |
| 23  | 进程     | Client 进程挂死     |
| 24  | 进程     | Worker 进程挂死     |
| 25  | UB 端口  | UB 端口 down      |
| 26  | UB 端口  | UB 端口闪断         |
| 27  | UB 端口  | UB 端口丢包         |
| 28  | UB 端口  | UB 端口降 lane     |
| 29  | UB 芯片  | UB 芯片 CE 故障     |
| 30  | UB 芯片  | UB 芯片 NFE 故障    |
| 31  | UB 芯片  | UB 芯片 FE 故障     |
| 32  | TCP 网卡 | TCP 网卡全部 down   |
| 33  | TCP 网卡 | TCP 单网卡 down    |
| 34  | TCP 网卡 | TCP 网卡时延        |
| 35  | TCP 网卡 | TCP 网卡丢包        |
| 36  | TCP 网卡 | TCP 网卡抖动        |
| 37  | TCP 网卡 | TCP 网卡闪断        |
| 38  | TCP 网卡 | TCP 网卡带宽不足      |
| 39  | UB 交换机 | UB 交换机端口故障      |
| 40  | UB 交换机 | UB 交换机端口闪断      |
| 41  | UB 交换机 | UB 交换机端口降 lane  |
| 42  | UB 交换机 | UB 交换机故障        |
| 43  | etcd   | etcd 集群不可用      |
| 44  | etcd   | ETCD 故障         |
| 45  | etcd   | ETCD 备节点故障      |
| 46  | etcd   | ETCD 主节点故障      |
| 47  | etcd   | ETCD 脑裂         |
| 48  | etcd   | ETCD 网络中断       |
| 49  | 分布式网盘  | 读写慢             |
| 50  | 分布式网盘  | 网络中断            |
| 51  | 分布式网盘  | 网络时延            |
| 52  | 分布式网盘  | 网络抖动            |
| 53  | 分布式网盘  | 网络丢包            |

**客户视角**对照表与主流程 T1～T6：[`KV_CLIENT_CUSTOMER_ALLINONE.md`](../../plans/kv_client_triage/KV_CLIENT_CUSTOMER_ALLINONE.md) **§1.1～1.4**。
