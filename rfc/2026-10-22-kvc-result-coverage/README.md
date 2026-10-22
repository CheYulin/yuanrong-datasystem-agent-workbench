# KVC 结果覆盖与 EXIST 空白说明

- **Status**: Complete
- **日期**：2026-10-22
- **范围**：[最终报告](https://yche.me/perf/kvc-coordinator-modular-20261021/index.html)展示，不重跑SDK或改写数据。

## 1. 原始结果核对

EXIST命中/未命中 × 3产品 × 4批量 × 3轮 = 72条，全部原始JSON存在且逐份匹配原冻结SHA256SUMS。54条valid、18条failed，没有缺记录。DataSystem B=1共6条有效；B=16/256/4096命中及未命中共18条失败，错误为RPC deadline exceeded。Mooncake和LMCache各24条EXIST有效。

失败轮次中存在部分成功调用（例如r0/datasystem/b16/exist_hit），但整轮失败，不把部分请求吞吐作为有效性能。原始错误仅证明RPC超时，本次未定位其运行时根因，不扩展为新的SDK行为结论。

## 2. 展示修正

原页面成功图表对失败聚合返回null，但只有失败长表，没有完整组合覆盖表，空白易被理解为漏测。新增随接口切换的覆盖统计和12行（3产品×4批量）结果表，列出状态、有效轮次、有效keys/s/batchQPS/P99及三轮JSON。可展开每轮查看成功/失败调用数及原始错误。保留失败表，不删历史错误。

缺记录、重复记录、不支持和原生失败分别显示；只有三个唯一有效轮次且聚合complete才展示性能数值。图表同样检查实际轮次完整性，不能仅凭旧aggregate继续显示缺记录组合。数值来自既有聚合，未经推测/插值/补零。EXIST性能仍含原生失败，不能声称补齐了失败组合的成功性能。

## 3. 历史与发布

`coverage-20261022-01`仅更新index.html、report.js、report.css及gzip/当前SHA256SUMS，资源URL加版本避免旧缓存。保留当前HTML已有复现ZIP的sha查询参数，不覆盖上一轮发布变更。发布前对当前index SHA做CAS检查，对变更文件核对当前manifest，并全量读取data.json/复现ZIP核对SHA。备份到目标节点 `/home/cache/kvc-presentation-revisions/coverage-20261022-01/`；公开provenance下保存修订JSON及旧清单。

历史W18/附录验收绑定原字节，不修改其回执；旧ZIP复现旧展示。数据JSON、data.js、原始结果、原采样、归档及构建版本保持原值。新版代码模板同步修正，未来生成的报告也具有覆盖表。

## 4. 验证配方

实际原始证据、截图和命令结果在 `/home/t14s/workspace/scratch/2026-10-22-kvc-result-coverage/`。本目录保存本轮浏览器/发布/72份公网EXIST核对配方，环境路径明确绑定本轮，不作为通用部署工具。源CSS/JS在脚本正式模块。

预览通过Playwright路由仅替换HTML/JS/CSS；正式验证直接访问公网。浏览器逐接口遍历1440/900/390/320宽度，每次核对12组合状态、36原始链接、精确聚合数值及失败/N/A图表空值，全部180行源数据须与冻结原件相同。额外注入缺记录/重复记录，验证覆盖表报警与图表null；实际展开失败证据并验证RPC错误文本。头栏唯一、无整页溢出、无页面错误及截图一起检查。

按ds-self-verify，本次仅report显示逻辑和展示发布，无产品热路径、并发、持久化修改，无需产品.repo_context更新。不重跑旧588项或原生压测作为这次变更的新结果；执行报告契约测试和真实浏览器。


## 5. 实际完成证据

- `python3 verify-source-exist.py`：72个唯一组合、原件SHA及native.valid全部通过；结果exist-source-audit.json。复执行与已有证明字节一致，不覆盖不同证明。
- `python3 verify-public-exist.py`：公网72份JSON全部HTTP200、完整SHA及native.valid一致，9.281137259秒；public-exist-audit.json。
- `node verify.mjs preview`：20个接口/屏宽组合（每组合12行/36原始链接）通过，21.729109854秒；另实际注入缺记录/重复记录，验证表格和图表均阻止假完整结果。
- `node verify.mjs public`：20个组合通过，24.189551069秒；实际180行源数据与冻结原件一致，9张图表保留原生失败null。桌面、手机截图已查看。
- 脚本根运行 `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.contract.test_report_inputs`：8项通过，1.413秒，report-tests.log。
- JS/Python语法检查、`git diff --check`通过；发布回执publish-result.json中data.json与复现ZIP全量SHA保持不变。公开修订[revision.json](https://yche.me/perf/kvc-coordinator-modular-20261021/provenance/coverage-20261022-01/revision.json)及旧清单可追溯。

发布复现顺序：从当前源码模板的新增覆盖块与线上index.before.html合成候选HTML，保留既有归档查询参数；复制候选index.html及源report.js/report.css到目标节点 `/home/cache/kvc-presentation-staging/coverage-20261022-01/`，再 `ssh xqyun-32c32g python3 - < publish.py`。发布配方的index SHA和全新备份目录绑定当前起点，重复操作会拒绝，不能用于覆盖未来修订。正文/脚本改动不进入SDK热循环，不更改测试成功判定。
