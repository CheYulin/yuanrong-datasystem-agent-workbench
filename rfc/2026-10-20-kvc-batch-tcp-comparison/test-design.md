# 3. 用例设计与公平性合同

## 3.1 固定矩阵

主矩阵使用独立 r7 `pure-kv` profile：Mooncake per-peer queued/pending 均8192，lane=4、admission timeout=1000ms显式固定，全部 B 使用同一配置；DataSystem 关闭 RocksDB 写入及 stream，两 worker 仍保留独立 fresh 工作目录。三方关闭经源码确认无用的功能，cgroup/并发/TCP/对象大小保持相同。默认 r2、共享目录 r3、旧协议 r4、独立目录仍出现 deadline 的 r5 分别保留，不记为 r7 性能重复。配置依据与未关闭部分见[精简配置设计](configuration-profile.md)。

每个产品/重复先运行独立 READ 服务周期，只预置65536 keys/64MiB，在四档 B 上测 UPSERT/GET/EXIST，再停止并排空服务。随后每个支持的 B 分别启动全新服务周期，预置同一65536-key池，固定预热32768新keys/32MiB并完整读回，再测262144新keys/256MiB fresh PUT；测前驻留98304 keys/96MiB，所有 B 和支持产品相同。各PUT周期之间以及PUT与READ之间不累计数据。LM 不支持的 PUT 槽保持 N/A，不通过逐 key setup补造批量性能。

正式共9个产品/重复组、36个服务周期；每个周期独立记录启动/停止/排空、起止与 SDK 窗口归属。READ首个支持的操作执行预置，其余复用该池；每个PUT周期都重新预置。初始化、固定量PUT预热、后验校验与关闭不进入 SDK 吞吐窗口。PUT 的固定量预热与其它操作3秒预热是两种明确不同的准备合同。

每 key 1 KiB；B=1/16/256/4096；8 个独立客户端，各 endpoint 4 个；3 轮产品顺序依次 DS→Moon→LM、Moon→LM→DS、LM→DS→Moon。每轮各产品用相同的批量顺序；批量顺序按轮旋转。READ 服务期内跨 B 保持同一热池；每个支持的 PUT 档位另启全新服务期。计时前均完成初始化与预热，启动耗时只进编排时间线，不把本矩阵称为 API 冷启动性能。

| 操作 | DS | Mooncake | LMCache CPU TCP | 负载 |
|---|---|---|---|---|
| 首次 PUT | mset，各 B | put_batch，各 B | scalar TCP，仅 B1 | 每 B 共 262144 个新 key，256 MiB；不循环旧 key；完成固定总量 |
| 更新 upsert | mset，各 B | upsert_batch，各 B | N/A | 固定 65536 keys / 64 MiB，20s，轮换两组真实 payload |
| GET | get(list)，各 B | get_batch，各 B | scalar TCP，仅 B1 | 同一热工作集，20s，全命中 |
| EXIST hit/miss | exist(list)，各 B | batch_is_exist，各 B | P2P 原生 ObjectKey list，各 B | 20s，分别全命中 / 全不命中 |

LMCache EXIST 涉及 lookup-and-lock / result-query / hit unlock ACK，与 DS/Moon 的布尔查询成本不同；图中必须标注。CPU MP 多 chunk 的 rolling prefix 无法代表任意独立 key 批量 GET/PUT，不列入等价接口。未支持格子输出明确 N/A JSON，不补零、不计入吞吐中位数。

## 3.2 正确性门禁

两客户端端点分别验证四档 B：独立 key 对应独立完整字节；完整 GET、反序 GET、子集 GET、全 hit、全 miss、交错 mixed hit/miss 向量及反序。B1 无法在一个元素中混合 hit/miss，单独标不适用。LM 批量 PUT/GET 不支持时，逐 key setup 和 B1 完整读回明确属于准备/正确性，不作为原生 batch 性能。

对已存在 key 使用另一组 payload：DS mset 可更新；Moon put_batch 与 LM store 可能 ACK 成功但保留旧值；Moon upsert_batch 必须真实更新。失败不自动重试，保留可能部分提交的未知数。LM 元数据命中锁必须在 finally 释放并等待 ACK，异常后 adapter 失效，不能把异常改写为 miss。

Gate 通过后先跑相同接口编排 smoke，再跑正式三轮。smoke 与正式结果独立，正式开始前冻结输入哈希。

正式矩阵保留 180 个槽：150 个原生支持槽、30 个 N/A。原生支持槽可以是有效完整测量，或带真实 rc=1/原件 SHA/错误/事件/服务周期的只读失败；两者不能混为“通过”。只有全部三轮有效的组合展示性能中位数。启用 `--continue-readonly-errors` 仅用于正常退出且证据完整的原生 GET/EXIST 状态码错误；写失败、字节或布尔校验失败、预热失败和进程异常仍终止。另设 fresh SDK 的限速 deadline 对照诊断，不作为正式性能重复。

## 3.3 统计边界

- `success` 是全成功 batch 数；`success_keys = success × B`。计速包括同步起点至最晚客户端结束，分别导出 batch/s、keys/s、payload MiB/s。
- 延迟从请求 list 构造完成到 adapter 返回，包含各方 O(B) 输入/输出校验与实际同步 SDK 调用；为整批完成时间。不能除以 B 后称作每 key P99。
- 逐客户端直方图合并后重算桶界。首次 PUT 的 B4096 仅 64 个成功 batch/轮（8 客户端各 8 次），因此尾延迟样本稀少，图表标低样本；不能把 262144 个 key 当 262144 个独立延迟样本。
- GET 定期验证整批内容；PUT 后验抽验首/中/末 batch；upsert 后验读取所有最后写过的批次，再在窗口外还原工作集，防止后续 GET 内容混杂。验证计数区分 key 数与 batch 数。
- cgroup 每 0.2s 采样，独立 dstat/iostat/mpstat/pidstat/perf 每秒；窗口内不足两点时 CPU/I/O 为 null。内存保留 cgroup 总计费、anon、file、shmem，避免只比较 RSS 或只比较懒分配池上限。
- 额外每秒采集外部进程 CPU delta / affinity / cgroup。该审计可发现潜在干扰，不能证明内核、中断和短命进程完全消失。压测期间不做源码扫描、构建、rsync、全目录哈希或大文件取证。

计费内存是窗口内 `memory.current` 的观测最大值，包含文件页等cgroup开销，不是净新增对象内存或进程RSS；不推断性扣除测前计费基线。匿名+SHM单列，不能再与可能包含SHM的file项简单相加。未在各产品间执行全局drop_caches；预热、轮换顺序与固定池用于对齐工作负载，不据此声称所有系统缓存状态完全相同。
