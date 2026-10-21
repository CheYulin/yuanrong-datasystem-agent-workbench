"""Render explicit frozen Markdown documents into local linked HTML pages."""
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
from urllib.parse import unquote, urlsplit, urlunsplit

ASSETS = Path(__file__).with_name('assets')


def build(sources, output, *, run_id):
    import markdown
    from markdown.extensions.toc import slugify_unicode
    if markdown.__version__ != '3.10.2':
        raise ValueError('Install the pinned requirements-report.txt for document rendering')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    assets = output / '_reader'
    assets.mkdir()
    for source, target in [('documents.css', 'reader.css'), ('documents.js', 'reader.js'), ('mermaid.min.js', 'mermaid.min.js')]:
        shutil.copy2(ASSETS / source, assets / target)
    sources = [Path(source).resolve(strict=True) for source in sources]
    targets = {source: source.stem + '.html' for source in sources}
    navigation = ''.join('<a href="' + html.escape(name, quote=True) + '">' + html.escape(source.stem) + '</a>'
                         for source, name in targets.items())
    records = []
    def wrapper(title, body, toc=''):
        return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(title) + '</title><link rel="stylesheet" href="_reader/reader.css?v=layout-20261021-02"></head><body><header><a class="brand" href="../index.html">实验报告</a><a href="index.html">文档中心</a><button id="doc-menu" aria-controls="doc-navigation" aria-expanded="false">文档目录</button></header><aside id="doc-navigation"><h2>' + html.escape(run_id) + '</h2>' + navigation + toc + '</aside><main>' + body + '</main><script src="_reader/mermaid.min.js"></script><script src="_reader/reader.js"></script></body></html>'
    for source in sources:
        original = source.read_bytes()
        copied = output / source.name
        if copied.exists() and copied.read_bytes() != original:
            raise ValueError('Frozen document differs from the selected source')
        if not copied.exists():
            shutil.copy2(source, copied)
        md = markdown.Markdown(extensions=['extra', 'toc', 'sane_lists'],
            extension_configs={'toc': {'slugify': slugify_unicode}})
        body = md.convert(original.decode())
        body = re.sub(r'<table>.*?</table>', lambda match: '<div class="table-scroll">' + match[0] + '</div>', body, flags=re.S)
        diagrams = [0]
        def diagram(match):
            diagrams[0] += 1
            caption = match[2] or ('图 ' + str(diagrams[0]) + '. 文档流程与时序')
            return '<figure><div class="mermaid">' + match[1] + '</div><figcaption>' + caption + '</figcaption></figure>'
        body = re.sub(r'<pre><code class="language-mermaid">(.*?)</code></pre>\s*(?:<p align="center">(.*?)</p>)?', diagram, body, flags=re.S)
        def link(match):
            url = urlsplit(html.unescape(match[2]))
            if not url.scheme and not url.netloc and url.path:
                destination = (source.parent / unquote(url.path)).resolve()
                if destination in targets:
                    value = urlunsplit(('', '', targets[destination], url.query, url.fragment))
                    return match[1] + html.escape(value, quote=True) + match[3]
            return match[0]
        body = re.sub(r'(href\s*=\s*["\'])(.*?)(["\'])', link, body)
        checksum = hashlib.sha256(original).hexdigest()
        meta = '<div class="meta">冻结文档网页 · <a href="' + html.escape(source.name, quote=True) + '" download>原始 Markdown</a><br><code>SHA256 ' + checksum + '</code></div>'
        (output / targets[source]).write_text(wrapper(source.stem, meta + body, md.toc))
        records.append({'source': source.name, 'html': targets[source], 'sha256': checksum, 'diagrams': diagrams[0]})
    body = '<h1>实验文档中心</h1><p>构建、用例设计、测试结果、脚本设计与工作流验收。</p><div class="doc-cards">' + ''.join('<article class="doc-card"><h2><a href="' + row['html'] + '">' + html.escape(Path(row['source']).stem) + '</a></h2><p>' + str(row['diagrams']) + ' 张 Mermaid 图</p></article>' for row in records) + '</div>'
    (output / 'index.html').write_text(wrapper('实验文档中心', body))
    (output / 'reader-build.json').write_text(json.dumps({'schema_version': 1, 'run_id': run_id,
        'markdown_version': markdown.__version__, 'documents': records}, ensure_ascii=False, indent=2) + '\n')
    return records
