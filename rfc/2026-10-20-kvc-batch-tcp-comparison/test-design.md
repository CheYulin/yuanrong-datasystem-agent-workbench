# 3. 用例设计与公平性合同

## 3.1 固定矩阵

主矩阵使用独立 r3 容量适配 profile：Mooncake per-peer queued/pending 均8192，lane 与 admission timeout 原值，全部 B 使用同一配置；DS/LM 及三方 cgroup/并发/协议/数据量均不变。默认 r2 下带跟踪门禁和 C8 无跟踪 smoke 的实际失败单列为诊断证据，不记为主矩阵的性能重复。

每 key 1 KiB；B=1/16/256/4096；8 个独立客户端，各 endpoint 4 个；3 轮产品顺序依次 DS→Moon→LM、Moon→LM→DS、LM→DS→Moon。每轮各产品用相同的批量顺序；批量顺序按轮旋转。产品服务每轮启动一次，跨 B 保持热工作集，并明确这不是每格冷启动实验。

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

## 3.3 统计边界

- `success` 是全成功 batch 数；`success_keys = success × B`。计速包括同步起点至最晚客户端结束，分别导出 batch/s、keys/s、payload MiB/s。
- 延迟从请求 list 构造完成到 adapter 返回，包含各方 O(B) 输入/输出校验与实际同步 SDK 调用；为整批完成时间。不能除以 B 后称作每 key P99。
- 逐客户端直方图合并后重算桶界。首次 PUT 的 B4096 仅 64 个成功 batch/轮（8 客户端各 8 次），因此尾延迟样本稀少，图表标低样本；不能把 262144 个 key 当 262144 个独立延迟样本。
- GET 定期验证整批内容；PUT 后验抽验首/中/末 batch；upsert 后验读取所有最后写过的批次，再在窗口外还原工作集，防止后续 GET 内容混杂。验证计数区分 key 数与 batch 数。
- cgroup 每 0.2s 采样，独立 dstat/iostat/mpstat/pidstat/perf 每秒；窗口内不足两点时 CPU/I/O 为 null。内存保留 cgroup 总计费、anon、file、shmem，避免只比较 RSS 或只比较懒分配池上限。
- 额外每秒采集外部进程 CPU delta / affinity / cgroup。该审计可发现潜在干扰，不能证明内核、中断和短命进程完全消失。压测期间不做源码扫描、构建、rsync、全目录哈希或大文件取证。
