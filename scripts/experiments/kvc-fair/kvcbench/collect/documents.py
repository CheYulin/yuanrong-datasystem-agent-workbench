"""Generate current build, test, script and result documents from bound evidence."""
import json
from pathlib import Path


def generate(root, out, analysis, builds, configuration, coverage):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    run_id = analysis['run_id']
    links = '[构建](build.md) · [用例](test-cases.md) · [脚本](scripts-design.md) · [结果](results.md)'
    build_lines = ['# 1. 本次构建与版本', '', links, '', f'运行标识：`{run_id}`。表中均为原始成功构建命令区间；部署时做了独立重验，本次测量没有重新编译。', '',
        '| 产品 | 实际 tag | commit SHA | 构建 wall 秒 |', '|---|---|---|---:|']
    for row in builds:
        build_lines.append(f"| {row['product']} | {row['tag']} | `{row['sha']}` | {row['build_wall_seconds']:.6f} |")
    build_lines += ['', '构建区间包括依赖、下载/缓存、配置、native 编译、安装和检查；不是纯编译计时或冷缓存排名。LMCache 的 PyTorch 二进制依赖与原生扩展构建分别记录。', '',
        '## 2. 可复现构建入口', '', '源码包包含版本声明、完整配方及依赖锁。先按实际构建主机配置填写 `configs/hosts/build.example.json`，再执行：', '',
        '```bash', 'python3 -m kvcbench build --spec /absolute/build-spec.json', 'python3 -m kvcbench.build.revalidate --root /absolute/native-root --out /absolute/native-root/revalidation/new-attempt', '```', '',
        '原始失败验收保留，重验另生成 metadata/catalog。GCC 15 的第三方兼容 wrapper 使用 C++ 头预包含及 C17，不修改产品 Git 源码；依赖缓存复用明确保留。', '', '## 3. 本次关键配置', '']
    for row in configuration:
        build_lines += ['### ' + row['name'], '', '```json', json.dumps(row['value'], ensure_ascii=False, indent=2), '```', '']
    build_lines += ['## 4. 原件入口', '', '[原始 native 构建证据](../raw/snapshot/payload/native-build/build-result.json) · [部署冻结输入](../raw/snapshot/payload/batch-stage.json)', '']
    (out / 'build.md').write_text('\n'.join(build_lines))
    (out / 'test-cases.md').write_text('\n'.join(['# 1. 测试脚本用例设计', '', links, '',
        '每次调用的独立 key 数为 1 / 16 / 256 / 4096，value=1024 bytes，8 客户端，两个服务端点，3 次重复。key 数是 API 批量大小，不是工作集总数。', '',
        '## 2. 接口与公平边界', '', '| API | 验证 |', '|---|---|', '| PUT | 新 key、固定总量、插入后读回 |', '| UPSERT | 热工作集覆盖、测后恢复 |', '| GET | 顺序、数量、完整 bytes 与抽样全批校验 |', '| EXIST hit/miss | 完整布尔向量与命中/缺失语义 |', '',
        'LMCache 不支持的等价 CPU 原生独立 key 批量 PUT/GET 与 UPSERT 保留 N/A；不使用 scalar 循环冒充原生 batch。只读原生错误保留原件和错误会计，不计入成功性能。', '',
        '## 3. 时序与计时边界', '', '```mermaid', 'sequenceDiagram', '    participant C as 8个客户端', '    participant S as 同节点两个KV端点', '    C->>S: 原生资格与完整bytes验证', '    C->>S: 预加载与warmup', '    Note over C,S: suite观测已就绪', '    loop 测量窗口', '        C->>S: 每次API请求B个独立keys', '        S-->>C: 完整原生返回', '    end', '    C->>S: 测后数据校验', '    Note over C,S: 每个fresh PUT批量使用独立服务epoch', '```', '<p align="center">图 1. 延迟统计覆盖一次原生 API 的整批调用，不除以 B。</p>', '',
        '## 4. 结果规则', '', '三次 distinct repeat 全部有效才计算成功性能的中位数、范围和 CV。缺失与 N/A 不补零；P99 使用合并直方图桶上界并保留低样本标记。资源只使用 SDK monotonic 窗口内的真实样本。', '']))
    (out / 'scripts-design.md').write_text('\n'.join(['# 1. 模块化脚本设计', '', links, '',
        '| 模块 | 职责 |', '|---|---|', '| build | 版本、配方、真实构建、重验、代码包 |', '| run | 部署、产品生命周期、资格、隔离/恢复、原生 worker |', '| observe | suite级采样与独立 ready/final |', '| collect | 锁内冻结、跨节点核验、分析及独立审计 |', '| report | 消费绑定中间结果，生成 ECharts / Markdown网页 / 离线包 |', '| delivery | 本地交付、上传、注册与公开验收 |', '| core/platform | 纯规则、统计和文件/锁/进程/回执 |', '| pipeline | 调用owner验收，再交接下游 |', '',
        '## 2. 工作流', '', '```mermaid', 'flowchart LR', '    A[W01固定计划] --> B[W02构建验收]', '    B --> C[W03代码包]', '    C --> D[W04部署]', '    D --> E[W05预检与W06隔离]', '    E --> F[W07资格与smoke]', '    F --> G[W08观测就绪]', '    G --> H[W09正式矩阵]', '    H --> I[W10停止排空]', '    I --> J[W11恢复]', '    J --> K[W12冻结与接收]', '    K --> L[W13分析与W14审计]', '    L --> M[W15报告]', '    M --> N[W16本地验收]', '    N --> O[W17发布与W18公开验收]', '```', '<p align="center">图 1. 每步提交实际输入、结果、清单、验收、摘要、耗时与最终commit。</p>', '',
        '## 3. 生命周期与失败', '', '共同资源锁跨运行根目录共享；正常下游只消费已接受的绑定。测量不重放、不续写旧 epoch；失败另留 attempt。ready仅证明启动，观测流停止后才封存。W12在停止、恢复及重新排空后冻结，包含原生构建 bytes、版本/命令/源代码身份和原始回执。', '',
        'W13/W14 在收集副本上执行，保留远端路径并显式映射；历史失败验收不改写。W15报告树不可变，W16–W18及其耗时、截图和交付回执另存。', '',
        '## 4. 重放入口', '', '```bash', 'python3 -m kvcbench workflow all --spec /absolute/run-spec.json --control /absolute/new-control', 'python3 -m kvcbench status --workflow /absolute/new-control/workflow', '```', '', '源码包和原始配置为复现依据；运行、分析和报告的源码身份分开保存。', '']))
    counts = analysis['counts']
    results = ['# 1. 测试验证结果', '', links, '', f'运行 `{run_id}`：有效 {counts.get("valid", 0)}，原生失败 {counts.get("failed", 0)}，N/A {counts.get("not_supported", 0)}，缺失 {counts.get("missing", 0)}，无效 {counts.get("invalid", 0)}。', '',
        '执行完整、证据核验与原生接口成功分别记录。构建重验、资格 PASS 或报告发布不改写原生失败。', '', '## 2. 各产品/API/批量结果', '',
        '| 产品 | API | B | 状态 | 有效重复 | keys/s 中位数 | 整批 P99 µs |', '|---|---|---:|---|---:|---:|---:|']
    for row in analysis['aggregates']:
        def value(name):
            number = row['metrics'][name]['median']
            return 'N/A' if row['status'] == 'not_supported' else '未观测' if number is None else f'{number:.6g}'
        results.append(f"| {row['product']} | {row['operation']} | {row['batch']} | {row['status']} | {row['valid_repeats']} | {value('keys_s')} | {value('p99_us')} |")
    results += ['', '## 3. 资源覆盖', '', coverage['scope'], '', f'短窗口的部分采样提示：{len(coverage["findings"])}。内存是已观测峰值；CPU/IO不足两样本保持未知，未插值或补零。', '',
                '[逐 case CSV](../results/summary.csv) · [资源审计](../audits/resources/resource-audit.json) · [TCP审计](../audits/tcp.json) · [部署审计](../audits/deployment.json)', '']
    (out / 'results.md').write_text('\n'.join(results))
    return [out / name for name in ('build.md', 'test-cases.md', 'scripts-design.md', 'results.md')]
