"""Verify received evidence and original receipts without rewriting their remote paths."""
from pathlib import Path

from kvcbench.collect import index
from kvcbench.collect.capture import REQUIRED_STEPS, ORIGIN_PREFIXES
from kvcbench.core.steps import ACCEPTED
from kvcbench.platform.files import read_json, regular, identity, real_directory
from kvcbench.platform.receipts import verify_commit

PREFIXES = ORIGIN_PREFIXES


def references(value):
    if isinstance(value, dict):
        if {'root', 'path', 'sha256', 'bytes', 'mode'} <= set(value):
            yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def terminal_records(payload, inventory):
    summary = index.terminal_suite(payload / 'formal-batch-001')
    state = read_json(regular(payload, 'controller/runtime-result.json'))
    if (state.get('execution_complete') is not True or state.get('native_passed') is not summary['valid']
            or state.get('run_id') != inventory['run_id'] or state.get('profile') != inventory['profile']):
        raise ValueError('Received runtime completion disagrees with its actual suite or run identity')
    before = read_json(regular(payload, 'controller/payload/environment-preparation/before.json'))
    restored = read_json(regular(payload, 'controller/payload/environment-restoration/result.json'))
    if (restored.get('accepted') is not True or restored.get('after', {}).get('settings') != before['settings']
            or restored.get('after', {}).get('runtime_mask') != before.get('runtime_mask')):
        raise ValueError('Received environment restoration does not match the recorded original settings/mask')
    final_path = regular(payload, 'formal-batch-001/resources/final.json')
    final = read_json(final_path)
    names = [row['path'] for row in final.get('files', [])]
    if (final.get('closed') is not True or final.get('normal_exit') is not True
            or set(final.get('children', {})) != {'host', 'interference'}
            or any(child.get('returncode') != 0 or child.get('forced') is not False for child in final['children'].values())):
        raise ValueError('Received observation was not closed normally')
    if (len(names) != len(set(names)) or 'final.json' in names
            or not {'ready.json', 'samples.jsonl', 'monitor-manifest.json', 'interference.jsonl', 'controller.log'} <= set(names)):
        raise ValueError('Received final observation manifest is incomplete or recursive')
    for row in final['files']:
        current = regular(final_path.parent, row['path'])
        if identity(current) != {key: row[key] for key in ('sha256', 'bytes', 'mode')}:
            raise ValueError('Closed observation file differs from its final manifest')
    stage = read_json(regular(payload, 'batch-stage.json'))
    if len(stage.get('build_artifacts', {})) != 9:
        raise ValueError('The three pinned source builds must identify their nine native outputs')
    native = Path(inventory['source_roots']['native'])
    for name, expected in stage['build_artifacts'].items():
        relative = Path(name).relative_to(native)
        if identity(regular(payload / 'native-build', relative)) != expected:
            raise ValueError('Received native output differs from the measured stage identity')
    return summary


def verify_received(snapshot, receipt):
    snapshot, receipt = real_directory(snapshot), real_directory(receipt)
    verified_index = index.verify(snapshot)
    inventory = read_json(regular(snapshot, 'evidence-index.json'))
    if inventory.get('schema_version') != 2 or set(inventory['source_roots']) - set(PREFIXES):
        raise ValueError('Explicit multi-origin collection is required for remote receipt adoption')
    if not {'runtime', 'control', 'native', 'bundle'} <= set(inventory['source_roots']):
        raise ValueError('Runtime/controller/build/bundle origins are mandatory')
    payload = snapshot / 'payload'
    roots = {source: str(payload / PREFIXES[name]) for name, source in inventory['source_roots'].items()}
    candidates = sorted((receipt / 'steps/W12').glob('attempt-*/commit.json'))
    if not candidates or list((receipt / 'steps/W12').glob('.partial-*')):
        raise ValueError('A closed W12 receipt is required; uncommitted collection state is not admitted')
    commit = candidates[-1]
    artifact_records = read_json(commit.parent / 'artifacts.json')['files']
    original_index = [row for row in artifact_records if row['path'] == 'evidence-index.json']
    if len(original_index) != 1 or any(original_index[0][key] != identity(snapshot / 'evidence-index.json')[key]
                                      for key in ('sha256', 'bytes', 'mode')):
        raise ValueError('W12 does not bind the received evidence index')
    original_snapshot = original_index[0]['root']
    original_receipt = read_json(commit.parent / 'inputs.json')['workflow']['root']
    roots[original_snapshot], roots[original_receipt] = str(snapshot), str(receipt)
    admission = read_json(regular(snapshot, 'collection-admission.json'))
    expected_mapping = {source: str(Path(original_snapshot) / 'payload' / PREFIXES[name])
                        for name, source in inventory['source_roots'].items()}
    if admission.get('root_mapping') != expected_mapping:
        raise ValueError('Collection admission root mapping differs from its original index')
    receipt_trees = [receipt, payload / 'controller/workflow', payload / 'native-build/workflow']
    for tree in receipt_trees:
        for path in tree.rglob('*.json'):
            for record in references(read_json(path)):
                original = Path(record['root'])
                if not any(original == Path(source) or Path(source) in original.parents for source in roots):
                    raise ValueError('Receipt references an origin outside the explicit frozen snapshot')
    checked = verify_commit(commit, roots=roots)
    if (checked['acceptance'] not in ACCEPTED or checked['identity']['run_id'] != inventory['run_id']
            or checked['identity']['profile'] != inventory['profile']):
        raise ValueError('The received collection receipt was not accepted for this run/profile')
    for step in REQUIRED_STEPS:
        attempts = sorted((payload / 'controller/workflow/steps' / step).glob('attempt-*/commit.json'))
        if not attempts:
            raise ValueError('A required runtime step is absent from the received snapshot: ' + step)
        record = verify_commit(attempts[-1], roots=roots)
        if (record['acceptance'] not in ACCEPTED or record['identity']['run_id'] != inventory['run_id']
                or record['identity']['profile'] != inventory['profile']):
            raise ValueError('A received runtime step is not accepted for this matrix: ' + step)
    summary = terminal_records(payload, inventory)
    native_receipts = []
    for path in sorted((payload / 'native-build/workflow/steps').glob('*/attempt-*/commit.json')):
        record = verify_commit(path, roots=roots)
        native_receipts.append({'step_id': record['identity']['step_id'], 'acceptance': record['acceptance'],
                                'bindings_verified': record['bindings_verified'], 'commit_sha256': record['commit_sha256']})
    if {row['step_id'] for row in native_receipts} != {'W01', 'W02', 'W02.datasystem', 'W02.mooncake', 'W02.lmcache'}:
        raise ValueError('Original source build step receipts are incomplete')
    return {'valid': True, 'run_id': inventory['run_id'], 'profile': inventory['profile'], 'files': verified_index['files'],
            'root_mapping': roots, 'collection_commit': str(commit), 'collection_commit_sha256': checked['commit_sha256'],
            'original_native_receipts': native_receipts,
            'execution_complete': summary['execution_complete'], 'native_passed': summary['valid'],
            'scope': 'Received payload identities, original closed W12/upstream receipt chains and original build receipt integrity; historical failed build acceptance remains failed. No measurement is replayed and no original record is rewritten.'}
