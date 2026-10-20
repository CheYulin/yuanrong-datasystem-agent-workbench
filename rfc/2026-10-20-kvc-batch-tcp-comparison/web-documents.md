# 网页文档转换与 Coordinator 术语约定

## 1. 范围与入口

用户要求把报告链接的 Markdown 构建文档、接口及测试细节改为可跳转的网页，并要求 DataSystem 后续统一使用 Coordinator。

- [旧 TCP 文档中心](https://yche.me/perf/kvc-tcp-20261020/docs/index.html)：15 份。
- [批量 pure-kv 文档中心](https://yche.me/perf/kvc-batch-tcp-20261020/docs/index.html)：17 份。
- 两份实验主页原 Markdown 入口已改为相应 HTML；提供左侧文档导航、本页章节、手机目录、代码块、表格滚动、搜索和返回实验报告。
- 11 个 Mermaid 代码块转为 SVG 图，并提供居中下方 caption；源 Markdown 按原字节保留下载。
- 历史 TCP 文档保留阶段 Status，并明确隔离更正及历史适用范围，避免把阶段说明当作当前验收。

## 2. 命名与证据边界

DataSystem 后续架构和组件叙述统一使用 **Coordinator**，已写入 workbench AGENTS.md 与网页阅读器说明。历史 rc44 源码符号、配置字段和日志保留原名，不因此新增 Coordinator 进程或改写实验拓扑。Mooncake 自身的 Master 名称按产品原名保留。

本次只修改文档展示与跳转；不访问压测目标机、不重新构建/压测，不修改产品代码、原始 Markdown、JSON、CSV、ZIP、构建与监控记录。产品热路径、并发、持久化和恢复行为无变更；repo_context 不需要更新，产品 UT/ST 未运行。

## 3. 可复现流程

依赖为 requirements-reader.txt 中的 Markdown==3.10.2。Mermaid 使用报告站点已有 vendored 文件，不在线执行 CDN 代码。输入快照包含两份报告原 HTML/gzip/manifest 和 32 份 Markdown；collect-source.py 及 source-snapshot.json 保存在仓库外证据目录。输出必须是全新目录：

```sh
python3 render-linked-documents.py --snapshot <original> --output <fresh-rendered> --mermaid <vendored-mermaid.min.js>
python3 publish-linked-documents.py --site /var/www/html --snapshot <source-snapshot.json> --rendered <uploaded-rendered>
python3 publish-linked-documents.py --site /var/www/html --snapshot <source-snapshot.json> --rendered <uploaded-rendered> --apply
```

渲染器把同一批文档之间的 .md 链接改为 .html，保留 GitHub/上游 .md 源码链接和原始下载；按 Unicode 生成章节锚点。阅读样式使用内容哈希文件名。发布脚本使用共享发布锁、安全路径、SHA 校验、CAS、失败回滚及原 HTML/gzip/manifest 留档；仅修改两份报告目录。

本次首次上线后，为修正阅读器兼容与旧样式缓存，实际执行了 stylesheet refresh：

```sh
python3 publish-linked-documents.py --snapshot <source-snapshot.json> --rendered <refresh-payload> --refresh-stylesheet
python3 publish-linked-documents.py --snapshot <source-snapshot.json> --rendered <refresh-payload> --refresh-stylesheet --apply
```

此入口只允许把现有文档中的 reader.css 引用替换为本次内容哈希样式，不允许顺便改写正文；未变化文件须与目标机 SHA 匹配，缺少或不一致即拒绝。原阅读版及收据留档到 reader-history/refresh-<manifest-sha>/。新建站点直接用最终模板，不需要重演历史 CSS 修补。

## 4. 实际验证

最终实际浏览器命令：

```sh
node /home/t14s/workspace/scratch/2026-10-20-kvc-doc-pages/qa-reader.mjs https://yche.me /home/t14s/workspace/scratch/2026-10-20-kvc-doc-pages/public-qa-003 public /home/t14s/workspace/scratch/2026-10-20-kvc-doc-pages/rendered-004/reader-build.json
```

结果 **586/586 PASS**，68 个桌面/手机视图、39.640203847 秒；包含真实目录跳转、搜索、全部 Mermaid、版本化样式、侧栏、源文档入口、无横向溢出及无 pageerror。两份主报告继续显示原 ECharts，主要跳转没有本地 .md 链接。实际源码为 qa-linked-documents.mjs，SHA `f51dc556b3036d6af283e26a2219f5a1b49a8a89be5db55e78fa9e832aff6ad6`。

文件审计逐项验证两个报告 **399 / 1891** 个 manifest 条目、最终 84 个渲染文件、全部 32 份源 Markdown、1149 个同站链接；0 缺失，7.229774303 秒。与本次前快照相比，原条目除主 HTML/gzip 外全部不变；两份 ZIP SHA 保持原值。实际命令为 `ssh -o BatchMode=yes xqyun-32c32g 'python3 -'`，stdin 为 executed-final-file-audit-v2.py。

语法和 diff 验证（workbench 根目录）：

```sh
python3 -m py_compile rfc/2026-10-20-kvc-batch-tcp-comparison/render-linked-documents.py rfc/2026-10-20-kvc-batch-tcp-comparison/publish-linked-documents.py
node --check rfc/2026-10-20-kvc-batch-tcp-comparison/qa-linked-documents.mjs
git diff --check
```

以上 exit 0。实际最终公网及文件审计通过，不用本地渲染通过替代公网交互。

## 5. 保留的失败与修正

- 本地第一轮 446 项通过；首次公网 452 项中 28 个手机菜单失败。诊断显示站点 reader 为侧栏添加 data-yche-native-toc=pure，CSS 用 important 隐藏；本地 aria 状态已改变但 display 仍 none。仅本阅读器内增加 ID 级显示规则，并让移入共享导航的 legacy header 采用 static，不改共享站点脚本。
- 第二次公网 486 项中批量文档仍有 15 个手机菜单失败。诊断记录实际 CSSOM 仍是旧版本，另一普通 GET 已返回新版本；采用带内容哈希的新文件名，让 HTML 指向不可变样式。没有以缓存清理、绕过或删去菜单断言判通过。
- 文件审计首版把 URL 的 ../../../index.html 按文件系统父目录解析，误判主页不存在；改用 urljoin 的站点根语义后通过，未修改原站点主页。
- 中间版精确压缩字节比对发现，远端 gzip.compress 与本地生成的 gzip SHA 不同；最终哈希样式及 HTML/gzip 均从本地冻结输出逐字节发布，最终 84 项精确 SHA 已通过。旧样式与 gzip 作为历史保留，不伪称中间校验通过。

## 6. 证据与耗时

所有原输入、失败/成功输出、截图、诊断、脚本实际版本和单调时钟耗时在：

`/home/t14s/workspace/scratch/2026-10-20-kvc-doc-pages/`

其中 local-qa-001、public-qa-001..003、style-diagnostic.json、publish/refresh-execution.json、navigation-compatibility-execution.json、final-file-audit-v2-execution.json、site-commit-timings.json 分别保存真实阶段结果。QA 与文件审计的源码另留原件。站点使用已有 commit_publication.py 隔离 index 干跑并定向提交，保留其他工作区文件，不 push。

Mermaid vendored 文件 SHA256：`70137e77bb273bb2ef972b86e8b0400cca8be53cb25bfc45911a186dc98665de`。
