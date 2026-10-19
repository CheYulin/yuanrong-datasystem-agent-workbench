# 统一 TCP 实验交付核对

Status: In-Progress；此文记录用户需求与实际证据位置，不替代原始日志或独立验证器。

## 1. 已完成的准备与预检

| 用户要求 | 当前证据与边界 |
|---|---|
| 发布源码构建 | build.md 与 formal-tcp-001/builds：复用已实际完成的三个固定SHA构建，新部署重新核对8个原生制品；本阶段没有重新编译 |
| 同节点双存储服务 | tcp-plan.md 与 products/config：DataSystem双KVC Worker+ETCD，Mooncake双4GiB peer+Master，LMCache双4GiB CPU MP Server |
| 所有通信先TCP、无RDMA/UB | protocol-evidence：三产品两客户端真实完整内容与TCP syscall证据通过；DS补充三尺寸、Worker flags、实际GET materialization |
| 公平资源与干扰控制 | 环境快照、cgroup-settings、相同16个客户端/服务物理核与NUMA0、12GiB+12GiB、独立监控核；后台服务待本轮完成恢复 |
| exist/put/get与kvtest参考 | test-design.md：共同fresh-key bytes接口、同步完成、独立SDK、多进程同步窗口、固定种子和三次重复；真实共同smoke6cells18结果全部通过 |
| 失败与中间耗时 | 前五轮失败protocol-history、preflight-history、不可变stage、修订SHA链、恢复receipt、操作JSONL；从正式性能窗口排除 |
| 阶段提交 | 50df91a（TCP入口与适配器）、9f9d027（真实协议门禁与修订审计）、6f4225e（复现文档与耗时后处理） |

## 2. 执行中与待闭合项目

| 项目 | 完成证据要求 |
|---|---|
| 正式矩阵 | 完整81cell/216当前结果/72三重复组、原始桶合并、错误与资源覆盖；独立verify_results和verify_tcp通过 |
| CPU/内存/磁盘与短PUT | 主窗口cgroup+/proc、dstat/iostat/mpstat/pidstat/perf原始日志；九格10ms资源专项独立于主三重复；缺失保持null |
| 耗时复盘 | timings.json/CSV分开SDK、命令、启动准备合计、关闭估计、门禁与修订；不把复用构建计为本轮编译 |
| 文档与脚本 | build/test-design/tcp-validation/script-design/timing及reproduce.zip；Mermaid时序；锁定参数、版本、环境和命令 |
| 页面与发布 | xqyun-32c32g:/var/www/html/perf/kvc-tcp-20261020；左侧导航、编号章节、ECharts八图、居中下caption；独立原生基线链接 |
| 实际浏览器和下载验收 | 1440/900/390px、筛选/resize、图点与原始数据绑定、TCP标签/专属验证JSON/文档/归档；所有摘要重算 |
| 环境恢复与成果提交 | 本阶段停止的原服务恢复、全部实验cgroup空、原始源码/基线报告保全；工作台与网站仅本任务范围提交 |

## 3. 结论边界

单节点回环TCP、CPU原始bytes、内存缓存与同步客户端接口。事务轮数、pickle副本、元数据拓扑和内存池触页差异如实同屏说明；结果不外推为跨机RDMA/UB、GPU KV tensor、推理TTFT、容量溢出、持久化或故障恢复表现。原生与TCP两套结果分别统计，不能混合补点。
