# 5. 发布、浏览器验收与复现入口

## 5.1 已发布入口

- [研究报告](https://yche.me/site-api/kvc-lab/ui/study.html)：12张ECharts，基础四接口、worker/client、中心配置、逻辑节点和完整证据。
- [实验控制页](https://yche.me/site-api/kvc-lab/ui/)：部署、运行、停止、持久进度及受保护下载。
- [步骤与运行记录](https://yche.me/site-api/kvc-lab/ui/guide.html) 与 [构建/脚本/结果文档](https://yche.me/site-api/kvc-lab/ui/docs/)。

已有站点管理账户登录后访问；页面、资产、原件与源码下载均经服务端授权。没有新增账户，浏览器验收复用既有账户JWT会话链，未测试密码输入。

## 5.2 冻结身份与实际结果

| 对象 | 已验证身份 / 结果 |
| --- | --- |
| 原生正式实验源码004 | `61c5c2e3151b227d5cc8bc86688164e622027ce953f67ef555ca54c4e044779e` |
| 安装006源码 | `96011ed08ba0fbe197da923d04fe49a4ede577ddc963338d42b66395dbfd0026` |
| DataSystem | `v0.9.2.rc44` / `2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6` |
| Mooncake | `v0.3.13.post1` / `719735896c86b56fabec6cf3e825fb2ea640597a` |
| 组合审核 | 240/240真实completion结果、18741项检查、176个统计组；缺失/重复/无效/异常均0 |
| 组合审核SHA | `8be578f7cb022d3597ad4eb708d787fe3810eb8400b0bcba9580b85792d775c7` |
| IO概述SHA | `0a41392b74b40c04c0a04162591876b89c09416447dbba0fd7ddc8b71bf5f196` |
| 数据质量 | 保留41个有界PUT短窗口、9个低样本P99 |
| 一跳资格 | 27次DS部署的独立B1 fresh写计数/回读均通过，仅限资格窗口 |
| 回收 | 最终stop任务成功；owned cgroup无PID，active execution为terminal |

安装006的站点58份与目标373份payload逐SHA匹配源码包。当前源码包378文件通过下载与解包安全检查，历史004包355文件仍按各job绑定原SHA。本文记录安装之后产生的验证回执；代码包与原生实验的冻结SHA持续作为各自身份，生成文档的MD输入SHA另列于 `reader-build.json`。

## 5.3 真实公开URL浏览器验收

006完整只读验收首attempt通过，耗时117.385秒。所有数值来自实际HTTPS下载的审核文件；不是本地合成性能fixture。

| 验收项 | 实际结果 |
| --- | --- |
| 研究页1440/900/390/320 | 各12张图逐点匹配原审核，EXIST hit/miss、P99及产品切换通过；三表和六任务步骤展开后内外无横向溢出 |
| 指南四宽 | alias302至受保护canonical；每宽1张真实Mermaid SVG，无重复header和溢出 |
| 5篇文档×四宽 | 20视图、52张表均无局部或整体横向溢出；真实列标签/headers/roles完整；每宽2张实际Mermaid |
| 匿名保护 | 50条HTML/JS/CSS/vendor/data/docs/source与任务下载路由401，no-store及Vary Cookie通过 |
| 原始/源码下载 | 当前及历史源码、manifest绑定通过；6份reproduce.sh下载核验，未执行 |
| 浏览器网络 | 416次browser GET、73次direct GET；非GET、页面错误和未知外源均0；28次已知统计beacon阻断 |

最初005文档检查的脚本把站点自动注入的布局/统计脚本误当作reader路径错误，原attempt保留。随后按实际依赖分类复验20页通过，187.444秒是005各轮观察脚本执行时间之和，非性能测量时间。通用文档原先有局部宽表滚动，006加入本实验专用卡片资产后完成上表实际验收。

应用回归命令：`python3 -B -m unittest discover -s tests -p 'test_*.py'`（在kvc-lab目录），153项通过，4.409秒。

## 5.4 操作耗时与产物

| 操作 | 秒 |
| --- | ---: |
| install004 | 91.177 |
| install005（研究页首次发布） | 64.087 |
| install006（文档卡片化） | 68.942 |
| 源码006两端逐文件SHA核对 | 4.237 |
| 文档卡片本地20视图验证 | 10.833 |
| 006线上完整只读验收 | 117.385 |
| 六组组合离线审核 | 4.473 |
| IO概述CLI逐字节复现 | 0.140 |

每个实验job的步骤回执另有部署、资格、预装载、逐case和清理的monotonic耗时；SDK窗口与站点wall端到端时长按不同口径记录。正式六组的完整耗时与失败观察见结果文档，不将部署/验收开销当作SDK吞吐。

本地最终回执：`study-public-live-006-2026-10-26T18-22-08-183Z/result.json`、`install-006/install-result.json`、`installed-code-identity-006.json`、`observability-final-reproduction-004.json`，均位于外部scratch证据目录，账户和token未写入仓库/回执。

## 5.5 文档发布的脚本入口

从仓库根目录执行以下脚本，生成当前RFC的HTML、MD及私有reader资产；MD的SHA由reader-build列出。临时产物留在仓库外，使用已配置的站点SSH alias。原生实验复现另按构建文档和各job下载的reproduce.sh执行。

```python
import json, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, "scripts/experiments/kvc-lab/deploy")
from render_docs import render
from install import transfer
evidence = Path(tempfile.mkdtemp(prefix="kvc-lab-docs-publication-"))
started = time.monotonic()
render(evidence / "docs")
bundle_sha = transfer("xqyun-32c32g", evidence / "docs",
                      "/opt/kvc-lab-20261026/web/docs", evidence, "docs-code")
(evidence / "receipt.json").write_text(json.dumps({
    "bundle_sha256": bundle_sha, "duration_s": time.monotonic() - started
}, indent=2) + "\n")
```
