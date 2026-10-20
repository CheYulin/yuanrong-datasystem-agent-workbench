# 1. 模块化重构验证计划与设计检查

**Status: Draft · 本文区分已执行的文档检查与尚未执行的实现验收。**

基线 `d5907ed041f44261bff82c99ac41bc96e2f0d18d`。主设计见 [design.md](design.md)，清单见 [migration-map.csv](migration-map.csv)。

新增的核心验收要求见 [工作流逐步验收](workflow-acceptance.md)：每步有中间结果、独立验收与来源绑定；W01–W18 是实验运行步骤，P0–P6 是本工具重构阶段。

## 2. 本次设计检查

本阶段仅修改 RFC 和 RFC 索引，不执行产品构建、目标节点清理、压测、网站发布或代码迁移。以下为 2026-10-20 实际检查结果；未执行的后续实现验收仍在第 3 节单列。

| 检查 | 结果 |
| --- | --- |
| 基线文件清单与映射一一覆盖 | 通过：132 个工具文件 + 10 个 RFC 辅助文件，共 142 项，无遗漏、重复或越界目标路径 |
| 模块边界、热路径、并发、证据与恢复风险检查 | 完成源码核对与双路交叉审阅；已修正 suite 监控生命周期、共享结果校验归属、正确性诊断映射、审计产物绑定 |
| 相对文档链接 | 首次设计 26 个；逐步验收补充后 32 个本地文档/源码链接均存在 |
| Mermaid 语法和 SVG 生成 | 首次设计 3 图；逐步验收补充后共 4 图，使用 Chromium 内 Mermaid 实际 parse/render；未只以代码围栏数代替语法验收 |
| 逐步验收设计覆盖 | W01–W18 共 18 步无缺号，均列输入/中间结果/验收条件/失败处理；统一回执包含 inputs/result/artifacts/acceptance/summary/commit |
| diff 空白检查 | `git diff --check` 与 `git diff --cached --check` 均通过；新增 CSV 已统一为 LF 行尾 |

本次映射/链接检查约 0.025 秒，含启动浏览器的 Mermaid 检查约 3.08 秒，均为实际工具 wall 耗时，仅代表这两条检查命令；未据此估算整个设计工作的总耗时。未重跑历史 465 项回归或目标节点实验，因为本阶段没有实现变更。

逐步验收补充经只读交叉复核，补齐了已验收产物不可变约束：观测首样本快照与开放采样流分开，环境恢复不改写已封存账本，收集排除自身与后续回执，交付验收写在报告树外。文档中的复核命令重新执行成功，映射/链接约 0.062 秒，4 图实际渲染约 2.98 秒；这些仍是文档检查，未执行新增运行契约场景。

## 3. 后续实施的验证矩阵

| 阶段 / 风险 | 验证方式 | 必须满足 |
| --- | --- | --- |
| P1 公共能力 | 复用既有 unit、安全验证；新增必要依赖规则检查 | run 不依赖 collect/report/diagnostics，collect 不依赖 run/report；共享纯结果校验在 core；无 import 副作用；不导入产品 SDK 也能查看 help 和离线处理 |
| P1–P5 每步验收 | 逐步执行、只读 verify-step、缺产物/篡改/换 run/返回码 0 但结果无效等负例 | 输入、结果、清单、acceptance、summary、最终 commit 齐备且 hash 绑定；下游只消费允许且匹配的验收结果 |
| P1–P5 不可变交接 | ready 后追加采样、恢复写新事件、收集自身排除、验收输出位置 | 已封存上游 hash 不受正常下游写入影响；开放流先快照或 finalize；报告验收和交付记录不进入已封存报告树 |
| P3/P4 失败与恢复交接 | ready 后采样失败、原生失败、清理恢复失败、硬退出、跳过必需步骤和过期回执 | ready 不代替全程观测；原生失败不冒充成功性能；清理不明阻断正常交接；诊断与正常依赖显式区分；不自动重放写请求 |
| P1 bundle | 对比源码清单、部署包、快照、ZIP，检查 mode 与安全相对路径 | 包含所有包、配置、配方、资源与必要测试；不依赖仅三个产品子目录的旧白名单 |
| P1 进程/锁 | 受控自有子进程、错 start ticks/命令身份、共享 inode 验证 | 错身份进程不终止；新旧工具同资源互斥；持锁和排空约束不降级 |
| P2 构建 | 假/已有已核验 catalog 单元契约；真实构建按改动选做 | builds-only 不要求旧 formal 成功；tag/SHA、实际源码、recipe/lock、产物与构建范围绑定；shell 可执行位保留 |
| P2 stage | 新目录、失败中断和冻结清单对照 | 配置只物化一次，输入 SHA/mode 正确；临时目录不冒充完成 stage；拒绝危险根路径 |
| P3 在线运行 | 复用 gate/epoch/failure 测试和本地安全验证；真实产品小规模 smoke | key/字节/顺序/hit-miss 校验不变；每 B fresh PUT 与 READ 独立 epoch；只读原生失败规则保留；写失败不自动重试 |
| P3 观测 | 工具缺失、非零退出、预期 SIGINT、单样本与时间窗口场景 | suite 级持续监控不变；全 suite 与 SDK 窗口分开；采样状态与业务状态分开；外部进程只读；监控预算明确；覆盖不足不捏造 0 |
| P3 热循环 | 代码审查与代表 B/operation case 前后交替对照 | 无新增逐请求锁、IO、日志、IPC、额外复制；分布变化结合噪声和置信范围分析，不设置无依据固定百分比阈值 |
| P4 离线处理 | 新旧代码读相同冻结证据；篡改/缺失/重置 counter 负例 | 稳定字段、状态、桶界、资源与耗时归约等价；原生失败和 N/A 不进入成功统计；来源变更被识别 |
| P4/P5 产物绑定 | 交换不同 run/profile 的 analysis、audit、index，或篡改稳定内容 | AuditResult 绑定 index/analysis hash、profile/schema 与源码身份；report 拒绝不匹配组合，不仅检查一个 verified 布尔值 |
| P4 历史兼容 | native/single-TCP/batch-TCP reader fixtures | 不覆盖旧输入、归档成员和历史失败；不将旧隔离不合格结果混入 R7 公平基线；未知 schema 拒绝 |
| P5 页面与交付 | 现有浏览器 QA 参数化后运行；桌面/窄屏和干净解压 | 左导航、编号、ECharts、居中图注、Mermaid、文档跳转、失败证据链接、资源加载正常；ZIP 可独立运行离线命令 |
| P5 发布 | 临时站点/有限刷新故障注入与验收 | 生成不发布；发布锁、首页 CAS、gzip、原始 ZIP 来源成员属性及线上下载 hash 保留；发布成功不改原生矩阵状态 |
| P6 入口退出 | 引用扫描、旧 CLI 对照与完整复现包验收 | 旧入口只转参，移除前已更新文档/自动化；历史包原样可读，不保留重复业务实现 |

## 4. 优先复用的测试证据

以下均为当前工具中的测试名；迁移后测试路径随清单调整，核心语义继续保留。

- `test_batch_epochs.py`、`test_batch_epoch_verify.py`：服务周期、独立目录、测量窗口和 fresh PUT。
- `test_batch_failures.py`、`test_batch_outcome_links.py`：原生失败、完整性和失败证据链接。
- `test_batch_resource_audit.py`、`test_batch_deployment_audit.py`：缺失、counter reset、进程身份、原始来源与后处理依赖。
- `test_batch_report_deployment.py`：原始运行、失败尝试、冻结脚本、TAR/ZIP 字节与 mode。
- `test_batch_refresh_delivery.py`、`test_versioned_delivery.py`：有限刷新、原始成员属性、精确下载版本与回退。
- `test_batch_build_stage.py`、`test_batch_stage_overrides.py`：纯构建入口、输入冻结与 launcher mode。
- `datasystem/test_runtime_safety.py --out <new-output-dir>`：独立程序，归 `tests/integration/`；不纳入普通 unittest 自动导入。

现有完整回归的历史结果只作为迁移起点，不作为重构后通过的证据。后续每阶段运行受影响的最小测试，阶段收尾再做必要全套回归；仅文档变动不重复运行产品实验。

## 5. 新旧离线结果对照口径

对照 fixture 至少覆盖：正常成功、真实原生失败、N/A、缺失/损坏输入、单采样、counter reset、错误 PID 身份、未知 schema 和中断 stage。冻结数据量较大的真实证据仍在仓库外；仓库内只保留最小脱敏 fixture 与清单。

稳定比较项：case/epoch/计划身份、配置与输入来源、成功/错误数、状态及失败原因、合并直方图、QPS/keys/s/整批延迟、资源归约、覆盖度、原始成员 SHA/mode、耗时的测量/未知/估算分类。

生成时间、后处理耗时、渲染工具身份、交付压缩文件 hash 可能随实现变化，按各自 provenance 和确定性规则验证，不要求整份报告逐字节相同；同一冻结原件本身不得变化。

## 6. 实施中的工程自检

每阶段记录具体命令、退出码、实际耗时、输入/输出与未执行项；执行 `git diff --check`、审查 diff、确认作用域和工作区。产品 hot path、并发、恢复或构建行为若有实际改变，再增加对应真实验证；不能以目录重构或单元测试推断性能与恢复安全。

本 RFC 未改变产品模块、构建路径或产品测试路径，因此本阶段无需修改 DataSystem `.repo_context/`。实施如果仍仅整理 workbench 工具，更新工具 docs/RFC 导航即可；若扩展到产品代码，再按产品仓工作流补充上下文和验证。

## 7. 文档检查复核方法

在 workbench 根目录执行以下检查；它只核对文档清单和链接，不执行实验，也不迁移文件。

```bash
python3 - <<'PY'
import csv, re, subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit
rfc = Path('rfc/2026-10-20-kvc-tool-modularization')
rows = list(csv.DictReader((rfc / 'migration-map.csv').open()))
expected = set(subprocess.check_output(
    ['git', 'ls-files', 'scripts/experiments/kvc-fair'], text=True).splitlines())
helpers = subprocess.check_output(['git', 'ls-files',
    'rfc/2026-10-20-kvc-batch-tcp-comparison',
    'rfc/2026-10-20-kvc-fair-comparison'], text=True).splitlines()
expected.update(p for p in helpers if Path(p).suffix in
                {'.py', '.sh', '.mjs', '.html', '.txt'})
actual = [row['source_path'] for row in rows]
assert len(actual) == len(set(actual)) and set(actual) == expected
for row in rows:
    for target in row['target_paths_relative_to_tool_root'].split(' | '):
        assert not Path(target).is_absolute() and '..' not in Path(target).parts
links = 0
for doc in rfc.glob('*.md'):
    for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', doc.read_text()):
        if urlsplit(target).scheme or target.startswith('#'):
            continue
        assert (doc.parent / unquote(target.split('#')[0])).exists(), target
        links += 1
print({'mapped': len(actual), 'links': links})
PY
git diff --check
```

Mermaid 检查使用本地已有的 Playwright、Chromium 和 Mermaid 文件，无需访问网站。将三者位置显式设置为 `PLAYWRIGHT_MODULE`、`CHROME_BIN`、`MERMAID_JS` 后执行：

```bash
node --input-type=module <<'JS'
import fs from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
const root = 'rfc/2026-10-20-kvc-tool-modularization';
const docs = (await fs.readdir(root)).filter(name => name.endsWith('.md'));
const sources = await Promise.all(docs.map(name => fs.readFile(root + '/' + name, 'utf8')));
const blocks = sources.flatMap(text => [...text.matchAll(/^```mermaid\n([\s\S]*?)^```/gm)].map(x => x[1]));
const {chromium} = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const browser = await chromium.launch({executablePath: process.env.CHROME_BIN,
    headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
try {
    const page = await browser.newPage();
    await page.setContent('<!doctype html><html><body></body></html>');
    await page.addScriptTag({path: process.env.MERMAID_JS});
    const rendered = await page.evaluate(async blocks => {
        mermaid.initialize({startOnLoad: false, securityLevel: 'strict', flowchart: {htmlLabels: false}});
        for (let i = 0; i < blocks.length; i++) {
            await mermaid.parse(blocks[i]);
            const result = await mermaid.render('design' + i, blocks[i]);
            if (!result.svg.startsWith('<svg')) throw Error('missing SVG');
        }
        return blocks.length;
    }, blocks);
    if (rendered !== 4) throw Error('expected four diagrams');
    console.log({mermaid_rendered: rendered});
} finally {
    await browser.close();
}
JS
```
