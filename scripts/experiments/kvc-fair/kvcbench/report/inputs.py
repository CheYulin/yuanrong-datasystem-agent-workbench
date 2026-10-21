"""Read only accepted, explicitly hash-bound offline report inputs."""
from pathlib import Path
import json

from kvcbench.core.steps import identifier
from kvcbench.core.result_state import report_completion, REPORT_DATA_INPUTS
from kvcbench.platform.files import read_json, validate_reference, digest

REQUIRED = REPORT_DATA_INPUTS


def load(path, *, diagnostic=False):
    path = Path(path)
    spec = read_json(path)
    if spec.get('schema_version') != 1:
        raise ValueError('Unsupported report input schema')
    run_id, profile = identifier(spec['run_id']), identifier(spec['profile'])
    relocations = {}
    reference_key = lambda record: json.dumps(record, sort_keys=True, separators=(',', ':'))
    for item in spec.get('relocations', []):
        original, local = item['original'], item['local']
        if any(original[field] != local[field] for field in ('sha256', 'bytes', 'mode')) or reference_key(original) in relocations:
            raise ValueError('A relocation must preserve the exact unique original file identity')
        relocations[reference_key(original)] = local
    def resolve(record):
        return validate_reference(relocations.get(reference_key(record), record))
    if set(spec.get('inputs', {})) != REQUIRED | {'audit'}:
        raise ValueError('Report requires the complete explicit input set')
    paths = {name: resolve(record) for name, record in spec['inputs'].items()}
    values = {name: read_json(source) for name, source in paths.items()}
    audit, analysis = values['audit'], values['analysis']
    for name in ('audit', 'analysis', 'workflow'):
        if values[name].get('run_id') != run_id or values[name].get('profile') != profile:
            raise ValueError('Report input run/profile differs: ' + name)
    if audit.get('bindings') != {name: spec['inputs'][name]['sha256'] for name in sorted(REQUIRED)}:
        raise ValueError('Audit is bound to a different analysis/evidence set')
    if not diagnostic and (audit.get('evidence_verified') is not True or audit.get('complete') is not True or audit.get('issues')):
        raise ValueError('Normal report requires a complete independently verified audit')
    if not diagnostic and not report_completion(analysis)['complete']:
        raise ValueError('Analysis does not contain a complete verified native-batch matrix')
    if audit.get('native_passed') is not analysis.get('passed'):
        raise ValueError('Audit and analysis disagree on native success')
    if not isinstance(values['series'], list) or not isinstance(values['builds'], list) or len(values['builds']) != 3:
        raise ValueError('Explicit resource series and three product build records are required')
    if not diagnostic and any(row.get('build_exit') != 0 or row.get('identity_valid') is not True for row in values['builds']):
        raise ValueError('Normal report requires three accepted native build records')
    names = {row.get('product') for row in values['builds']}
    if names != {'datasystem', 'mooncake', 'lmcache'}:
        raise ValueError('Build input must identify all three products exactly once')
    evidence = values['evidence']
    if not isinstance(evidence, list):
        raise ValueError('Explicit report evidence references are required')
    destinations = set()
    file_destinations = set()
    namespaces = {'results', 'raw', 'workflow', 'build-logs', 'environment', 'qualification', 'runtime', 'observations', 'audits', 'builds', 'history', 'failed-attempts', 'measured-runtime'}
    for row in evidence:
        destination = Path(row['destination'])
        if (destination.is_absolute() or not destination.parts or '..' in destination.parts
                or destination.as_posix() != row['destination'] or destination.parts[0] not in namespaces
                or destination.as_posix() in destinations):
            raise ValueError('Unsafe or duplicate report evidence destination')
        destinations.add(destination.as_posix())
        if 'directory_mode' in row:
            if set(row) != {'destination', 'directory_mode'} or type(row['directory_mode']) is not int or not 0 <= row['directory_mode'] <= 0o777:
                raise ValueError('Explicit empty directory requires a safe permission mode')
        else:
            resolve(row['source'])
            file_destinations.add(destination.as_posix())
    if any(any(parent.as_posix() in file_destinations for parent in Path(name).parents) for name in destinations):
        raise ValueError('Evidence destination files conflict with directories')
    for row in analysis.get('rows', []):
        for key in ('evidence', 'outcome_evidence'):
            if row.get(key) and row[key] not in destinations:
                raise ValueError('A reported evidence link is not in the bound inventory: ' + row[key])
    step_files = {'inputs.json', 'result.json', 'artifacts.json', 'acceptance.json', 'summary.md', 'commit.json'}
    for step in values['workflow'].get('steps', []):
        links = step.get('evidence')
        if links is None:
            continue
        if not isinstance(links, dict) or set(links) != step_files:
            raise ValueError('Workflow evidence requires the complete step receipt files')
        if len({str(Path(destination).parent) for destination in links.values()}) != 1:
            raise ValueError('Workflow evidence files must belong to one step attempt')
        for name, destination in links.items():
            if Path(destination).name != name or destination not in file_destinations:
                raise ValueError('Workflow evidence link is not in the bound file inventory')
    documents = []
    for record in spec.get('documents', []):
        source = resolve(record)
        if source.suffix != '.md' or source.name in {item.name for item in documents}:
            raise ValueError('Document inputs must be distinct explicit Markdown files')
        documents.append(source)
    bundle = resolve(spec['source_bundle'])
    if bundle.suffix != '.zip':
        raise ValueError('An explicit verified reproduction source ZIP is required')
    if audit.get('document_bindings') is not None and audit['document_bindings'] != {source.name: digest(source) for source in documents}:
        raise ValueError('Document bytes differ from the independently bound current report documents')
    if audit.get('source_bundle_sha256') is not None and audit['source_bundle_sha256'] != digest(bundle):
        raise ValueError('Report source bundle differs from the audit-bound package')
    for name, source in paths.items():
        if digest(source) != spec['inputs'][name]['sha256']:
            raise ValueError('Report inputs changed while being read')
    return {'spec': spec, 'values': values, 'paths': paths, 'documents': documents,
            'bundle': bundle, 'diagnostic': diagnostic, 'run_id': run_id, 'profile': profile, 'resolve': resolve}
