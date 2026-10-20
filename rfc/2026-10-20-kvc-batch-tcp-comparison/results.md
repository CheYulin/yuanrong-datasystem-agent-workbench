# 正式测试结果：证据完整，存在原生失败

R7正式三重复完成：132有效、18原生失败、30 N/A，180唯一槽、36独立服务期、9产品组；独立重算0issues。`evidence_verified=true`与`passed=false`同时保留。失败均为DataSystem B16/256/4096的EXIST命中和未命中，每种组合三轮均失败；144个客户端错误原文及失败回执均保留。

## 1. 支持能力与结果边界

LMCache仅B1 PUT/GET，UPSERT和更大批量PUT/GET为N/A；批量EXIST包含P2P lookup/query/unlock协议成本。不存在的等价接口不填零、不回退逐key数据请求。各B的数据量、校验、C8与服务/客户端配额相同；资源/绑定/管理协议仍有产品差异。

## 2. 吞吐与整批延迟

以下均为同一产品/操作/B的三轮有效中位数。首次PUT固定262144新key；其它操作20秒窗口；P99为整批合并直方图桶上界，不能除以B。大B首次PUT仅64个batch/轮，不能把key数当延迟样本数。

| 产品 | API | B | 结果 | keys/s中位数 | 整批P99 µs | CPU核 | 计费GiB | 匿名+SHM GiB |
|---|---|---:|---|---:|---:|---:|---:|---:|
| datasystem | exist_hit | 1 | complete | 46290.671 | 282.624 | 8.076 | 5.781 | 1.117 |
| datasystem | exist_hit | 4096 | failed | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| datasystem | exist_miss | 1 | complete | 40838.276 | 294.912 | 8.074 | 5.774 | 1.109 |
| datasystem | exist_miss | 4096 | failed | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| datasystem | get | 1 | complete | 30700.855 | 405.504 | 8.102 | 5.779 | 1.117 |
| datasystem | get | 4096 | complete | 389920.746 | 98566.144 | 10.278 | 6.192 | 1.536 |
| datasystem | put | 1 | complete | 15443.995 | 679.936 | 8.316 | 6.560 | 1.900 |
| datasystem | put | 4096 | complete | 72029.617 | 486539.264 | 8.184 | 6.798 | 2.133 |
| datasystem | upsert | 1 | complete | 15448.970 | 679.936 | 8.297 | 5.888 | 1.225 |
| datasystem | upsert | 4096 | complete | 74236.078 | 524288.000 | 8.171 | 6.209 | 1.554 |
| lmcache | exist_hit | 1 | complete | 3868.078 | 3244.032 | 5.496 | 14.783 | 10.123 |
| lmcache | exist_hit | 4096 | complete | 65619.641 | 763363.328 | 3.149 | 14.836 | 10.183 |
| lmcache | exist_miss | 1 | complete | 6266.071 | 1818.624 | 5.780 | 14.786 | 10.125 |
| lmcache | exist_miss | 4096 | complete | 174034.497 | 436207.616 | 4.121 | 14.834 | 10.178 |
| lmcache | get | 1 | complete | 748.660 | 14155.776 | 4.736 | 14.791 | 10.132 |
| lmcache | get | 4096 | not_supported | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| lmcache | put | 1 | complete | 2429.087 | 5242.880 | 5.223 | 15.341 | 10.682 |
| lmcache | put | 4096 | not_supported | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| lmcache | upsert | 1 | not_supported | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| lmcache | upsert | 4096 | not_supported | 未观测 | 未观测 | 未观测 | 未观测 | 未观测 |
| mooncake | exist_hit | 1 | complete | 90952.414 | 143.360 | 8.259 | 5.298 | 0.653 |
| mooncake | exist_hit | 4096 | complete | 3442834.389 | 9437.184 | 6.755 | 5.396 | 0.750 |
| mooncake | exist_miss | 1 | complete | 91729.692 | 143.360 | 8.244 | 5.289 | 0.649 |
| mooncake | exist_miss | 4096 | complete | 7377809.183 | 4784.128 | 7.845 | 5.391 | 0.746 |
| mooncake | get | 1 | complete | 36200.604 | 335.872 | 8.288 | 5.315 | 0.670 |
| mooncake | get | 4096 | complete | 99263.341 | 440401.920 | 9.032 | 5.445 | 0.795 |
| mooncake | put | 1 | complete | 27498.243 | 450.560 | 8.387 | 5.819 | 1.176 |
| mooncake | put | 4096 | complete | 105987.211 | 398458.880 | 9.051 | 5.907 | 1.260 |
| mooncake | upsert | 1 | complete | 27967.304 | 434.176 | 8.349 | 5.313 | 0.671 |
| mooncake | upsert | 4096 | complete | 115633.487 | 356515.840 | 9.210 | 5.396 | 0.747 |

全部B16/B256及波动、单轮结果、原始错误见报告`analysis.md`、`summary.csv`和交互图。存在原生失败的六种组合不生成性能聚合；其错误窗口中的成功速率只作诊断，不与有效窗口排名。

## 3. 正确性与解释

短门禁/smoke通过不意味着20秒窗口必然通过。DataSystem的长窗口失败已在独立目录和关闭RocksDB/stream后复现；四个独立SDK对照支持GET/EXIST共享线程池旧deadline的源码机制，详见datasystem.md及verification.md，不把它归因于共享RocksDB，也不把未直接读取的TLS内容写成已观测事实。

原生源码未修改。这里是固定版本、单节点双服务、TCP、1KiB、C8的Python SDK端到端负载，不是所有语言SDK或硬件路径的性能极限，也不推断跨节点、RDMA、GPU、推理或故障恢复收益。配置优选移除已确认无用功能，没有穷举全局最优。
