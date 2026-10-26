"""Render the checked-in experiment RFC using the existing pinned report reader."""
import argparse
from pathlib import Path
import sys


def render(output):
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent / 'kvc-fair'))
    from kvcbench.report.documents import build
    source = root.parents[2] / 'rfc/2026-10-26-kvc-control-lab'
    documents = sorted(source.glob('*.md'))
    if not documents:
        raise FileNotFoundError('RFC sources are required for reproducible document rendering: ' + str(source))
    return build(documents, output, run_id='KVC 登录实验台 · 构建与用例文档')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    render(parser.parse_args().output)
