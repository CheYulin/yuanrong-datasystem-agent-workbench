# 1. 模块化重构与最终 Coordinator 测试结果

**Status: In-Progress · W01–W16 已通过相应验收；W17 目标端安装/注册已通过；W18 公网实际下载验收进行中，不将安装成功写成公开验收完成。**

## 2. 原生测试结果

最终运行 `kvc-native-batch-20261021-coordinator003`；目标 tiantiyun-80c128g，DataSystem 使用 native Coordinator 与两个 Workers，控制面计入 server cgroup。三轮共180条记录，132有效、18失败、30不支持，没有缺失或无效条目。有效结果才参与性能图表。原生失败不是流程缺失，证据验收通过不会改写它。

| 产品 | 有效 | 失败 | 不支持 |
| --- | ---: | ---: | ---: |
| DataSystem | 42 | 18 | 0 |
| Mooncake | 60 | 0 | 0 |
| LMCache | 30 | 0 | 30 |

DataSystem 的18条失败是 EXIST hit/miss 在B16/256/4096的三轮调用，原始错误为 RPC deadline exceeded。与历史ETCD基线的失败组合相同，不据此声称已完成TLS直接观测或新根因验证。LMCache的30条N/A是固定release没有等价的独立多key批量CPU/TCP PUT/UPSERT/GET接口；不以逐key循环替代后参与原生排名。

## 3. 来源与复现边界

| 阶段 | 身份与范围 |
| --- | --- |
| DataSystem | v0.9.2.rc44 / 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6 |
| Mooncake | v0.3.13.post1 / 719735896c86b56fabec6cf3e825fb2ea640597a |
| LMCache | v0.5.6 / 3e11e10812b448142c6cdb177a3058652bd826fa |
| 原始源码构建 | `/home/worktrees/kvc-native-modular-20261021-004`；编译命令实际耗时DS965.342663636、Mooncake376.759765198、LMCache74.125489483秒；不相加作为构建总时长 |
| 构建重验 | attempt003，明确未重编译；Coordinator ELF与原已绑定tar成员字节/mode匹配，原metadata失败保留 |
| 正式测量 | coordinator003原始冻结运行代码与部署配置；W09保留exit1/finding，W10/W11通过并排空恢复 |
| P6新布局节点证明 | package005，真实start/gate/smoke/restore；无第二套正式矩阵，731.933716645秒 |
| 最终离线分析/报告 | package007，manifest 49599085a43a19c6ea3280f2671579501175fe73579fb3802169ef5ae3b62ead，ZIP f60a8ee78a5a7a962b4f0b8d144357e8e311e048d13b2746b3a83335e605932f |
| package005→007 | runtime/SDK/build/observe/core/platform/collect及运行pipeline字节/mode一致；差异限发布校验、说明/测试/profile和报告CSS |
| W17网络适配 | 单独的、按实际执行源码绑定的ZIP复用配方，见交付回执附录；不冒充package007内的原始rsync实现 |

全部4,674,945,017字节正式原始采样参与资源审计；150个已执行case窗口覆盖达标，21个distinct DS Coordinator epochs来源/实际身份复核通过。前端显示51,318条实际样本，不将缺失值补零。

## 4. 逐步终态与实测耗时

以下为本轮规范W步骤的原始同进程monotonic耗时；各阶段边界和嵌套关系不同，不累加为全流程时间。W02复用已重验构建，不是重新编译耗时。失败attempt保留，正式终态只在实际完成后填入。

| 步骤 | 验收 | 本步退出码 | 秒 |
| --- | --- | ---: | ---: |
| W01 | passed | 0 | 0.064721941 |
| W02 | passed | 0 | 3.822264368 |
| W03 | passed | 0 | 0.368736261 |
| W04 | passed | 0 | 8.556385308 |
| W05 | passed | 0 | 0.396624812 |
| W06 | passed | 0 | 0.902332892 |
| W07 | passed | 0 | 158.905902333 |
| W08 | passed | 0 | 1.637256830 |
| W09 | passed_with_findings | 1 | 4457.367467702 |
| W10 | passed | 0 | 54.374696476 |
| W11 | passed | 0 | 23.137824022 |
| W12 | passed | 0 | 156.188330779 |
| W13 | passed | 0 | 58.836330661 |
| W14 | passed | 0 | 474.589839736 |
| W15 | passed | 0 | 254.680362641 |
| W16 | passed | 0 | 376.913211987 |
| W17 | passed | 0 | 1734.650078315 |
| W18 | running | — | — |

## 5. 已执行的验证与限制

- 最新全量回归579项通过，62.309秒（public-html-all-002.log）；后续仅报告CSS/文档变动，对应实际浏览器预检通过，不为文档重复运行产品矩阵。
- P6迁移映射142项，全部目标存在；工具源码根只保留README.md、bundle-inputs.json、requirements-report.txt三个文件。
- SDK/共享worker的28个实现文件与24个部署shim资源字节一致；没有请求热循环改动。未执行旧/新源码交替性能基准，也不据目录调整声称性能提升。
- W13/W14最终包完整审计通过，命令579.233766695秒；W15/W16外层命令658.028500788秒，返回0。原报告、干净解压、离线重生成的展示数据SHA一致：857900ac8b628cf1ca910161e482e0c49e527d05c8134d0606028823ee80f2e3。
- 本地与干净包的实际浏览器在1440/390/320宽度通过：9图、4文档、Mermaid、步骤链接、目录与布局。尚不等同于W18公开验收。
- 主报告完整文件4776项，manifest b2a0d19efdd5ce79a0bd2b9c339391060b8989ac18ae4f342f5e407e0316310b；复现ZIP ec27f4b92bd0b00006e095ea3351b284b551d8cb53b2f4d4e005958b2bf4e17c。

本次不改DataSystem产品源码、持久化格式或产品.repo_context；回执/控制IO不进入SDK热循环。新布局资格与Coordinator正式测量、离线/浏览器、公开交付是分开的证据，不能相互替代。

## 6. 实际安装与公开验收状态

[主报告](https://yche.me/perf/kvc-coordinator-modular-20261021/index.html)已安装。W17第四次attempt实际passed，1734.650078315秒；目标端完整4776文件manifest相同，首页290→291，只新增本报告。前三个操作员取消的failed保留。W18已开始实际公网下载，不以目标端SHA替代公网响应验证；独立逐步回执附录将在W18结束后生成，以避免主报告自引用hash。
