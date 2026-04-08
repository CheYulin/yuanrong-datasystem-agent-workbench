# 流程叙事

每个主流程一篇短文：触发条件、前置条件、失败语义，并链接到具体代码路径或序列图。


| 文档                                                                                             | 说明                                       |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------- |
| [dsbench-install-deploy-run-observe.md](dsbench-install-deploy-run-observe.md)                 | dsbench 从安装产物到压测与观测的闭环                   |
| [remote-get-ub-urma-flow.md](remote-get-ub-urma-flow.md)                                       | Worker↔Worker Remote Get（UB/URMA）路径与文件定位 |
| [remote-get-tcp-fallback-urma-retry-polljfc.md](remote-get-tcp-fallback-urma-retry-polljfc.md) | TCP 回切、URMA 失败重试与 20ms 级时延               |
| [client-worker-master-retry-fault-handling.md](client-worker-master-retry-fault-handling.md)   | client→worker→master 重试与 Remote Get 故障语义 |


