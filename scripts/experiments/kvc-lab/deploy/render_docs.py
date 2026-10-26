"""Render the checked-in experiment RFC using the existing pinned report reader."""
import argparse
from pathlib import Path
import shutil
import sys


def render(output):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent / 'kvc-fair'))
    from kvcbench.report.documents import build
    source = root.parents[2] / 'rfc/2026-10-26-kvc-control-lab'
    documents = sorted(source.glob('*.md'))
    if not documents:
        raise FileNotFoundError('RFC sources are required for reproducible document rendering: ' + str(source))
    records = build(documents, output, run_id='KVC 登录实验台 · 构建与用例文档')
    output = Path(output)
    for name in ('docs-tables.css', 'docs-tables.js'):
        shutil.copy2(root / 'web/assets' / name, output / '_reader' / name)
    for page in sorted(output.glob('*.html')):
        content = page.read_text()
        if content.count('</head>') != 1 or content.count('</body>') != 1:
            raise ValueError('Unexpected experiment document wrapper: ' + page.name)
        content = content.replace('</head>', '<link rel="stylesheet" href="_reader/docs-tables.css"></head>')
        content = content.replace('</body>', '<script defer src="_reader/docs-tables.js"></script></body>')
        page.write_text(content)
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    render(parser.parse_args().output)
