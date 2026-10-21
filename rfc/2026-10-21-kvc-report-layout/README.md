# KVC 报告头栏与表格布局修正

- **Status**: Complete
- **范围**：[已发布报告](https://yche.me/perf/kvc-coordinator-modular-20261021/index.html)及其文档阅读器的CSS。

## 1. 原因与修改

实际浏览器发现统一站点头栏 `#site-nav` 内包含搬入的 `header.yche-legacy-links`，报告未限定范围的 `header` 选择器使其仍为fixed并覆盖统一头栏。报告样式改为仅匹配非站点/非搬迁头栏，隐藏重复旧入口，保留离线原生头栏与事件绑定DOM。文档CSS同步隔离旧头栏。

表格由浏览器自动分配列宽，按语义设置最小宽度：失败原因360px、构建tag/SHA250px、构建信息260px、配置值380px。窄屏在表格容器内部滚动，不让整页横向溢出。文档表格最小880px/单列120px，防止多列表被压成字符竖排。

## 2. 发布与历史边界

更新 `assets/report.css`、`docs/_reader/reader.css`，并为主报告及5个文档HTML的CSS链接增加 `?v=layout-20261021-02`，同步gzip及当前SHA256SUMS。发布前备份旧字节到目标节点 `/home/cache/kvc-presentation-revisions/layout-20261021-01/`；公开 `provenance/layout-20261021-01/` 保存原清单及CSS新旧SHA。HTML正文、数据、原始采样、测试结果、ZIP和既有验收回执不改写。原ZIP复现原展示，应用本次CSS可复现新布局；历史验收绑定原清单，当前清单对应这次展示修订。

## 3. 验证

浏览器配方和发布配方保存在本目录。实际截图/结果在 `/home/t14s/workspace/scratch/2026-10-21-kvc-layout-fix/`，浏览器依赖使用当前环境已有Playwright与Chrome。预览通过路由替换CSS，正式验收直接读取公网。覆盖1440/900/390/320宽度、主报告及构建文档、唯一固定头栏、页面不溢出、最小列宽、目录开关/实际章节跳转、浏览器错误与截图。

本次为CSS展示修正，不涉及产品热路径、并发、持久化或产品.repo_context；按ds-self-verify检查diff和实际浏览器，不重跑原生压测。


## 4. 实际完成结果

第一轮CSS部署成功，但普通公网仍取得旧缓存CSS，实际浏览器未通过（两个fixed header、窄列仍在）；不作为完成。第二轮增加明确CSS版本链接，实际公网上线后通过。两轮原清单、备份与修订JSON分别为layout-20261021-01/02，不覆盖第一次证据。

- 预览 `node verify.mjs preview`：8个页面/视口组合通过。
- 公网 `node verify.mjs public`：8个组合通过；1440/900/390/320宽度，每种包含主报告和构建文档；头栏/溢出/列宽/菜单与跳转/页面错误均检查，桌面及手机截图已审阅。
- `python3 check-css.py`：2份CSS的plain/gzip共4次公网字节比较通过。
- 在脚本根执行 `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.contract.test_report_inputs`：8项通过，1.776秒。
- `git diff --check`、发布Python配方语法检查通过。产品仓未改动；没有重跑压测，原132/18/30结果不变。

发布配方按顺序为 `publish-css-only-001.py`（先复制report.css/documents.css到远端/tmp）与 `publish.py`（增加链接版本）。通过 `ssh xqyun-32c32g python3 - < 配方.py` 执行。每个修订备份目录必须全新，重复执行会拒绝，避免覆盖旧证据。脚本不是跨环境通用部署器，URL、路径、依赖和修订名都明确绑定本轮报告。

公开修订身份：[第一次](https://yche.me/perf/kvc-coordinator-modular-20261021/provenance/layout-20261021-01/revision.json)、[第二次](https://yche.me/perf/kvc-coordinator-modular-20261021/provenance/layout-20261021-02/revision.json)。数据JSON与1350557425字节归档在发布前全量SHA核验不变。当前清单记录新展示，历史回执及复现ZIP保留原展示，不能把旧交付验收写成新字节的验收。
