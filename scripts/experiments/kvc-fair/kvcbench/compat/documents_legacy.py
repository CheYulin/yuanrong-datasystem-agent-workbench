#!/usr/bin/env python3
"""Render frozen report Markdown as navigable HTML without changing source evidence."""
import argparse
import gzip
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import unquote, urlsplit, urlunsplit

import markdown
from markdown.extensions.toc import slugify_unicode


LABELS = {'README': '实验说明', 'build': '构建与版本', 'configuration-profile': '精简配置',
          'datasystem': 'DataSystem 接口细节', 'mooncake': 'Mooncake 接口细节',
          'lmcache': 'LMCache 接口细节', 'test-design': '测试用例设计',
          'script-design': '脚本与复现设计', 'report-design': '报告设计',
          'tcp-plan': 'TCP 方案', 'tcp-acceptance': 'TCP 验收',
          'tcp-validation': 'TCP 验证结果', 'validation': '验证结果',
          'verification': '阶段验证记录', 'results': '正式测试结果',
          'resource-results': '资源结果', 'resource-audit': '独立资源审计',
          'timing': '步骤耗时', 'self-review-pure-kv': '配置自检',
          'analysis': '完整统计表', 'isolation-erratum': '历史隔离更正'}
TERM = 'DataSystem 后续架构说明统一使用 Coordinator。历史源码符号、命令和日志保留原名；本文固定 rc44 的实验未新增独立 Coordinator，也未重新运行。Mooncake Master 按该产品原名保留。'
CSS = '''*{box-sizing:border-box}html{scroll-padding-top:88px}body{margin:0;color:#193247;background:#f2f6f9;font:16px/1.85 system-ui,-apple-system,"Segoe UI",sans-serif}a{color:#1265b1;text-decoration:none}a:hover{text-decoration:underline}header:not(#site-nav){position:fixed;inset:0 0 auto;height:64px;z-index:20;background:#fff;border-bottom:1px solid #d6e1ea;display:flex;align-items:center;gap:22px;padding:0 28px}.brand{font-size:20px;font-weight:750;color:#153a52}header:not(#site-nav) span{font-size:14px;color:#547084}header:not(#site-nav) button{display:none}aside{position:fixed;top:64px;bottom:0;left:0;width:270px;background:#fff;overflow:auto;padding:22px 20px;border-right:1px solid #d6e1ea}aside h2{font-size:15px;margin:12px 0}aside a{display:block;padding:5px 8px;font-size:14px;border-radius:5px}aside a[aria-current=page]{background:#e5f0fa;font-weight:700}aside .toc{border-top:1px solid #dce5ed;margin-top:18px;padding-top:12px}aside .toc ul{list-style:none;padding-left:10px}aside .toc li{margin:0}aside .toc a{font-size:12px;padding:3px 0}main{margin:88px 28px 48px 298px;background:#fff;padding:30px 34px;max-width:1460px;min-width:0;overflow-wrap:anywhere;border:1px solid #dce5ed;border-radius:10px}h1{font-size:29px;line-height:1.45;margin:16px 0 24px}h2{font-size:23px;margin-top:34px}h3{font-size:19px;margin-top:25px}h1,h2,h3,h4{scroll-margin-top:90px;overflow-wrap:anywhere}.meta,.notice{padding:12px 16px;margin:14px 0;border-radius:6px;background:#edf5fa;border-left:4px solid #327da6;font-size:14px}.historical{background:#fff2e8;border-color:#a95f20}.small{font-size:13px;color:#52697b}.table-scroll{overflow:auto;max-width:100%;margin:18px 0}table{border-collapse:collapse;min-width:680px;width:100%;font-size:14px}th,td{border:1px solid #cbd9e3;padding:10px 12px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#eaf1f7}pre{background:#152839;color:#ebf3fa;padding:18px;border-radius:7px;overflow:auto;max-width:100%;font-size:13px;line-height:1.65}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}p code,li code,td code{background:#eef3f7;padding:1px 4px;overflow-wrap:anywhere}blockquote{border-left:4px solid #b4cbdc;padding-left:18px;color:#4b6273}figure{margin:22px 0;padding:16px;border:1px solid #dce5ed;border-radius:8px;overflow:auto}figure .mermaid{min-width:550px;text-align:center}figure svg{max-width:100%;height:auto}figcaption{text-align:center;font-size:13px;color:#52697b;margin-top:14px}.doc-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.doc-card{padding:18px;border:1px solid #d2e0ea;border-radius:7px}.doc-card h2{font-size:18px;margin:0 0 8px}.doc-card p{margin:0;font-size:13px;color:#52697b}input{width:100%;padding:12px;font:inherit;border:1px solid #b9ccd9;border-radius:6px;margin:12px 0 22px}[hidden]{display:none!important}.mermaid-error{color:#9b311d;background:#fff1e9;padding:16px}footer{margin-top:36px;padding-top:15px;border-top:1px solid #dce5ed;font-size:13px;color:#52697b}@media(max-width:800px){header:not(#site-nav){height:70px;padding:0 12px;gap:8px;font-size:13px}header:not(#site-nav) span{display:none}header:not(#site-nav) button{display:block;border:1px solid #b8cddc;background:#f5f9fc;border-radius:6px;padding:6px 8px;font-size:13px;color:#1a4766}aside{top:70px;width:min(86vw,320px);z-index:18;box-shadow:3px 0 15px #19324725}aside[aria-hidden=true]{display:none}main{margin:86px 10px 24px;padding:20px 16px;border-radius:6px}h1{font-size:25px}h2{font-size:21px}.doc-cards{grid-template-columns:1fr}.meta,.notice{padding:11px 12px}figure{padding:8px}pre{font-size:12px}}
#site-nav header.yche-legacy-links{position:static;inset:auto;height:auto;min-height:0;z-index:auto;padding:0;box-shadow:none;background:transparent;border:0}@media(max-width:800px){#doc-navigation[aria-hidden=false]{display:block!important}#doc-navigation[aria-hidden=true]{display:none!important}}'''
JS = '''(()=>{const button=document.querySelector('#doc-menu'),nav=document.querySelector('#doc-navigation');const mobile=matchMedia('(max-width:800px)');function set(open){nav.setAttribute('aria-hidden',mobile.matches?String(!open):'false');button.setAttribute('aria-expanded',String(open))}set(false);button.addEventListener('click',()=>set(button.getAttribute('aria-expanded')!=='true'));nav.addEventListener('click',e=>{if(e.target.closest('a')&&mobile.matches)set(false)});mobile.addEventListener('change',()=>set(false));const search=document.querySelector('#doc-search');search?.addEventListener('input',()=>{for(const c of document.querySelectorAll('.doc-card'))c.hidden=!c.textContent.toLowerCase().includes(search.value.toLowerCase())});window.docReady=(async()=>{const nodes=[...document.querySelectorAll('.mermaid')];if(!nodes.length)return;try{mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral',fontFamily:'system-ui',flowchart:{htmlLabels:false}});await mermaid.run({nodes});}catch(error){document.documentElement.dataset.mermaidError=String(error);console.error(error);}})();})();'''


def sha(value):
    return hashlib.sha256(value).hexdigest()


def relative(source, target):
    return os.path.relpath(target, source.parent).replace(os.sep, '/')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mermaid', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Output must be a fresh directory')
    args.output.mkdir(parents=True)
    sources = sorted(args.snapshot.rglob('*.md'))
    targets = {p.resolve(): (args.output / p.relative_to(args.snapshot)).with_suffix('.html') for p in sources}
    inventories = {}
    for p in sources:
        key = p.relative_to(args.snapshot).parts[0]
        inventories.setdefault(key, []).append(p)

    def rewrite_link(value, origin):
        url = urlsplit(html.unescape(value))
        if url.scheme and url.scheme not in ['http', 'https']:
            return value
        if url.netloc and url.netloc != 'yche.me':
            return value
        if url.path.startswith('/perf/'):
            path = args.snapshot / unquote(url.path.removeprefix('/perf/'))
        elif url.path.startswith('/') or url.scheme:
            return value
        else:
            path = origin.parent / unquote(url.path)
        destination = targets.get(path.resolve())
        if not destination:
            return value
        if url.netloc or url.path.startswith('/'):
            new_path = '/perf/' + destination.relative_to(args.output).as_posix()
        else:
            new_path = relative(targets.get(origin.resolve(), args.output / origin.relative_to(args.snapshot)), destination)
        return html.escape(urlunsplit(('', '', new_path, url.query, url.fragment)), quote=True)

    def rewrite(content, source):
        return re.sub(r'(href\s*=\s*["\'])(.*?)(["\'])',
                      lambda m: m[1] + rewrite_link(m[2], source) + m[3], content)

    def wrapper(page, key, title, body, toc='', source=None):
        root = args.output / key
        report = relative(page, root / 'index.html')
        library = relative(page, root / 'docs/index.html')
        links = []
        for doc in inventories[key]:
            target = targets[doc.resolve()]
            current = ' aria-current="page"' if page == target else ''
            links.append(f'<a href="{html.escape(relative(page, target))}"{current}>{html.escape(LABELS.get(doc.stem, doc.stem))}</a>')
        assets = relative(page, root / '_reader')
        meta = ''
        if source:
            original = args.output / source.relative_to(args.snapshot)
            meta = f'<div class="meta">冻结文档的网页阅读版 · <a href="{html.escape(relative(page, original))}" download>下载原始 Markdown</a> · <a href="{library}">文档中心</a><br><span class="small">源文件 SHA256：<code>{sha(source.read_bytes())}</code></span></div>'
        historical = ('<div class="notice historical"><b>历史适用限制：</b>旧 DataSystem 两 Worker 未显式隔离 RocksDB 目录，性能不能作为隔离公平基线。文中 Status 等为当时阶段记录；以<a href="'+relative(page, root/'isolation-erratum.html')+'">隔离更正</a>和实验报告为准。</div>' if key == 'kvc-tcp-20261020' else '')
        term = f'<details class="notice"><summary>DataSystem 术语约定</summary>{html.escape(TERM)}</details>'
        return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} · KVC 实验文档</title><link rel="stylesheet" href="{assets}/reader-{sha(CSS.encode())[:16]}.css"></head><body><header><a class="brand" href="/index.html">yche.me</a><a href="{report}">返回实验报告</a><a href="{library}">文档中心</a><button id="doc-menu" aria-controls="doc-navigation" aria-expanded="false">☰ 文档目录</button></header><aside id="doc-navigation" aria-label="文档导航"><h2>{'旧单 key TCP' if key == 'kvc-tcp-20261020' else 'R7 批量 pure-kv'}</h2>{''.join(links)}{toc}</aside><main>{meta}{historical}{term}{body}<footer>文档网页只调整阅读与跳转；原始 Markdown、构建记录和压测数据保持不变。</footer></main><script src="{assets}/mermaid.min.js"></script><script src="{assets}/reader.js"></script></body></html>'''

    records = []
    for source in sources:
        key = source.relative_to(args.snapshot).parts[0]
        page = targets[source.resolve()]
        md = markdown.Markdown(extensions=['extra', 'toc', 'sane_lists'],
                               extension_configs={'toc': {'slugify': slugify_unicode}})
        body = md.convert(source.read_text())
        counter = [0]

        def diagram(match):
            counter[0] += 1
            return '<figure><div class="mermaid">' + match[1] + '</div><figcaption>图 ' + str(counter[0]) + ' 文档中的流程与交互（Mermaid）</figcaption></figure>'

        body = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>', diagram, body, flags=re.S)
        body = re.sub(r'(<table\b.*?</table>)', r'<div class="table-scroll">\1</div>', body, flags=re.S)
        body = rewrite(body, source)
        toc = '<div class="toc"><h2>本页章节</h2>' + md.toc + '</div>' if md.toc_tokens else ''
        rendered = wrapper(page, key, LABELS.get(source.stem, source.stem), body, toc, source).encode()
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(rendered)
        records.append({'source': source.relative_to(args.snapshot).as_posix(),
                        'source_sha256': sha(source.read_bytes()),
                        'page': page.relative_to(args.output).as_posix(), 'page_sha256': sha(rendered),
                        'mermaid_count': counter[0]})
    for key, docs in inventories.items():
        root = args.output / key
        assets = root / '_reader'
        assets.mkdir()
        (assets / ('reader-' + sha(CSS.encode())[:16] + '.css')).write_text(CSS)
        (assets / 'reader.js').write_text(JS)
        shutil.copyfile(args.mermaid, assets / 'mermaid.min.js')
        page = root / 'docs/index.html'
        cards = []
        for source in docs:
            target = targets[source.resolve()]
            cards.append(f'<article class="doc-card"><h2><a href="{html.escape(relative(page, target))}">{html.escape(LABELS.get(source.stem, source.stem))}</a></h2><p>{html.escape(source.relative_to(args.snapshot/key).as_posix())}</p></article>')
        body = f'<h1>实验文档中心</h1><p>构建、配置、接口细节、验证与复现。共 {len(docs)} 份文档；网页之间直接跳转，源码仍可下载。</p><input id="doc-search" type="search" aria-label="搜索文档" placeholder="搜索构建、配置、验证……"><div class="doc-cards">'+''.join(cards)+'</div>'
        page.write_text(wrapper(page, key, '文档中心', body))
        original = args.snapshot / key / 'index.html'
        text = rewrite(original.read_text(), original)
        marker = '</h1>'
        if text.count(marker) != 1:
            raise ValueError('Ambiguous report hero')
        text = text.replace(marker, marker+'<p><a href="docs/index.html">实验文档中心：构建 / 配置 / 接口细节 / 验证与复现 →</a></p>', 1)
        (root / 'index.html').write_text(text)
    for path in list(args.output.rglob('*')):
        if path.is_file():
            path.with_name(path.name + '.gz').write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    manifest = {'kind': 'frozen_markdown_reading_pages', 'python_markdown_version': markdown.__version__,
                'terminology': TERM, 'documents': records,
                'mermaid_sha256': sha(args.mermaid.read_bytes()),
                'files': {p.relative_to(args.output).as_posix(): sha(p.read_bytes()) for p in sorted(args.output.rglob('*')) if p.is_file()}}
    (args.output / 'reader-build.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'documents': len(records), 'diagrams': sum(x['mermaid_count'] for x in records), 'files': len(manifest['files'])}))


if __name__ == '__main__':
    main()
