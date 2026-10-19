# 统一 TCP 实验交付核对

Status: In-Progress；主矩阵、九格 PUT 资源专项及本地结果/协议核验已完成，报告/公开验收待完成；记录外部操作排除项仍为 unknown。此文不替代原始日志或独立验证器。

## 1. 已完成的准备与预检

| 用户要求 | 当前证据与边界 |
|---|---|
| 发布源码构建 | build.md 与 formal-tcp-001/builds：复用已实际完成的三个固定SHA构建，新部署重新核对8个原生制品；本阶段没有重新编译 |
| 同节点双存储服务 | tcp-plan.md 与 products/config：DataSystem双KVC Worker+ETCD，Mooncake双4GiB peer+Master，LMCache双4GiB CPU MP Server |
| 所有通信先TCP、无RDMA/UB | protocol-evidence：三产品两客户端真实完整内容与TCP syscall证据通过；DS补充三尺寸、Worker flags、实际GET materialization |
| 公平资源与干扰控制 | 环境快照、cgroup-settings、相同16个客户端/服务物理核与NUMA0、12GiB+12GiB、独立监控核；后台服务恢复已留证，未记录干扰排除保持unknown |
| exist/put/get与kvtest参考 | test-design.md：共同fresh-key bytes接口、同步完成、独立SDK、多进程同步窗口、固定种子和三次重复；真实共同smoke6cells18结果全部通过 |
| 主矩阵驱动阶段 | 驱动 summary 回报 81 cell/216 标准结果、failed=[]、无 fatal；UTC 2026-10-19 20:41:54.554432→22:42:19.371899，7224.817467s；本地日志另核到216唯一完成事件，原始结果独立复核通过 |
| 九格 PUT 资源专项执行 | summary 回报 UTC 2026-10-19 22:42:44.441697→22:46:17.881811，213.440114s；9/9、valid=true、failed=[]、monitor_exit=0；21192样本、0读取错误，9窗口覆盖、单窗至少58样本；收尾四cgroup均空，原始数据独立复核通过 |
| 失败与中间耗时 | 前五轮失败protocol-history、preflight-history、不可变stage、修订SHA链、恢复receipt、操作JSONL；从正式性能窗口排除 |
| 本地独立结果/协议核验 | 216主结果/72组三重复、9专项及TCP7/7通过；74,795,773成功、0接口失败；223核心JSON与1586历史文件/25输入/8制品已核对，缺interference-audit的排除项仍unknown |
| 收尾恢复 | environment-restored.json：setroubleshootd.service start退出0、后验active，errors=[]且四cgroup空；0.22397715s为记录恢复区间，非SDK耗时 |
| 原始证据归档 | rawtar 4384/4384普通文件逐SHA/尺寸校验、gzip EOF/CRC通过，无遗漏/额外/unsafe/重复；94,315,457B，SHA256见tcp-validation.md；原始日志包不进Git |
| 阶段提交 | 当前截至 966b74a，完整本地提交清单见第4节；不表示TCP公开交付已完成 |

## 2. 执行中与待闭合项目

| 项目 | 完成证据要求 |
|---|---|
| 结果与资源边界（已核对，报告须保留） | verify_results通过但evidence_complete=false，记录干扰排除unknown；20短PUT窗口CPU/coverage未知、2窗口无内存点，8组CPU/2组内存中位留空；专项独立于主三重复，不用于补齐主组 |
| CPU/内存/磁盘与短PUT（已核对，待报告验收） | 7217主样本、21192专项样本，窗口差分/匿名+SHM/总计费口径分开；专项覆盖98.3341%–99.9784%，实际周期p50约10ms，观察者约0.260655核，不能声称零开销 |
| 耗时复盘 | timings.json/CSV分开SDK、命令、启动准备合计、关闭估计、门禁与修订；不把复用构建计为本轮编译 |
| 文档与脚本 | build/test-design/tcp-validation/script-design/timing及reproduce.zip；Mermaid时序；锁定参数、版本、环境和命令 |
| 页面与发布 | xqyun-32c32g:/var/www/html/perf/kvc-tcp-20261020；左侧导航、编号章节、ECharts八图、居中下caption；独立原生基线链接 |
| 实际浏览器和下载验收 | 1440/900/390px、筛选/resize、图点与原始数据绑定、TCP标签/专属验证JSON/文档/归档；所有摘要重算 |
| 恢复后成果提交与公开闭合 | 恢复凭据已核对，仍需本轮最终文档/报告/验收成果提交；工作台与网站仅本任务范围提交，原始源码/基线报告保全 |

## 3. 结论边界

单节点回环TCP、CPU原始bytes、内存缓存与同步客户端接口。事务轮数、pickle副本、元数据拓扑和内存池触页差异如实同屏说明；结果不外推为跨机RDMA/UB、GPU KV tensor、推理TTFT、容量溢出、持久化或故障恢复表现。原生与TCP两套结果分别统计，不能混合补点。

主矩阵、专项和恢复凭据分别记录四个实验 cgroup 为空；后验服务 active 仅表示记录时刻状态。结果比较和资源解释见 tcp-validation.md，限定于本机固定配置。没有 interference-audit.json，因此不宣称已排除任意未记录干扰；尚未完成公开 TCP 页面验收。

## 4. 已记录的阶段提交

- `50df91a`：独立 TCP 入口与适配器。
- `9f9d027`：真实三产品协议门禁和预检修订审计。
- `6f4225e`：TCP 复现说明与全部预检耗时后处理。
- `0e7616f`：TCP 交付需求与原生基线分别核对。
- `3589f0c`：报告区分协议预检失败和正式性能失败。
- `ada452c`：独立 TCP 复现支持与验收命令。
- `966b74a`：完整原始 TCP 证明独立保存并以 SHA 绑定紧凑报告数据，同时保留浏览器验证支持。

以上为当前仓库已存在的阶段提交；主矩阵与专项证据已本地核验，报告、公开验收和最终提交仍待闭合。
