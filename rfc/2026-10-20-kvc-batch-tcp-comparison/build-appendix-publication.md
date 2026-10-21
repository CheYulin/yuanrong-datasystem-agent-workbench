# 构建附录发布与验证记录

> 目录重构说明：本专题历史脚本和模板已归档至工具的 tools/migrations/kvc_20261020/report-tools，历史浏览器检查在 tests/acceptance/legacy。下文原始执行命令与耗时保留其当时路径；新实验使用工具 README 中的模块入口。

## 1. 请求与页面范围

用户要求在网页增加附录，明确 DataSystem / Mooncake / LMCache 如何构建、关键配置与 tag。本次同时更新[旧单 key TCP 页面](https://yche.me/perf/kvc-tcp-20261020/index.html#build-appendix)及[R7 批量页面](https://yche.me/perf/kvc-batch-tcp-20261020/index.html#build-appendix)，增加目录和页首快捷入口。附录分 A.1 版本、A.2 构建、A.3 运行参数、A.4 证据。

- 版本：DataSystem v0.9.2.rc44 / 2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6；Mooncake v0.3.13.post1 / 719735896c86b56fabec6cf3e825fb2ea640597a；LMCache v0.5.6 / 3e11e10812b448142c6cdb177a3058652bd826fa。
- 区分 rc44 与 final、Mooncake tag 与 wheel 内部 0.3.12.post1、编译 HTTP 能力与运行 metadata server、首次构建与后续构建物复用。
- 对照旧 TCP 和 R7 pure-kv 的 RocksDB / stream、观测、淘汰、队列配置，避免倒写历史；构建时长不作为编译速度排名。
- 原构建记录、完整重放配方、R7 Worker 旗标和冻结启动脚本在附录中可直接访问。

## 2. 来源与修改边界

内容依据本专题 build.md / configuration-profile.md、已保存 builds/*.json、旧 formal-tcp-001/build-and-script-evidence、R7 gate 旗标及 measured-runtime 冻结脚本。build-appendix.html 是最终展示模板；publish-build-appendix.py 为定向发布入口，使用共享发布锁、SHA/payload 校验、原始 HTML/gzip/manifest 留档、CAS 与失败回滚。数据、ZIP、既有收据不重写；旧收据的适用版本在正文明确说明。

原压测目标节点未访问；没有产品源码、构建或测试路径、运行进程和配置变更。不重跑编译或压测。热路径、并发和持久化恢复无产品行为改变；repo_context 无需更新。未运行产品 UT/ST，因为此次为网页文档补充。

## 3. 实际验证命令与结果

从本地 workspace 运行的实际浏览器命令：

```sh
node /home/t14s/workspace/scratch/2026-10-20-kvc-build-appendix/qa-appendix.mjs /home/t14s/workspace/scratch/2026-10-20-kvc-build-appendix/browser-qa-004
```

结果：66/66 PASS，45.812620781 秒；2 页面 × 1440/390 视口；版本身份、构建命令展开、配置、章节导航、无文档溢出、原 ECharts 图、手机 tag 不断行且表格横向滚动、所有附录链接 HEAD 200、无 pageerror。实际 QA 源码同 [qa-build-appendix.mjs](../../scripts/experiments/kvc-fair/tests/acceptance/legacy/qa-build-appendix.mjs)，SHA `2ad89c08837aec99c93b19c9e20d7ab275ba83269bf6b0250f17bfd852b8ae7b`。截图已查看桌面和手机版本表。

语法与差异检查（workbench 根目录）：

```sh
python3 -m py_compile rfc/2026-10-20-kvc-batch-tcp-comparison/publish-build-appendix.py rfc/2026-10-20-kvc-batch-tcp-comparison/restore-appendix-build-records.py rfc/2026-10-20-kvc-batch-tcp-comparison/appendix-mobile-layout-v1.py rfc/2026-10-20-kvc-batch-tcp-comparison/appendix-mobile-layout-v2.py
node --check rfc/2026-10-20-kvc-batch-tcp-comparison/qa-build-appendix.mjs
git diff --check
```

以上均 exit 0。文件审计实际执行 `ssh -o BatchMode=yes xqyun-32c32g 'python3 -'`，stdin 是仓库外 executed-manifest-audit.py：逐项安全路径、SHA、gzip、原 manifest 不变项、原 ZIP SHA 核对。

最终文件审计 PASS，远端 6.549145060 秒：

- kvc-tcp-20261020：315 项通过，补充前 289 项原条目未变（仅 HTML/gzip 被替换），ZIP `921d4c10cdc624c81d004ae1d788eb1e6bb6403124c221f6cf1ba001cffb7471`；最终 HTML `e3174a535c7a3611656dbe4d5e42ce9d681d5c46987df357d3331b207e8a263b`。
- kvc-batch-tcp-20261020：1799 项通过，补充前 1778 项原条目未变（仅 HTML/gzip 被替换），ZIP `5627acfe3244071332dc8a18cf8aa49583d2010ca1e09a94af49321b837bc938`；最终 HTML `01785a284e64d19141fd85cb1c58129b31824dd2af50ac36837974c6e3366889`。

## 4. 发现的问题与修正

- QA-001 如实失败：旧 TCP 缺三份 builds JSON 的链接 404；拓扑图使用 ECharts graphic，QA 要求所有图必须有 series 的谓词不适用。补入与原构建记录 SHA 一致的三份 JSON；只对 topology 接受实际非空 graphic，未放宽其他图。修正脚本见 restore-appendix-build-records.py。
- QA-002 的 64 项通过，但截图中的手机版本表被压窄。采用表格横向滚动并固定 tag 不断行，历史修正脚本 appendix-mobile-layout-v1.py 保留。
- QA-003 的新增手机滚动两项失败：站点 reader CSS 用 important 覆盖 min-width。appendix-mobile-layout-v2.py 仅在本附录 ID 内覆盖，未修改共享样式；QA-004 最终 66 项通过。两次修正的 CAS 旧 SHA 绑定对应历史版本，不用于盲目重放已更新页面。
- 初次读取提交日志时把多段日志当单个 JSON，出现 Extra data；日志实际为 JSON + diff stat，不影响发布或验收，后续使用 JSONDecoder.raw_decode 读取首对象。

## 5. 复现、留档与耗时

最终模板、定向发布脚本、旧记录补齐脚本、两份历史手机迁移脚本与最终 QA 源码随 RFC 提交。重放至补充前页面时，用最终模板和配方/旗标生成 payload-sha256.json，先运行 publish-build-appendix.py --payload <目录> 干跑，再加 --apply；旧页缺 builds 目录时执行 restore-appendix-build-records.py。最终模板已包含手机样式，无需重放两份历史手机迁移。

原始 before HTML/gzip/manifest 在站点各 build-appendix/history/ 与 mobile-layout/ 留档；文件链接补齐前 manifest 在 link-repair/ 留档。完整实际输入、输出、全部失败 QA、截图、实际执行源码、source SHA 和各步时间位于：

`/home/t14s/workspace/scratch/2026-10-20-kvc-build-appendix/`

其中 publish-dry-run/apply.json、repair-build-links-execution.json、mobile-layout[-v2]-execution.json、browser-qa-001..004/qa.json、final2-manifest-audit-execution.json、site-commit-timings.json 分别保存各步耗时。站点提交使用已有隔离 index 的 commit_publication.py，校验 master/origin、仅本报告路径与 blob 大小，保留其他用户工作区；没有 push。
