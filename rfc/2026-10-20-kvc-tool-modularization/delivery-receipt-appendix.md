# 1. 交付回执附录重建配方

**Status: 配方已记录；本次真实导出和网页验收结果在最终实施记录中另记。**

主报告只包含其生成前已冻结的证据。W14–W18 的终态位于报告树外，避免自引用 hash；此配方在 W18 结束后生成独立附录，不修改主报告或首页注册项。它复用 report.documents 生成说明网页，复制 ECharts 画实际步骤耗时，不启动 SDK、不修改原件。

附录是回执浏览入口，不以仅核对回执自身 SHA 代替上游文件/验证器的完整独立审计；完整验证仍由各步实际实现完成。保留绝对原始引用，source 字段说明其本次位置，主报告复现 ZIP 提供运行/分析原件。

## 2. 使用方式

将下列代码存为 export-delivery-receipts.py，按 --help 传入主报告、派生分析、交付控制和源码根目录，out 必须全新。使用源码中的 requirements-report.txt 固定依赖。

```python
"""Export closed workflow receipts without changing the immutable experiment report."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import sys

parser = argparse.ArgumentParser(description=__doc__)
for name in ('tool-root', 'report', 'derived', 'control', 'out', 'url'):
    parser.add_argument('--' + name, required=True)
parser.add_argument('--extra', action='append', default=[])
args = parser.parse_args()
sys.path.insert(0, args.tool_root)
from kvcbench.report.documents import build

out = Path(args.out).absolute()
out.mkdir(exist_ok=False)
report, derived, control = (Path(getattr(args, name)).resolve(strict=True)
                            for name in ('report', 'derived', 'control'))
payload = json.loads((report / 'data.json').read_text())
assert payload['counts'] == {'valid': 132, 'failed': 18, 'not_supported': 30}
assert payload['run_id'] == 'kvc-native-batch-20261021-coordinator003'
roots = [report / 'raw/snapshot/payload/controller/workflow/steps',
         report / 'raw/snapshot-receipt/steps', report / 'workflow/derived/steps',
         derived / 'workflow/steps', control / 'workflow/steps']
extras = []
if args.extra:
    (out / 'extras').mkdir()
for value in args.extra:
    source = Path(value)
    assert source.is_file() and not source.is_symlink()
    target = out / 'extras' / source.name
    assert not target.exists()
    shutil.copy2(source, target)
    extras.append(target.relative_to(out).as_posix())
extra_links = '<ul>' + ''.join('<li><a href="' + html.escape(name, quote=True) + '">' + html.escape(name) + '</a></li>' for name in extras) + '</ul>'
rows = {}
history = {}
for root in roots:
    for commit in sorted(root.glob('W*/attempt-*/commit.json')):
        value = json.loads(commit.read_text())
        step = value['identity']['step_id']
        # Latest owner wins only for identical run and step identities.
        if step in rows:
            assert rows[step]['run_id'] == value['identity']['run_id']
        target = out / 'receipts' / step / commit.parent.name
        if target.exists():
            for name in [*value['files'], 'commit.json']:
                assert (target / name).read_bytes() == (commit.parent / name).read_bytes()
        else:
            target.mkdir(parents=True)
            for name, expected in value['files'].items():
                source = commit.parent / name
                assert hashlib.sha256(source.read_bytes()).hexdigest() == expected['sha256']
                shutil.copy2(source, target / name)
            shutil.copy2(commit, target / 'commit.json')
        result = json.loads((target / 'result.json').read_text())
        assert result['identity'] == value['identity']
        assert result['duration_s'] == (result['end_mono_ns'] - result['start_mono_ns']) / 1e9
        rows[step] = dict(step=step, attempt=value['identity']['attempt_id'], run_id=value['identity']['run_id'],
            acceptance=value['acceptance'], execution=result['execution'],
            returncode=result['returncode'], duration_s=result['duration_s'],
            source=str(commit), path=target.relative_to(out).as_posix(),
            native_outcomes=result.get('native_outcomes'))
        history[(step, value['identity']['attempt_id'])] = rows[step]
assert all(row['run_id'] == payload['run_id'] for row in rows.values())
assert all('W%02d' % n in rows for n in range(1, 19))
assert all(row['acceptance'] in ('passed', 'passed_with_findings') for row in rows.values())
ordered = sorted(history.values(), key=lambda row: (int(row['step'][1:].split('.')[0]), row['step'], row['attempt']))
(out / 'data.json').write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + '\n')
shutil.copy2(Path(args.tool_root) / 'kvcbench/report/assets/echarts.min.js', out / 'echarts.min.js')
shutil.copy2(__file__, out / 'export-delivery-receipts.py')
links = ('inputs.json', 'result.json', 'artifacts.json', 'acceptance.json', 'summary.md', 'commit.json')
table = []
for row in ordered:
    hrefs = ' · '.join('<a href="' + row['path'] + '/' + name + '">' + name + '</a>' for name in links)
    table.append('<tr><td>' + row['step'] + ' / ' + row['attempt'] + '</td><td>' + row['execution'] + '</td><td>'
        + row['acceptance'] + '</td><td>' + str(row['returncode']) + '</td><td>'
        + f"{row['duration_s']:.3f}" + '</td><td>' + hrefs + '</td></tr>')
canonical = [rows['W%02d' % n] for n in range(1, 19)]
style = '#acceptance-navigation{display:block!important}#acceptance-navigation [data-yche-native-chapter]{display:block!important}body{margin:0;background:#f4f7fb;color:#18354e;font:16px/1.7 system-ui}aside{position:fixed;width:210px;top:0;bottom:0;padding:20px;background:white}aside a{display:block}main{margin-left:250px;padding:24px;max-width:1200px}section{background:white;border:1px solid #dce5ef;border-radius:12px;padding:20px;margin:20px 0;min-width:0}a{color:#1958b5;overflow-wrap:anywhere}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%}td,th{padding:8px;text-align:left;border-bottom:1px solid #ddd}#chart{height:430px;width:100%}figcaption{text-align:center;color:#60738c}code{overflow-wrap:anywhere}@media(max-width:850px){aside{position:static;width:auto}main{margin:0;padding:12px}section{padding:12px}}'
# The appendix has its own immutable manifest, outside the main report hash cycle.
page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>工作流验收与耗时回执</title><style>' + style + '</style></head><body><aside id="acceptance-navigation"><strong>逐步验收</strong><a href="#scope">1. 口径</a><a href="#timings">2. 耗时</a><a href="#receipts">3. 中间结果</a><a href="docs/acceptance.html">4. 设计说明</a><a href="' + html.escape(args.url, quote=True) + '">返回实验主报告</a></aside><main><section id="scope"><h1>1. W01–W18 验收回执</h1><p>执行、证据验收、原生接口结果分别记录。主矩阵：132 有效、18 失败、30 N/A。W09 退出码 1 与 passed_with_findings 同时保留。LMCache：UPSERT全部B均N/A（12条）；PUT/GET仅B1有效，B16/256/4096共18条N/A；EXIST全部B有效。W17 的未压缩上传、压缩准备与较大压缩传输均被操作员中断，失败回执保留；终态以最新 attempt 为准。W18 首次因已知阅读器尾随换行的校验缺陷失败，失败记录保留；后续使用独立记录的新校验源码。</p><p>此附录在 W18 结束后独立生成，不写入主报告，以避免自身验收 hash 循环。逐步回执是原件副本，原绝对引用不改写；完整证据与复现包见主报告。这里重新核对回执 SHA/身份/耗时，不声称独立重跑全部上游审计。</p></section><section id="timings"><h2>2. 各步骤实测耗时</h2><figure><div id="chart"></div><figcaption>图 1. 每步同进程 monotonic 耗时（秒）；步骤边界和嵌套关系不同，不相加为总耗时。</figcaption></figure></section><section id="receipts"><h2>3. 可复核的中间结果</h2><div class="scroll"><table><thead><tr><th>步骤</th><th>执行</th><th>验收</th><th>退出码</th><th>秒</th><th>原始回执</th></tr></thead><tbody>' + ''.join(table) + '</tbody></table></div></section><section><h2>4. 复现与完整性</h2><p><a href="docs/acceptance.html">设计和重建命令</a> · <a href="export-delivery-receipts.py">导出脚本</a> · <a href="data.json">结构化结果</a> · <a href="SHA256SUMS">文件 SHA256 清单</a></p>' + extra_links + '</section></main><script src="echarts.min.js"></script><script>const rows=' + json.dumps(canonical, ensure_ascii=False).replace('</', '<\\/') + ';const chart=echarts.init(document.getElementById("chart"));chart.setOption({grid:{left:65,right:20,bottom:65,top:25},tooltip:{trigger:"axis"},xAxis:{type:"category",data:rows.map(x=>x.step),axisLabel:{rotate:45}},yAxis:{type:"value",name:"秒"},series:[{type:"bar",data:rows.map(x=>x.duration_s),itemStyle:{color:"#267fb5"}}]});addEventListener("resize",()=>chart.resize());</script></body></html>'
(out / 'index.html').write_text(page)
docs = out / 'docs'
docs.mkdir()
document = docs / 'acceptance.md'
document.write_text('# 1. 逐步验收附录的设计\n\n工作流采用 inputs → execution → artifacts → acceptance → committed result，正常下游仅消费验收通过且身份与 hash 匹配的产物。失败 attempt 保留，不自动重放写请求。\n\n## 2. 中间结果\n\n每步可查看输入、执行结果、产物清单、验收结论、摘要与终态 commit；start.json 另保留起始时间。W14–W18 在主报告树外封存，附录只复制其最终回执。每步实际验收实现和完整原始证据见主报告复现包。\n\n## 3. 耗时\n\n各步 duration_s 来自原始同进程 monotonic 时钟。不同主机不相减，嵌套步骤不累加。W02 复用已核验构建；真实源码构建耗时见主报告构建文档。\n\n## 4. 重建\n\n使用主报告代码包内固定的 requirements-report.txt，运行导出脚本，选择全新 out。\n\n```bash\npython3 export-delivery-receipts.py --tool-root <kvc-fair源码> --report <主报告> --derived <W13-W14目录> --control <W15-W18控制目录> --out <新目录> --url ' + args.url + '\n```\n\n导出脚本只读来源；不启动被测服务、不修改原始回执、不执行发布。所需路径对应本次结构化结果 data.json 的 source 字段。\n')
build([document], docs, run_id=canonical[0]['run_id'])
manifest = []
for path in sorted(out.rglob('*')):
    if path.is_file():
        manifest.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.relative_to(out).as_posix())
(out / 'SHA256SUMS').write_text('\n'.join(manifest) + '\n')
print(json.dumps({'out': str(out), 'steps': len(ordered), 'canonical_steps': 18, 'complete': True}))
```

## 3. 附录的浏览器实测脚本

此脚本的实际执行结论在最终记录中列出；不是仅凭代码声明通过。

```javascript
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromium} from '/mnt/c/Users/T14S/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const [source, output] = process.argv.slice(2);
await fs.mkdir(output, {recursive: false});
const base = source.startsWith('https://') ? source.replace(/\/$/, '') + '/' : pathToFileURL(path.resolve(source) + '/').href;
const browser = await chromium.launch({executablePath: '/home/t14s/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome', headless: true, args: ['--no-sandbox']});
const cases = [];
try {
  for (const width of [1440, 390, 320]) {
    const page = await browser.newPage({viewport: {width, height: 950}});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(base + 'index.html', {waitUntil: 'networkidle'});
    await page.waitForFunction(() => window.echarts && echarts.getInstanceByDom(document.querySelector('#chart'))?.getOption().series[0].data.length === 18);
    if (!(await page.locator('aside a').first().isVisible())) throw new Error('Appendix navigation is hidden');
    const state = await page.evaluate(() => ({overflow: document.documentElement.scrollWidth > innerWidth + 1, links: document.querySelectorAll('table a').length, chartWidth: document.querySelector('#chart').getBoundingClientRect().width, caption: getComputedStyle(document.querySelector('figcaption')).textAlign}));
    if (state.overflow || state.links < 108 || state.chartWidth <= 0 || state.caption !== 'center') throw new Error(JSON.stringify(state));
    await page.screenshot({path: path.join(output, 'index-' + width + '.png')});
    await page.goto(base + 'docs/acceptance.html', {waitUntil: 'networkidle'});
    const docOverflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    if (docOverflow || errors.length) throw new Error(JSON.stringify({docOverflow, errors}));
    cases.push({width, passed: true, state, docOverflow, errors});
    await page.close();
  }
} finally {
  await browser.close();
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({passed: cases.length === 3, base, cases}, null, 2) + '\n');
}
```

## 4. 独立附录发布与逐文件公网核验

此脚本的实际执行结论在最终记录中列出；不是仅凭代码声明通过。

```python
"""Publish only a fresh receipt appendix; never change homepage registration."""
import argparse
import json
from pathlib import Path
import shlex
import sys
import uuid

parser = argparse.ArgumentParser(description=__doc__)
for name in ('tool-root', 'source', 'control'):
    parser.add_argument('--' + name, required=True)
args = parser.parse_args()
sys.path.insert(0, args.tool_root)
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest
from kvcbench.delivery.public import fetch, verify_html

source, control = Path(args.source).resolve(strict=True), Path(args.control).absolute()
control.mkdir(exist_ok=False)
host = 'xqyun-32c32g'
destination = '/var/www/html/perf/kvc-coordinator-modular-20261021-acceptance'
base = 'https://yche.me/perf/kvc-coordinator-modular-20261021-acceptance/'
stage = '/var/www/html/perf/.kvc-receipts-' + uuid.uuid4().hex
def command(name, argv):
    result = execute(argv, cwd=control, log=control / (name + '.log'),
                     record=control / (name + '-command.json'), timeout=600)
    if result['returncode'] != 0 or result['error'] is not None:
        raise RuntimeError('Command did not complete: ' + name)

command('upload', ['rsync', '-a', '--chmod=D755,F644', str(source) + '/', host + ':' + stage + '/'])
remote = '''import fcntl,hashlib,json,os,sys
from pathlib import Path
source,destination=map(Path,sys.argv[1:])
assert not any(p.is_symlink() for p in source.rglob('*'))
assert not any(p.is_symlink() for p in [source,*source.parents,destination,*destination.parents])
expected={}
for line in (source/'SHA256SUMS').read_text().splitlines():
 checksum,name=line.split('  ',1)
 assert not Path(name).is_absolute() and '..' not in Path(name).parts and name not in expected
 assert hashlib.sha256((source/name).read_bytes()).hexdigest()==checksum
 expected[name]=checksum
assert set(expected)=={p.relative_to(source).as_posix() for p in source.rglob('*') if p.is_file() and p.name!='SHA256SUMS'}
with open('/tmp/kvc-yche-upload.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 assert not destination.exists() and not destination.is_symlink()
 os.rename(source,destination)
print(json.dumps({'installed':str(destination),'files':len(expected),'homepage_changed':False}))
'''
command('install', ['ssh', host, shlex.join(['python3', '-c', remote, stage, destination])])
records = []
names = [line.split('  ', 1)[1] for line in (source / 'SHA256SUMS').read_text().splitlines()] + ['SHA256SUMS']
for name in names:
    row, content = fetch(base + name, keep=name.endswith('.html'))
    if name.endswith('.html'):
        row['html_projection'] = verify_html((source / name).read_bytes(), content)
    elif row['sha256'] != digest(source / name):
        raise ValueError('Public appendix differs: ' + name)
    records.append(row)
atomic_json(control / 'downloads.json', {'passed': True, 'url': base, 'files': records}, exclusive=True)
print(json.dumps({'passed': True, 'url': base, 'downloads': len(records), 'control': str(control)}))
```

## 5. 本次压缩分块 W17 适配配方

网络实测约1MiB/s，普通7.4GiB上传不能满足原1800秒命令超时。未压缩首次尝试和压缩准备权限检查取消均保留失败回执；本配方作为新的 W17 attempt 使用同一 W16 上游，不重跑 SDK、不改报告字节。8路64MiB分块、整包/分块SHA、安全解包与网页读取权限核对后，复用原 install/publish 完整清单、首页锁/CAS/gzip。此适配脚本是 W17 独立验证源，不冒充主报告源码包007内的原始传输实现。

```python
"""W17 network adapter: compress, split, parallel-upload, verify, install unchanged report."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import shutil
import sys
import uuid

SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
sys.path.insert(0, str(SOURCE))
from kvcbench.core.steps import Check
from kvcbench.delivery import acceptance, install, publish
from kvcbench.pipeline.publication import latest
from kvcbench.pipeline.steps import perform
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json, reference
from kvcbench.platform.receipts import Workflow

control = ROOT / 'coordinator-delivery-001'
workflow = Workflow(control / 'workflow')
parent = latest(workflow, 'W16')
spec = read_json(control / 'publication-spec.json')
assert spec == read_json(ROOT / 'coordinator-publication-spec001.json')
report = Path(read_json(control / 'spec.json')['report'])
out = control / ('compressed-upload-' + uuid.uuid4().hex)
out.mkdir()

def command(name, argv, timeout=3600):
    row = execute(argv, cwd=out, log=out / (name + '.log'),
                  record=out / (name + '-command.json'), timeout=timeout)
    if row['returncode'] != 0 or row['error'] is not None:
        raise ValueError('Transport command did not complete: ' + name)
    return row

def action():
    manifest = acceptance.manifest(report)
    archive = out / 'report.tar.gz'
    command('compress', ['tar', '-I', 'gzip -1', '-cf', str(archive), '-C', str(report), '.'])
    assert digest(report / 'SHA256SUMS') == manifest['manifest_sha256']
    chunks = out / 'chunks'
    chunks.mkdir()
    records = []
    with archive.open('rb') as stream:
        while block := stream.read(64 << 20):
            path = chunks / ('part-%05d' % len(records))
            path.write_bytes(block)
            records.append({'name': path.name, 'bytes': len(block), 'sha256': digest(path)})
    transport = {'schema_version': 1, 'report_manifest_sha256': manifest['manifest_sha256'],
        'archive_sha256': digest(archive), 'archive_bytes': archive.stat().st_size,
        'parallelism': 8, 'chunk_bytes': 64 << 20, 'chunks': records,
        'scope': 'Transport only; original report bytes and metadata are revalidated by the original installer. No SDK run or report regeneration.'}
    atomic_json(out / 'transport.json', transport, exclusive=True)
    host = spec['host']
    remote = spec['site'] + '/perf/.kvc-compressed-' + uuid.uuid4().hex
    ssh = lambda argv: ['ssh', host, shlex.join([str(v) for v in argv])]
    command('mkdir', ssh(['mkdir', remote]))
    def send(row):
        return command('upload-' + row['name'], ['rsync', '-a', str(chunks / row['name']), host + ':' + remote + '/'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, records))
    for name, source in [('transport.json', out / 'transport.json'),
                         ('install.py', Path(install.__file__)), ('publish.py', Path(publish.__file__))]:
        command('upload-' + name, ['scp', str(source), host + ':' + remote + '/' + name])
    unpack = '''import hashlib,json,sys,tarfile
from pathlib import Path
root=Path(sys.argv[1]); spec=json.loads((root/'transport.json').read_text())
archive=root/'report.tar.gz'
with archive.open('xb') as output:
 for row in spec['chunks']:
  part=root/row['name']; assert part.is_file() and not part.is_symlink()
  block=part.read_bytes(); assert len(block)==row['bytes'] and hashlib.sha256(block).hexdigest()==row['sha256']
  output.write(block)
value=hashlib.sha256()
with archive.open('rb') as stream:
 for block in iter(lambda:stream.read(8<<20),b''):value.update(block)
assert value.hexdigest()==spec['archive_sha256'] and archive.stat().st_size==spec['archive_bytes']
target=root/'report'; target.mkdir()
with tarfile.open(archive,'r:gz') as source:
 members=source.getmembers()
 assert all(not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and (m.isfile() or m.isdir()) for m in members)
 source.extractall(target,members=members)
for path in [target,*target.rglob('*')]:path.chmod(0o755 if path.is_dir() else 0o644)
assert hashlib.sha256((target/'SHA256SUMS').read_bytes()).hexdigest()==spec['report_manifest_sha256']
print(json.dumps({'transport_verified':True,'archive_bytes':spec['archive_bytes'],'chunks':len(spec['chunks'])}))
'''
    command('assemble-extract', ssh(['python3', '-c', unpack, remote]))
    command('install-register', ssh(['python3', remote + '/install.py', '--source', remote + '/report',
        '--site', spec['site'], '--relative', spec['relative'], '--title', spec['title'], '--date', spec['date'],
        '--record', remote + '/registration.json', '--publisher', remote + '/publish.py']))
    result = json.loads((out / 'install-register.log').read_text().splitlines()[-1])
    result.update(transport=transport, transport_stage=remote, original_failed_attempt_preserved=True)
    atomic_json(out / 'result.json', result, exclusive=True)
    selected = sorted(p for p in out.iterdir() if p.is_file() and p.suffix in {'.json', '.log'})
    artifacts = [reference(out, p.name) for p in selected]
    return [Check('uploaded_registered_manifest', True, True, True)], artifacts, result

commit = perform(workflow, 'W17', action=action,
    scope='Compressed eight-stream transport of the exact W16-accepted report; chunk/archive SHA; original full-tree installer and fresh homepage CAS/gzip',
    sources=[Path(__file__), Path(install.__file__), Path(publish.__file__)],
    inputs=[control / 'publication-spec.json', ROOT / 'coordinator-upload-cancel-intent001.json', ROOT / 'coordinator-compressed-cancel-intent001.json'], upstream=[parent])
print(json.dumps({'upload_commit': str(commit), 'out': str(out)}))
```

## 6. ZIP 复用的完整重建预验收

三层 tar 中会重复传输 ZIP 与同一批原件；在共享约1MiB/s链路上改为复用 ZIP 内的逐文件字节。下列准备脚本已真实重建全部4776文件并通过原清单验证：59.770644596秒；ZIP1350557425字节，overlay5691017字节。此结果是静态交付验证，不是新的 SDK 结果。

```python
"""Prove reconstruction of the exact accepted report from its ZIP plus a small overlay."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import zipfile

ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
sys.path.insert(0, str(SOURCE))
from kvcbench.delivery.acceptance import manifest
from kvcbench.platform.files import digest, atomic_json
from kvcbench.core.timing import started, finished

begin = started()
report = ROOT / 'coordinator-report-001'
out = ROOT / 'coordinator-zip-transport-plan001'
out.mkdir(exist_ok=False)
overlay = out / 'overlay'
overlay.mkdir()
target = out / 'local-reconstruction'
target.mkdir()
expected = {name: checksum for checksum, name in
            (line.split('  ', 1) for line in (report / 'SHA256SUMS').read_text().splitlines())}
selected = []
with zipfile.ZipFile(report / 'reproduce.zip') as archive:
    names = set(archive.namelist())
    for name, checksum in expected.items():
        if name == 'reproduce.zip':
            continue
        member = 'kvc-fair/report/' + name
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        valid = False
        if member in names:
            value = hashlib.sha256()
            with archive.open(member) as source, destination.open('xb') as stream:
                for block in iter(lambda: source.read(1 << 20), b''):
                    value.update(block)
                    stream.write(block)
            valid = value.hexdigest() == checksum
        if valid:
            selected.append({'path': name, 'member': member, 'sha256': checksum})
        else:
            source = report / name
            copied = overlay / name
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, copied)
            shutil.copy2(source, destination)
    shutil.copy2(report / 'SHA256SUMS', overlay / 'SHA256SUMS')
    shutil.copy2(report / 'SHA256SUMS', target / 'SHA256SUMS')
shutil.copy2(report / 'reproduce.zip', target / 'reproduce.zip')
verified = manifest(target)
assert verified['manifest_sha256'] == digest(report / 'SHA256SUMS')
with tarfile.open(out / 'overlay.tar.gz', 'w:gz', compresslevel=1) as archive:
    for path in sorted(overlay.rglob('*')):
        if path.is_file():
            archive.add(path, arcname=path.relative_to(overlay).as_posix(), recursive=False)
result = {'verified': True, 'report': str(report), 'manifest': verified,
          'archive': str(report / 'reproduce.zip'), 'archive_sha256': digest(report / 'reproduce.zip'),
          'archive_bytes': (report / 'reproduce.zip').stat().st_size,
          'selected': selected, 'overlay_sha256': digest(out / 'overlay.tar.gz'),
          'overlay_bytes': (out / 'overlay.tar.gz').stat().st_size, **finished(begin)}
atomic_json(out / 'plan.json', result, exclusive=True)
print(json.dumps({k: v for k, v in result.items() if k != 'selected'}))
```

## 7. 实际 ZIP 复用 W17 适配与受控取消

最终适配只传原ZIP及小overlay，安全恢复原报告，再调用原installer完整复核。此前较大传输按PID、start_ticks、完整命令、父子归属和独占进程组核对后停止；仅8个自有rsync收到SIGINT，remaining_owned_children为空，原owner封存W17失败回执。未触及SDK、原件和无关进程。

```python
"""W17 network adapter: reuse verified ZIP entries plus overlay, verify and install exact report."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shlex
import shutil
import sys
import uuid

SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
sys.path.insert(0, str(SOURCE))
from kvcbench.core.steps import Check
from kvcbench.delivery import acceptance, install, publish
from kvcbench.pipeline.publication import latest
from kvcbench.pipeline.steps import perform
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json, reference
from kvcbench.platform.receipts import Workflow

control = ROOT / 'coordinator-delivery-001'
workflow = Workflow(control / 'workflow')
parent = latest(workflow, 'W16')
spec = read_json(control / 'publication-spec.json')
assert spec == read_json(ROOT / 'coordinator-publication-spec001.json')
report = Path(read_json(control / 'spec.json')['report'])
out = control / ('compressed-upload-' + uuid.uuid4().hex)
out.mkdir()

def command(name, argv, timeout=3600):
    row = execute(argv, cwd=out, log=out / (name + '.log'),
                  record=out / (name + '-command.json'), timeout=timeout)
    if row['returncode'] != 0 or row['error'] is not None:
        raise ValueError('Transport command did not complete: ' + name)
    return row

def action():
    manifest = acceptance.manifest(report)
    plan = read_json(ROOT / 'coordinator-zip-transport-plan001/plan.json')
    assert plan['verified'] and plan['manifest']['manifest_sha256'] == manifest['manifest_sha256']
    archive = report / 'reproduce.zip'
    assert digest(archive) == plan['archive_sha256']
    chunks = out / 'chunks'
    chunks.mkdir()
    records = []
    with archive.open('rb') as stream:
        while block := stream.read(64 << 20):
            path = chunks / ('part-%05d' % len(records))
            path.write_bytes(block)
            records.append({'name': path.name, 'bytes': len(block), 'sha256': digest(path)})
    transport = {'schema_version': 1, 'report_manifest_sha256': manifest['manifest_sha256'],
        'archive_sha256': digest(archive), 'archive_bytes': archive.stat().st_size,
        'parallelism': 8, 'chunk_bytes': 64 << 20, 'chunks': records,
        'selected': plan['selected'], 'overlay_sha256': plan['overlay_sha256'],
        'scope': 'Transport only; original report bytes and metadata are revalidated by the original installer. No SDK run or report regeneration.'}
    atomic_json(out / 'transport.json', transport, exclusive=True)
    host = spec['host']
    remote = spec['site'] + '/perf/.kvc-compressed-' + uuid.uuid4().hex
    ssh = lambda argv: ['ssh', host, shlex.join([str(v) for v in argv])]
    command('mkdir', ssh(['mkdir', remote]))
    def send(row):
        return command('upload-' + row['name'], ['rsync', '-a', str(chunks / row['name']), host + ':' + remote + '/'])
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, records))
    for name, source in [('transport.json', out / 'transport.json'),
                         ('overlay.tar.gz', ROOT / 'coordinator-zip-transport-plan001/overlay.tar.gz'),
                         ('install.py', Path(install.__file__)), ('publish.py', Path(publish.__file__))]:
        command('upload-' + name, ['scp', str(source), host + ':' + remote + '/' + name])
    unpack = '''import hashlib,json,sys,tarfile,zipfile,shutil
from pathlib import Path
root=Path(sys.argv[1]); spec=json.loads((root/'transport.json').read_text())
archive=root/'reproduce.zip'
with archive.open('xb') as output:
 for row in spec['chunks']:
  part=root/row['name']; assert part.is_file() and not part.is_symlink()
  block=part.read_bytes(); assert len(block)==row['bytes'] and hashlib.sha256(block).hexdigest()==row['sha256']
  output.write(block)
value=hashlib.sha256()
with archive.open('rb') as stream:
 for block in iter(lambda:stream.read(8<<20),b''):value.update(block)
assert value.hexdigest()==spec['archive_sha256'] and archive.stat().st_size==spec['archive_bytes']
target=root/'report'; target.mkdir()
seen=set()
with zipfile.ZipFile(archive) as source:
 for row in spec['selected']:
  name=Path(row['path']); assert not name.is_absolute() and '..' not in name.parts and str(name) not in seen
  assert row['member']=='kvc-fair/report/'+row['path']
  seen.add(str(name)); destination=target/name; destination.parent.mkdir(parents=True,exist_ok=True)
  value=hashlib.sha256()
  with source.open(row['member']) as stream,destination.open('xb') as output:
   for block in iter(lambda:stream.read(1<<20),b''):value.update(block);output.write(block)
  assert value.hexdigest()==row['sha256']
overlay=root/'overlay.tar.gz'
assert hashlib.sha256(overlay.read_bytes()).hexdigest()==spec['overlay_sha256']
with tarfile.open(overlay,'r:gz') as source:
 members=source.getmembers()
 assert all(not Path(m.name).is_absolute() and '..' not in Path(m.name).parts and m.isfile() and m.name not in seen for m in members)
 source.extractall(target,members=members)
shutil.copyfile(archive,target/'reproduce.zip')
for path in [target,*target.rglob('*')]:path.chmod(0o755 if path.is_dir() else 0o644)
assert hashlib.sha256((target/'SHA256SUMS').read_bytes()).hexdigest()==spec['report_manifest_sha256']
print(json.dumps({'transport_verified':True,'archive_bytes':spec['archive_bytes'],'chunks':len(spec['chunks']),'selected':len(spec['selected'])}))
'''
    command('assemble-extract', ssh(['python3', '-c', unpack, remote]))
    command('install-register', ssh(['python3', remote + '/install.py', '--source', remote + '/report',
        '--site', spec['site'], '--relative', spec['relative'], '--title', spec['title'], '--date', spec['date'],
        '--record', remote + '/registration.json', '--publisher', remote + '/publish.py']))
    result = json.loads((out / 'install-register.log').read_text().splitlines()[-1])
    result.update(transport=transport, transport_stage=remote, original_failed_attempt_preserved=True)
    atomic_json(out / 'result.json', result, exclusive=True)
    selected = sorted(p for p in out.iterdir() if p.is_file() and p.suffix in {'.json', '.log'})
    artifacts = [reference(out, p.name) for p in selected]
    return [Check('uploaded_registered_manifest', True, True, True)], artifacts, result

commit = perform(workflow, 'W17', action=action,
    scope='ZIP reuse and small overlay transport of the exact W16-accepted report; chunk/archive SHA; original full-tree installer and fresh homepage CAS/gzip',
    sources=[Path(__file__), Path(install.__file__), Path(publish.__file__)],
    inputs=[control / 'publication-spec.json', ROOT / 'coordinator-upload-cancel-intent001.json', ROOT / 'coordinator-compressed-cancel-intent001.json', ROOT / 'coordinator-zip-transport-plan001/plan.json', ROOT / 'coordinator-zip-switch-result001.json'], upstream=[parent])
print(json.dumps({'upload_commit': str(commit), 'out': str(out)}))
```

```python
"""Cancel only the exact owned network adapter and its verified rsync children."""
import json
import os
from pathlib import Path
import signal
import time

ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
pid = 432250
parent = Path('/proc') / str(pid)
expected = [b'python3', b'-B', str(ROOT / 'coordinator-chunked-publish-002.py').encode()]
assert (parent / 'cmdline').read_bytes().split(b'\0')[:-1] == expected
start = (parent / 'stat').read_text().split(') ', 1)[1].split()[19]
intent = {'pid': pid, 'start_ticks': start, 'reason': 'Measured aggregate site bandwidth remains ~1MiB/s. Fully verified ZIP+overlay reconstruction cuts transfer from 2.89GB to 1.36GB with identical 4776-file manifest; cancel only unfinished transport, no installation/registration occurred.', 'wall_ns': time.time_ns()}
with (ROOT / 'coordinator-zip-switch-intent001.json').open('x') as stream:
    json.dump(intent, stream, indent=2)
os.kill(pid, signal.SIGINT)
records = []
for _ in range(240):
    active = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / 'stat').read_text().split(') ', 1)[1].split()
            if stat[1] != str(pid):
                continue
            cmd = (entry / 'cmdline').read_bytes().split(b'\0')[:-1]
            if len(cmd) != 4 or cmd[:2] != [b'rsync', b'-a']:
                continue
            assert cmd[2].startswith(str(ROOT / 'coordinator-delivery-001/compressed-upload-21c155c7a44440ccb32c0a2b736213d6/chunks/part-').encode())
            assert cmd[3] == b'xqyun-32c32g:/var/www/html/perf/.kvc-compressed-243ae27fdc03475fa698b0c0be049ff7/'
            child = int(entry.name)
            assert os.getpgid(child) == child
            active.append(child)
            if child not in [row['pid'] for row in records]:
                records.append({'pid': child, 'start_ticks': stat[19], 'argv': [x.decode() for x in cmd]})
                os.killpg(child, signal.SIGINT)
        except (FileNotFoundError, ProcessLookupError):
            pass
    if not active:
        break
    time.sleep(.25)
with (ROOT / 'coordinator-zip-switch-result001.json').open('x') as stream:
    json.dump({'intent': intent, 'signals': records, 'remaining_owned_children': active, 'end_wall_ns': time.time_ns()}, stream, indent=2)
assert not active
print(json.dumps({'signals': len(records), 'remaining_owned_children': active}))
```

## 8. 单次后续交付配方

等待已核对PID/start_ticks的当前W17 owner真正终止，只有已封存的第四次W17通过才执行W18。随后从实际W18产物提取公开验收与浏览器结果，构建独立附录、本地浏览器验收、发布并逐文件核对、公开浏览器验收。所有命令有独立日志/耗时，不自动重试或重跑SDK。此为本次运行的显式操作配方；重新实验须使用新的run/root/control/进程身份，不能机械重复这些固定输出路径。

```python
"""Finish accepted delivery steps exactly once; never retry SDK or failed attempts."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
sys.path.insert(0, str(SOURCE))
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json
from kvcbench.core.timing import started, finished

control = ROOT / 'coordinator-delivery-001'
out = ROOT / 'coordinator-delivery-tail-command001'
out.mkdir(exist_ok=False)
begin = started()
pid = 432670
proc = Path('/proc') / str(pid)
expected = [b'python3', b'-B', str(ROOT / 'coordinator-zip-publish-001.py').encode()]
assert (proc / 'cmdline').read_bytes().split(b'\0')[:-1] == expected
ticks = (proc / 'stat').read_text().split(') ', 1)[1].split()[19]
atomic_json(out / 'start.json', {'pid': pid, 'start_ticks': ticks,
    'source_sha256': digest(__file__), 'purpose': 'Wait for exact W17 owner terminal; then explicit W18 and separate receipt appendix.', **begin}, exclusive=True)

def accepted(step):
    root = control / 'workflow/steps' / step
    assert not list(root.glob('.partial-*'))
    candidates = sorted(root.glob('attempt-*/commit.json'))
    assert candidates
    commit = read_json(candidates[-1])
    assert commit['acceptance'] in ('passed', 'passed_with_findings')
    return candidates[-1]

def command(name, argv):
    row = execute(argv, cwd=SOURCE, log=out / (name + '.log'),
        record=out / (name + '-command.json'), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=10800)
    if row['returncode'] != 0 or row['error'] is not None:
        raise RuntimeError('Step stopped without acceptance: ' + name)
    return row

try:
    while proc.exists():
        try:
            stat = (proc / 'stat').read_text().split(') ', 1)[1].split()
            if stat[19] != ticks or stat[0] == 'Z':
                break
        except FileNotFoundError:
            break
        if time.monotonic_ns() - begin['start_mono_ns'] > 10800 * 10**9:
            raise TimeoutError('Original W17 owner remains live; no restart attempted')
        time.sleep(5)
    w17 = accepted('W17')
    assert w17.parent.name == 'attempt-000004'
    command('check-main-publication', [sys.executable, '-B', ROOT / 'coordinator-final-delivery-001.py', 'check'])
    w18 = accepted('W18')
    artifacts = read_json(w18.parent / 'artifacts.json')['files']
    downloads = [Path(row['root']) / row['path'] for row in artifacts if row['path'] == 'downloads.json']
    assert len(downloads) == 1
    public_out = downloads[0].parent
    assert read_json(public_out / 'browser/result.json')['passed'] is True
    extra = ROOT / 'coordinator-appendix-extras-001'
    extra.mkdir()
    names = ['coordinator-upload-cancel-intent001.json', 'coordinator-compressed-cancel-intent001.json',
        'coordinator-zip-switch-intent001.json', 'coordinator-zip-switch-result001.json',
        'coordinator-zip-remote-probe001-result.json', 'p6-source-bridge-005-007.json',
        'p6-hotpath-equivalence-001.json', 'p6-layout-bundle-007.json',
        'coordinator-chunked-publish-001.py', 'coordinator-chunked-publish-002.py',
        'coordinator-zip-publish-001.py', 'prepare-zip-transport-001.py',
        'cancel-compressed-transport-001.py', 'publish-receipt-appendix.py',
        'receipt-appendix-browser.mjs', 'coordinator-final-delivery-001.py',
        'coordinator-delivery-tail-001.py']
    records = []
    def copy(source, name):
        target = extra / name
        assert source.is_file() and not source.is_symlink() and not target.exists()
        shutil.copy2(source, target)
        assert digest(source) == digest(target)
        records.append({'source': str(source), 'copy': name, 'sha256': digest(target)})
    for name in names:
        copy(ROOT / name, name)
    for source, name in [
        (ROOT / 'coordinator-zip-transport-plan001/plan.json', 'zip-reconstruction-plan.json'),
        (ROOT / 'coordinator-derive-command003/derive-command.json', 'w13-w14-command.json'),
        (ROOT / 'coordinator-report-command001/report-command.json', 'w15-w16-command.json'),
        (ROOT / 'coordinator-check-command001/check-command.json', 'w18-command.json'),
        (control / 'local-acceptance/result.json', 'w16-local-acceptance.json'),
        (public_out / 'downloads.json', 'w18-public-downloads.json'),
        (public_out / 'browser/result.json', 'w18-browser-result.json'),
    ]:
        copy(source, name)
    for width in (1440, 390, 320):
        copy(public_out / 'browser' / ('report-' + str(width) + '.png'), 'w18-report-' + str(width) + '.png')
    atomic_json(extra / 'extras-index.json', {'files': records}, exclusive=True)
    appendix = ROOT / 'coordinator-acceptance-site-001'
    argv = [sys.executable, '-B', ROOT / 'export-delivery-receipts.py', '--tool-root', SOURCE,
        '--report', ROOT / 'coordinator-report-001', '--derived', ROOT / 'coordinator-derived-003',
        '--control', control, '--out', appendix,
        '--url', 'https://yche.me/perf/kvc-coordinator-modular-20261021/index.html']
    for path in sorted(extra.iterdir()):
        argv.extend(['--extra', path])
    command('export-appendix', argv)
    command('browser-appendix-local', ['node', ROOT / 'receipt-appendix-browser.mjs', appendix,
        ROOT / 'coordinator-appendix-local-browser-001'])
    command('publish-appendix', [sys.executable, '-B', ROOT / 'publish-receipt-appendix.py',
        '--tool-root', SOURCE, '--source', appendix, '--control', ROOT / 'coordinator-appendix-publication-001'])
    command('browser-appendix-public', ['node', ROOT / 'receipt-appendix-browser.mjs',
        'https://yche.me/perf/kvc-coordinator-modular-20261021-acceptance/',
        ROOT / 'coordinator-appendix-public-browser-001'])
    result = {'passed': True, 'main_public_commit': str(w18), 'appendix': str(appendix)}
except BaseException as error:
    result = {'passed': False, 'error': str(error), 'type': type(error).__name__}
    raise
finally:
    atomic_json(out / 'result.json', {**result, **finished(begin)}, exclusive=True)
print(json.dumps(result))
```

## 9. 精确 HTML 尾部校验修正与第二次 W18 后续配方

第一次 W18 真实失败：主index通过，5个原件无末尾换行的docs页面仅多出阅读器固定尾部所带换行。捕获原件/served并逐字节定位后，仅移除精确组件与其伴随换行，不做一般空白归一化。4项针对性测试覆盖原件0/1/2末尾换行及额外尾部/正文空白拒绝；全量580项通过57.975秒。已发布主报告/ZIP不改，W18独立记录新validator源码；首次failed/尾随脚本失败记录不覆盖。

```python
"""One explicit W18 attempt after the measured HTML tail-component fix."""
import json
import os
from pathlib import Path
import sys

SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
sys.path.insert(0, str(SOURCE))
from kvcbench.platform.command import execute

out = ROOT / 'coordinator-check-command002'
row = execute(['/usr/bin/time', '-v', sys.executable, '-B', '-m', 'kvcbench', 'check-publication',
    '--control', ROOT / 'coordinator-delivery-001'], cwd=SOURCE,
    log=out / 'check.log', record=out / 'check-command.json',
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=7200)
print(json.dumps(row))
sys.exit(0 if row['returncode'] == 0 and row['error'] is None else 1)
```

```python
"""Finish accepted delivery steps exactly once; never retry SDK or failed attempts."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
sys.path.insert(0, str(SOURCE))
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json
from kvcbench.core.timing import started, finished

control = ROOT / 'coordinator-delivery-001'
out = ROOT / 'coordinator-delivery-tail-command002'
out.mkdir(exist_ok=False)
begin = started()
pid = 435923
proc = Path('/proc') / str(pid)
expected = [b'python3', b'-B', str(ROOT / 'coordinator-check-publication-002.py').encode()]
assert (proc / 'cmdline').read_bytes().split(b'\0')[:-1] == expected
ticks = (proc / 'stat').read_text().split(') ', 1)[1].split()[19]
atomic_json(out / 'start.json', {'pid': pid, 'start_ticks': ticks,
    'source_sha256': digest(__file__), 'purpose': 'Wait for exact second W18 owner terminal; export only accepted closed receipts and separate appendix.', **begin}, exclusive=True)

def accepted(step):
    root = control / 'workflow/steps' / step
    assert not list(root.glob('.partial-*'))
    candidates = sorted(root.glob('attempt-*/commit.json'))
    assert candidates
    commit = read_json(candidates[-1])
    assert commit['acceptance'] in ('passed', 'passed_with_findings')
    return candidates[-1]

def command(name, argv):
    row = execute(argv, cwd=SOURCE, log=out / (name + '.log'),
        record=out / (name + '-command.json'), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=10800)
    if row['returncode'] != 0 or row['error'] is not None:
        raise RuntimeError('Step stopped without acceptance: ' + name)
    return row

try:
    while proc.exists():
        try:
            stat = (proc / 'stat').read_text().split(') ', 1)[1].split()
            if stat[19] != ticks or stat[0] == 'Z':
                break
        except FileNotFoundError:
            break
        if time.monotonic_ns() - begin['start_mono_ns'] > 10800 * 10**9:
            raise TimeoutError('Second W18 owner remains live; no restart attempted')
        time.sleep(5)
    w17 = accepted('W17')
    assert w17.parent.name == 'attempt-000004'
    w18 = accepted('W18')
    assert w18.parent.name == 'attempt-000002'
    artifacts = read_json(w18.parent / 'artifacts.json')['files']
    downloads = [Path(row['root']) / row['path'] for row in artifacts if row['path'] == 'downloads.json']
    assert len(downloads) == 1
    public_out = downloads[0].parent
    assert read_json(public_out / 'browser/result.json')['passed'] is True
    extra = ROOT / 'coordinator-appendix-extras-001'
    extra.mkdir()
    names = ['coordinator-upload-cancel-intent001.json', 'coordinator-compressed-cancel-intent001.json',
        'coordinator-zip-switch-intent001.json', 'coordinator-zip-switch-result001.json',
        'coordinator-zip-remote-probe001-result.json', 'p6-source-bridge-005-007.json',
        'p6-hotpath-equivalence-001.json', 'p6-layout-bundle-007.json',
        'coordinator-chunked-publish-001.py', 'coordinator-chunked-publish-002.py',
        'coordinator-zip-publish-001.py', 'prepare-zip-transport-001.py',
        'cancel-compressed-transport-001.py', 'publish-receipt-appendix.py',
        'receipt-appendix-browser.mjs', 'coordinator-final-delivery-001.py',
        'coordinator-delivery-tail-001.py', 'coordinator-delivery-tail-002.py',
        'coordinator-check-publication-002.py']
    records = []
    def copy(source, name):
        target = extra / name
        assert source.is_file() and not source.is_symlink() and not target.exists()
        shutil.copy2(source, target)
        assert digest(source) == digest(target)
        records.append({'source': str(source), 'copy': name, 'sha256': digest(target)})
    for name in names:
        copy(ROOT / name, name)
    for source, name in [
        (ROOT / 'coordinator-zip-transport-plan001/plan.json', 'zip-reconstruction-plan.json'),
        (ROOT / 'coordinator-derive-command003/derive-command.json', 'w13-w14-command.json'),
        (ROOT / 'coordinator-report-command001/report-command.json', 'w15-w16-command.json'),
        (ROOT / 'coordinator-check-command002/check-command.json', 'w18-command.json'),
        (ROOT / 'coordinator-public-html-diagnostic001/result.json', 'w18-first-html-diagnostic.json'),
        (ROOT / 'coordinator-public-html-recheck001.json', 'w18-html-recheck.json'),
        (ROOT / 'public-html-final-all-003.log', 'unit-regression-580.log'),
        (control / 'local-acceptance/result.json', 'w16-local-acceptance.json'),
        (public_out / 'downloads.json', 'w18-public-downloads.json'),
        (public_out / 'browser/result.json', 'w18-browser-result.json'),
    ]:
        copy(source, name)
    for n, record in enumerate(read_json(w18.parent / 'acceptance.json')['validator']['sources']):
        source = Path(record['root']) / record['path']
        assert digest(source) == record['sha256']
        copy(source, 'w18-validator-' + str(n) + '-' + source.name)
    for width in (1440, 390, 320):
        copy(public_out / 'browser' / ('report-' + str(width) + '.png'), 'w18-report-' + str(width) + '.png')
    atomic_json(extra / 'extras-index.json', {'files': records}, exclusive=True)
    appendix = ROOT / 'coordinator-acceptance-site-001'
    argv = [sys.executable, '-B', ROOT / 'export-delivery-receipts.py', '--tool-root', SOURCE,
        '--report', ROOT / 'coordinator-report-001', '--derived', ROOT / 'coordinator-derived-003',
        '--control', control, '--out', appendix,
        '--url', 'https://yche.me/perf/kvc-coordinator-modular-20261021/index.html']
    for path in sorted(extra.iterdir()):
        argv.extend(['--extra', path])
    command('export-appendix', argv)
    command('browser-appendix-local', ['node', ROOT / 'receipt-appendix-browser.mjs', appendix,
        ROOT / 'coordinator-appendix-local-browser-001'])
    command('publish-appendix', [sys.executable, '-B', ROOT / 'publish-receipt-appendix.py',
        '--tool-root', SOURCE, '--source', appendix, '--control', ROOT / 'coordinator-appendix-publication-001'])
    command('browser-appendix-public', ['node', ROOT / 'receipt-appendix-browser.mjs',
        'https://yche.me/perf/kvc-coordinator-modular-20261021-acceptance/',
        ROOT / 'coordinator-appendix-public-browser-001'])
    result = {'passed': True, 'main_public_commit': str(w18), 'appendix': str(appendix)}
except BaseException as error:
    result = {'passed': False, 'error': str(error), 'type': type(error).__name__}
    raise
finally:
    atomic_json(out / 'result.json', {**result, **finished(begin)}, exclusive=True)
print(json.dumps(result))
```

## 10. 第三次W18：全量精确Range及后续封存

规范模块为delivery/range_download.py，原报告包007不重写。普通GET失败、宿主机完整回读、精确Range探测及588项回归详见实施记录第31节。以下配方一次执行新W18；只有全部字节与总SHA、浏览器及首页检查完成才能封存通过并输出附录。下载增补源码由实际已封存validator-sources生成，不宣称旧包007含后续修正。

```python
"""One explicit W18 attempt using strict full-file segmented archive transfer."""
import json
import os
from pathlib import Path
import sys

SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
sys.path.insert(0, str(SOURCE))
from kvcbench.platform.command import execute

out = ROOT / 'coordinator-check-command003'
row = execute(['/usr/bin/time', '-v', sys.executable, '-B', '-m', 'kvcbench', 'check-publication',
    '--control', ROOT / 'coordinator-delivery-001'], cwd=SOURCE,
    log=out / 'check.log', record=out / 'check-command.json',
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=7200)
print(json.dumps(row))
sys.exit(0 if row['returncode'] == 0 and row['error'] is None else 1)
```

```python
"""Finish accepted delivery steps exactly once; never retry SDK or failed attempts."""
import json
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile

ROOT = Path('/home/t14s/workspace/scratch/2026-10-21-kvc-refactor')
SOURCE = Path('/home/t14s/workspace/git-repos/yuanrong-datasystem-agent-workbench/scripts/experiments/kvc-fair')
sys.path.insert(0, str(SOURCE))
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, digest, read_json
from kvcbench.core.timing import started, finished

control = ROOT / 'coordinator-delivery-001'
out = ROOT / 'coordinator-delivery-tail-command003'
out.mkdir(exist_ok=False)
begin = started()
pid = 437573
proc = Path('/proc') / str(pid)
expected = [b'python3', b'-B', str(ROOT / 'coordinator-check-publication-003.py').encode()]
assert (proc / 'cmdline').read_bytes().split(b'\0')[:-1] == expected
ticks = (proc / 'stat').read_text().split(') ', 1)[1].split()[19]
atomic_json(out / 'start.json', {'pid': pid, 'start_ticks': ticks,
    'source_sha256': digest(__file__), 'purpose': 'Wait for exact third W18 owner terminal; export only accepted closed receipts and separate appendix.', **begin}, exclusive=True)

def accepted(step):
    root = control / 'workflow/steps' / step
    assert not list(root.glob('.partial-*'))
    candidates = sorted(root.glob('attempt-*/commit.json'))
    assert candidates
    commit = read_json(candidates[-1])
    assert commit['acceptance'] in ('passed', 'passed_with_findings')
    return candidates[-1]

def command(name, argv):
    row = execute(argv, cwd=SOURCE, log=out / (name + '.log'),
        record=out / (name + '-command.json'), env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=10800)
    if row['returncode'] != 0 or row['error'] is not None:
        raise RuntimeError('Step stopped without acceptance: ' + name)
    return row

try:
    while proc.exists():
        try:
            stat = (proc / 'stat').read_text().split(') ', 1)[1].split()
            if stat[19] != ticks or stat[0] == 'Z':
                break
        except FileNotFoundError:
            break
        if time.monotonic_ns() - begin['start_mono_ns'] > 10800 * 10**9:
            raise TimeoutError('Third W18 owner remains live; no restart attempted')
        time.sleep(5)
    w17 = accepted('W17')
    assert w17.parent.name == 'attempt-000004'
    w18 = accepted('W18')
    assert w18.parent.name == 'attempt-000003'
    artifacts = read_json(w18.parent / 'artifacts.json')['files']
    downloads = [Path(row['root']) / row['path'] for row in artifacts if row['path'] == 'downloads.json']
    assert len(downloads) == 1
    public_out = downloads[0].parent
    assert read_json(public_out / 'browser/result.json')['passed'] is True
    extra = ROOT / 'coordinator-appendix-extras-001'
    extra.mkdir()
    names = ['coordinator-upload-cancel-intent001.json', 'coordinator-compressed-cancel-intent001.json',
        'coordinator-zip-switch-intent001.json', 'coordinator-zip-switch-result001.json',
        'coordinator-zip-remote-probe001-result.json', 'p6-source-bridge-005-007.json',
        'p6-hotpath-equivalence-001.json', 'p6-layout-bundle-007.json',
        'coordinator-chunked-publish-001.py', 'coordinator-chunked-publish-002.py',
        'coordinator-zip-publish-001.py', 'prepare-zip-transport-001.py',
        'cancel-compressed-transport-001.py', 'publish-receipt-appendix.py',
        'receipt-appendix-browser.mjs', 'coordinator-final-delivery-001.py',
        'coordinator-delivery-tail-001.py', 'coordinator-delivery-tail-002.py',
        'coordinator-check-publication-002.py', 'coordinator-check-publication-003.py',
        'coordinator-delivery-tail-003.py']
    records = []
    def copy(source, name):
        target = extra / name
        assert source.is_file() and not source.is_symlink() and not target.exists()
        shutil.copy2(source, target)
        assert digest(source) == digest(target)
        records.append({'source': str(source), 'copy': name, 'sha256': digest(target)})
    for name in names:
        copy(ROOT / name, name)
    for source, name in [
        (ROOT / 'coordinator-zip-transport-plan001/plan.json', 'zip-reconstruction-plan.json'),
        (ROOT / 'coordinator-derive-command003/derive-command.json', 'w13-w14-command.json'),
        (ROOT / 'coordinator-report-command001/report-command.json', 'w15-w16-command.json'),
        (ROOT / 'coordinator-check-command003/check-command.json', 'w18-command.json'),
        (ROOT / 'coordinator-public-html-diagnostic001/result.json', 'w18-first-html-diagnostic.json'),
        (ROOT / 'coordinator-public-html-recheck001.json', 'w18-html-recheck.json'),
        (ROOT / 'public-ranges-all-004.log', 'unit-regression-final.log'),
        (control / 'local-acceptance/result.json', 'w16-local-acceptance.json'),
        (public_out / 'downloads.json', 'w18-public-downloads.json'),
        (public_out / 'range-progress.jsonl', 'w18-range-progress.jsonl'),
        (ROOT / 'coordinator-zip-http-diagnostic001/result.json', 'w18-zip-http-diagnostic.json'),
        (ROOT / 'coordinator-zip-http-diagnostic001/no-cache-ranges.json', 'w18-no-cache-range-probes.json'),
        (public_out / 'browser/result.json', 'w18-browser-result.json'),
    ]:
        copy(source, name)
    validator_sources = read_json(w18.parent / 'acceptance.json')['validator']['sources']
    overlay = ROOT / 'coordinator-w18-validator-overlay001.zip'
    overlay_manifest = []
    with zipfile.ZipFile(overlay, 'x', zipfile.ZIP_DEFLATED) as archive:
        for n, record in enumerate(validator_sources):
            source = Path(record['root']) / record['path']
            assert digest(source) == record['sha256']
            copy(source, 'w18-validator-' + str(n) + '-' + source.name)
            relative = Path(record['original_path']).relative_to(SOURCE)
            assert relative.parts[0] in ('kvcbench', 'configs') and '..' not in relative.parts
            archive.write(source, relative.as_posix())
            overlay_manifest.append({'path': relative.as_posix(), 'sha256': record['sha256'],
                                     'bytes': record['bytes'], 'mode': record['mode']})
        archive.writestr('W18-VALIDATOR-MANIFEST.json', json.dumps({'run_id': read_json(w18)['identity']['run_id'],
            'source_bundle_sha256': read_json(ROOT / 'p6-layout-bundle-007.json')['archive_sha256'],
            'scope': 'Actual closed W18 verification sources only; original SDK/runtime/analysis/report package007 stays immutable.',
            'files': overlay_manifest}, ensure_ascii=False, indent=2) + '\n')
        archive.writestr('W18-VALIDATOR-README.md', '# W18 公网验证源码增补\n\n在新的目录解压原源码包，再把本增补ZIP解压到kvc-fair根目录。增补只用于本次公网验证和分段下载，不改原始实验ZIP、SDK和数值结果。\n\n原代码包：https://yche.me/perf/kvc-coordinator-modular-20261021/provenance/source-bundle.zip\n\n在该kvc-fair目录执行（输出须为新路径）：\n\n```bash\npython3 -B -m kvcbench.delivery.range_download --url "https://yche.me/perf/kvc-coordinator-modular-20261021/reproduce.zip?sha256=ec27f4b92bd0b00006e095ea3351b284b551d8cb53b2f4d4e005958b2bf4e17c" --size 1350557425 --sha256 ec27f4b92bd0b00006e095ea3351b284b551d8cb53b2f4d4e005958b2bf4e17c --out /absolute/new-reproduce.zip\n```\n\n每段必须为206且Content-Range/长度精确，完整SHA匹配才发布输出；失败保留.partial，不覆盖现有文件。新实验使用workbench主干源码。\n')
    copy(overlay, 'w18-validator-overlay.zip')
    for width in (1440, 390, 320):
        copy(public_out / 'browser' / ('report-' + str(width) + '.png'), 'w18-report-' + str(width) + '.png')
    atomic_json(extra / 'extras-index.json', {'files': records}, exclusive=True)
    appendix = ROOT / 'coordinator-acceptance-site-001'
    argv = [sys.executable, '-B', ROOT / 'export-delivery-receipts.py', '--tool-root', SOURCE,
        '--report', ROOT / 'coordinator-report-001', '--derived', ROOT / 'coordinator-derived-003',
        '--control', control, '--out', appendix,
        '--url', 'https://yche.me/perf/kvc-coordinator-modular-20261021/index.html']
    for path in sorted(extra.iterdir()):
        argv.extend(['--extra', path])
    command('export-appendix', argv)
    command('browser-appendix-local', ['node', ROOT / 'receipt-appendix-browser.mjs', appendix,
        ROOT / 'coordinator-appendix-local-browser-001'])
    command('publish-appendix', [sys.executable, '-B', ROOT / 'publish-receipt-appendix.py',
        '--tool-root', SOURCE, '--source', appendix, '--control', ROOT / 'coordinator-appendix-publication-001'])
    command('browser-appendix-public', ['node', ROOT / 'receipt-appendix-browser.mjs',
        'https://yche.me/perf/kvc-coordinator-modular-20261021-acceptance/',
        ROOT / 'coordinator-appendix-public-browser-001'])
    result = {'passed': True, 'main_public_commit': str(w18), 'appendix': str(appendix)}
except BaseException as error:
    result = {'passed': False, 'error': str(error), 'type': type(error).__name__}
    raise
finally:
    atomic_json(out / 'result.json', {**result, **finished(begin)}, exclusive=True)
print(json.dumps(result))
```
