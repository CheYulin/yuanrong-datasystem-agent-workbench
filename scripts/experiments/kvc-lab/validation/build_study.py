"""Package a complete independently audited study for the private chart report."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re

try:
    from .audit_study_004 import BUILDS, SOURCE, case_key, expected_matrix, summarize
except ImportError:
    from audit_study_004 import BUILDS, SOURCE, case_key, expected_matrix, summarize

if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kvclab.spec import preview_plan

REQUIRED = {'baseline', 'worker', 'client', 'metadata', 'nodes2', 'nodes4'}
CONFIG_FILES = {'baseline': 'baseline', 'worker': 'worker-scaling', 'client': 'client-scaling',
                'metadata': 'metadata-scaling', 'nodes2': 'nodes-2', 'nodes4': 'nodes-4'}


def contract(name):
    path = Path(__file__).resolve().parents[1] / 'configs' / (CONFIG_FILES[name] + '.json')
    return expected_matrix(preview_plan(json.loads(path.read_text())))


def validate(audit):
    if audit.get('status') != 'passed':
        raise ValueError('The independent audit must pass before report packaging')
    runs = audit.get('runs', [])
    if {row['name'] for row in runs} != REQUIRED or len(runs) != len(REQUIRED):
        raise ValueError('All six planned studies must appear exactly once')
    if any(not re.fullmatch(r'[a-f0-9]{32}', row.get('job_id', '')) for row in runs) or len({row['job_id'] for row in runs}) != len(runs):
        raise ValueError('A physical run cannot replace another planned study')
    for run in runs:
        coverage = run['coverage']
        if run.get('status') != 'passed' or run.get('passed') is not True or run.get('issues') or type(coverage['expected']) is not int or coverage['expected'] <= 0 or coverage['actual_remote_completion_rows'] != coverage['expected'] or any(coverage[key] for key in ['missing', 'duplicates', 'unexpected', 'invalid_count']):
            raise ValueError('Unaccepted or incomplete matrix: ' + run['name'])
        if len(run.get('rows', [])) != coverage['expected'] or any(row.get('audit_passed') is not True for row in run['rows']):
            raise ValueError('Every original case must have independent validation')
        planned = contract(run['name'])
        fields = [key for key in next(iter(planned.values())) if key != 'requested_duration_s']
        identities = lambda rows: Counter(tuple(row.get(key) for key in fields) for row in rows)
        if identities(run['rows']) != identities(planned.values()):
            raise ValueError('The accepted matrix differs from the named study contract: ' + run['name'])
        for row in run['rows']:
            if row['operation'] != 'put' and (type(row.get('duration_s')) not in (int, float) or not math.isfinite(row['duration_s']) or row['duration_s'] < .99 * planned[case_key(row)]['requested_duration_s']):
                raise ValueError('A shorter read/exist probe cannot replace the named study window')
        if run.get('source_sha256') != SOURCE or any(row.get('source_sha256') != SOURCE or row.get('build_sha') != BUILDS.get(row.get('product')) for row in run['rows']):
            raise ValueError('The named study source and product builds must remain pinned')
    summaries = audit.get('summaries', [])
    if not summaries or sum(row.get('observations', 0) for row in summaries) != sum(run['coverage']['expected'] for run in runs):
        raise ValueError('Summary observations must cover every accepted original case exactly once')
    for summary in summaries:
        if not summary.get('repeat_identities') or summary.get('observations') != len(summary['repeat_identities']):
            raise ValueError('Invalid accepted repeat identities')
        for name in ['keys_s', 'p99_us', 'server_cpu_cores', 'client_cpu_cores', 'master_cpu_cores']:
            metric = summary.get(name, {})
            if metric.get('count') != summary['observations'] or any(type(metric.get(key)) not in (int, float) or not math.isfinite(metric[key]) for key in ['median', 'min', 'max']):
                raise ValueError('Required metric is incomplete: ' + name)
    if summaries != summarize(runs):
        raise ValueError('Summary groups and metrics must match the accepted audit rows')
    if not isinstance(audit.get('scope'), dict):
        raise ValueError('The audit scope is required')
    return True


def validate_directory(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Report data must be a real directory')
    names = {path.name for path in directory.iterdir()}
    required = {'study.json', 'audit.json', 'study-manifest.json'}
    if names not in (required, required | {'observability.json'}) or any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
        raise ValueError('Report data must contain only the explicit frozen report files')
    manifest = json.loads((directory / 'study-manifest.json').read_text())
    if manifest.get('kind') != 'accepted_private_study' or set(manifest.get('files', {})) != names - {'study-manifest.json'}:
        raise ValueError('Report manifest must cover the exact file set')
    for name, expected in manifest['files'].items():
        content = (directory / name).read_bytes()
        if expected != {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)}:
            raise ValueError('Report manifest mismatch: ' + name)
    original = (directory / 'audit.json').read_bytes()
    audit, study = json.loads(original), json.loads((directory / 'study.json').read_bytes())
    validate(audit)
    validate(study)
    expected = {**audit, 'scope': {**audit['scope'], 'host': 'tiantiyun-80c128g'},
                'audit_sha256': hashlib.sha256(original).hexdigest(), 'audit_url': 'data/audit.json'}
    for key in ['findings', 'observability']:
        if key in study:
            expected[key] = study[key]
    if study != expected:
        raise ValueError('Report data differs from its bound independent audit')
    if ('observability.json' in names) != ('observability' in study) or 'observability' in study and json.loads((directory / 'observability.json').read_text()) != study['observability']:
        raise ValueError('Report observability file is not bound to its displayed data')
    if 'observability' in study:
        validate_observability(study['observability'], audit, hashlib.sha256(original).hexdigest())
    return directory


def validate_observability(observed, audit, audit_sha256):
    if not isinstance(observed, dict) or observed.get('performance_audit_sha256') != audit_sha256 or observed.get('status') != 'passed' or observed.get('binding_passed') is not True:
        raise ValueError('IO overview must bind the exact accepted performance audit')
    runs = observed.get('runs', [])
    expected = {(run['name'], run['job_id']) for run in audit['runs']}
    if len(runs) != len(expected) or {(run.get('name'), run.get('job_id')) for run in runs} != expected:
        raise ValueError('IO overview must cover the same six physical jobs')
    counts = {run['name']: run['coverage']['expected'] for run in audit['runs']}
    for run in runs:
        if any(type(run.get(key)) is not int or run[key] < 0 for key in ['known_windows', 'unknown_windows']) or run['known_windows'] + run['unknown_windows'] != counts[run['name']]:
            raise ValueError('IO overview must account for every server observation window')
        if run['unknown_windows'] and (run.get('read_bytes') is not None or run.get('write_bytes') is not None):
            raise ValueError('Unknown IO windows cannot be counted as zero in totals')


def package(audit_path, output, *, findings_path=None, observability_path=None):
    audit_path, output = Path(audit_path), Path(output)
    original = audit_path.read_bytes()
    report = json.loads(original)
    validate(report)
    findings = observed = None
    if findings_path:
        findings = json.loads(Path(findings_path).read_text())
        if not isinstance(findings, list) or any(not isinstance(row, dict) or not isinstance(row.get('text'), str) or row.get('section') not in {'baseline', 'worker', 'client', 'metadata', 'nodes', 'evidence'} for row in findings):
            raise ValueError('Findings must be explicit text annotations tied to report sections')
    if observability_path:
        original_io = Path(observability_path).read_bytes()
        observed = json.loads(original_io)
        validate_observability(observed, report, hashlib.sha256(original).hexdigest())
    output.mkdir(parents=True, exist_ok=False)
    (output / 'audit.json').write_bytes(original)
    report['audit_sha256'] = hashlib.sha256(original).hexdigest()
    report['audit_url'] = 'data/audit.json'
    report['scope']['host'] = 'tiantiyun-80c128g'
    if findings_path:
        report['findings'] = findings
    if observability_path:
        report['observability'] = observed
        (output / 'observability.json').write_bytes(original_io)
    (output / 'study.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    manifest = {path.name: {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'size': path.stat().st_size} for path in sorted(output.iterdir())}
    (output / 'study-manifest.json').write_text(json.dumps({'kind': 'accepted_private_study', 'files': manifest}, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--findings')
    parser.add_argument('--observability')
    args = parser.parse_args()
    print(json.dumps(package(args.audit, args.out, findings_path=args.findings, observability_path=args.observability)))
