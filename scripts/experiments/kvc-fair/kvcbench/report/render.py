"""Render an offline ECharts report from accepted immutable intermediate results."""
import argparse
import html
import json
from pathlib import Path
import shutil
import zipfile

from kvcbench.core.timing import started, finished
from kvcbench.platform.artifacts import finalize_artifacts, version_zip_href
from kvcbench.platform.files import atomic_json, digest, identity, validate_reference
from kvcbench.report.inputs import load
from kvcbench.report.model import compact_payload
from kvcbench.report.documents import build as build_documents
from kvcbench.report.offline import package as package_offline

ASSETS = Path(__file__).with_name('assets')
TOOL_ROOT = Path(__file__).resolve().parents[2]


def copy_bound(record, destination, *, resolve=validate_reference):
    source = resolve(record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    if identity(destination) != {key: record[key] for key in ('sha256', 'bytes', 'mode')}:
        raise ValueError('Bound report input changed during copy')


def render(inputs, out, *, diagnostic=False):
    begin = started()
    prepared = load(inputs, diagnostic=diagnostic)
    out = Path(out).absolute()
    if out.exists() or out.is_symlink() or any(parent.is_symlink() for parent in out.parents):
        raise FileExistsError('Report output must be a fresh independent directory')
    input_paths = [*prepared['paths'].values(), *prepared['documents'], prepared['bundle'], Path(inputs)]
    if any(out == source or source in out.parents or out in source.parents for source in input_paths):
        raise ValueError('Report output must be separate from all source inputs')
    out.mkdir(parents=True)
    values, spec = prepared['values'], prepared['spec']
    analysis = dict(values['analysis'], samples=values['series'])
    payload = compact_payload(analysis)
    payload.update(run_id=prepared['run_id'], profile=prepared['profile'], builds=values['builds'],
                   workflow=values['workflow'], audit=values['audit'], diagnostic=diagnostic,
                   publication_eligible=not diagnostic and values['audit']['complete'] is True,
                   documents=[{'title': path.stem, 'href': 'docs/' + path.stem + '.html'} for path in prepared['documents']])
    (out / 'assets').mkdir()
    for name in ('report.css', 'report.js'):
        shutil.copy2(ASSETS / name, out / 'assets' / name)
    shutil.copy2(ASSETS / 'echarts.min.js', out / 'assets/echarts.min.js')
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode()) >= 20 << 20:
        raise ValueError('Report data exceeds the explicit 20 MiB budget')
    (out / 'data.json').write_text(encoded)
    (out / 'data.js').write_text('window.KVC_DATA=' + encoded.replace('</', '<\\/') + ';\n')
    for name, record in spec['inputs'].items():
        copy_bound(record, out / 'provenance' / (name + '.json'), resolve=prepared['resolve'])
    for item in values['evidence']:
        if 'directory_mode' in item:
            target = out / item['destination']
            target.mkdir(parents=True, exist_ok=False)
            target.chmod(item['directory_mode'])
        else:
            copy_bound(item['source'], out / item['destination'], resolve=prepared['resolve'])
    docs = out / 'docs'
    docs.mkdir()
    for record in spec.get('documents', []):
        copy_bound(record, docs / Path(record['path']).name, resolve=prepared['resolve'])
    build_documents(prepared['documents'], docs, run_id=prepared['run_id'])
    copy_bound(spec['source_bundle'], out / 'provenance/source-bundle.zip', resolve=prepared['resolve'])
    shutil.copy2(out / 'provenance/source-bundle.zip', out / 'reproduce.zip')
    title = '诊断预览' if diagnostic else '执行完整 · 原生失败已保留' if not analysis['passed'] else '执行与原生验证通过'
    source = (ASSETS / 'report.html').read_text().replace('{{STATUS}}', html.escape(title)).replace('{{RUN_ID}}', html.escape(prepared['run_id']))
    (out / 'index.html').write_text(source)
    atomic_json(out / 'render-record.json', {'schema_version': 1, 'run_id': prepared['run_id'],
                'profile': prepared['profile'], 'diagnostic': diagnostic, 'inputs_sha256': digest(inputs),
                'source_bundle_sha256': digest(out / 'reproduce.zip'),
                'timing_scope': 'input/render snapshot before offline packaging and final compression; complete call timing returned to controller', **finished(begin)}, exclusive=True)
    package_offline(out, spec, values['evidence'], diagnostic=diagnostic)
    (out / 'index.html').write_text(version_zip_href((out / 'index.html').read_text(), digest(out / 'reproduce.zip')))
    finalize_artifacts(out)
    return {'out': str(out), 'run_id': prepared['run_id'], 'diagnostic': diagnostic,
            'native_passed': analysis['passed'], 'publication_eligible': payload['publication_eligible'],
            'timing_scope': 'complete render call including archive, manifest and compression', **finished(begin)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--diagnostic', action='store_true')
    args = parser.parse_args(argv)
    print(json.dumps(render(args.inputs, args.out, diagnostic=args.diagnostic), ensure_ascii=False))


if __name__ == '__main__':
    main()
