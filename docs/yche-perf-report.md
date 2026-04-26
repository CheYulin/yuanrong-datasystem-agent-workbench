### 性能 Breakdown 报告（自动生成）
- **metrics 来源**: `/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/docs/yche.log:2`
- **cycle** / **interval_ms** / **part**: 312 / 10000 / 1 of 1

#### 与手工分析对齐的 Metrics 树
> 说明：下列 **非严格可加树**；跨 worker 与 client 并行时，不能期望子项之和等于 `client_rpc_get_latency`。

##### Client → Worker（客户端 / 压测进程）

- *（本 snapshot 无此段指标）*

##### 入口 Worker 处理

- `worker_process_get_latency`：Get 在 worker 上处理（MsgQ 时常为排队+执行，**µs**）
  - `{"count":142923,"avg_us":37,"max_us":248}`

##### Worker ↔ Worker / Meta（跨节点与查 meta）

- `worker_rpc_query_meta_latency`：query meta 路径（**µs**）；常对应「W1→meta/下一跳」量级参考
  - `{"count":142923,"avg_us":524,"max_us":24050}`
- `worker_rpc_get_remote_object_latency`：跨 worker 拉对象（**µs**，旧名/日志名）
  - `{"count":142262,"avg_us":1398,"max_us":6141}`

##### 数据面（URMA）

- `worker_urma_write_latency`：**µs**
  - `{"count":142255,"avg_us":18,"max_us":126}`
- `worker_urma_wait_latency`：**µs**
  - `{"count":142255,"avg_us":1270,"max_us":6032}`
- `urma_import_jfr`：**µs**
  - `{"count":10,"avg_us":1380,"max_us":1982}`

#### 异常与健康检查

- （未触发其它启发式告警）

#### Perf Log 摘录（**ns**；ENABLE_PERF 编译且日志含 `[Perf Log]` 时才有数据）

| PerfKey | count | avgTime(ns) | maxTime(ns) | avg(µs) |
|---------|------:|------------:|------------:|--------:|
| `WORKER_PROCESS_GET_OBJECT` | 142923 | 2642758 | 26296740 | avg 2642.758 µs |
| `WORKER_PULL_REMOTE_DATA` | 142923 | 1897845 | 15165620 | avg 1897.845 µs |
| `WORKER_QUERY_META` | 142923 | 525047 | 24050240 | avg 525.047 µs |

#### 运维提示（可粘贴到报告尾部）

- Poll-thread / Rpc-thread：`UrmaEventHandler` 相关日志可与 `worker_urma_wait_latency` 对照。
- RPC 线程数 / 队列深度：结合 `worker_get_threadpool_queue_latency` 与线程池统计日志。
