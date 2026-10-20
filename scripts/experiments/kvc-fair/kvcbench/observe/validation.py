"""Read-only observation artifact validation; startup and full coverage are distinct."""
from pathlib import Path

from kvcbench.platform.files import read_json, identity, regular


def ready(path):
    row = read_json(path)
    if row.get('ready') is not True or row.get('scope') != 'startup only; full sampling coverage remains unverified':
        raise ValueError('Missing startup-only observation readiness')
    if set(row.get('children', {})) != {'host', 'interference'}:
        raise ValueError('Both suite monitor identities are required')
    if any(type(child.get('pid')) is not int or child['pid'] <= 0 or not child.get('argv')
           for child in row['children'].values()):
        raise ValueError('Invalid owned monitor identity')
    sample = row['readiness_sample']
    for role in ('server', 'client'):
        group = sample['groups'][role]
        if type(group['cpu.stat'].get('usage_usec')) is not int or type(group.get('memory.current')) is not int:
            raise ValueError('Missing required initial resource counters')
    for name in row['required_tools']:
        if (row['startup_manifest']['commands'].get(name, {}).get('available') is not True
                or name not in sample['tool_returncodes'] or sample['tool_returncodes'][name] is not None):
            raise ValueError('Required tool was not live at the readiness snapshot: ' + name)
    if row.get('duration_s') != (row['end_mono_ns'] - row['start_mono_ns']) / 1e9:
        raise ValueError('Invalid observation startup duration')
    return {'valid': True, 'scope': 'persisted startup snapshot only; current liveness and full coverage unverified'}


def final(path):
    path = Path(path)
    row = read_json(path)
    if row.get('closed') is not True or row.get('normal_exit') is not True:
        raise ValueError('Suite monitor streams were not closed normally')
    if set(row.get('children', {})) != {'host', 'interference'} or any(
            child.get('returncode') != 0 or child.get('forced') is not False for child in row['children'].values()):
        raise ValueError('Monitor stop identities/exits are incomplete')
    names = [item['path'] for item in row['files']]
    required = {'ready.json', 'samples.jsonl', 'monitor-manifest.json', 'interference.jsonl', 'controller.log'}
    if len(set(names)) != len(names) or not required <= set(names) or 'final.json' in names:
        raise ValueError('Finalized observation manifest is incomplete or recursive')
    for item in row['files']:
        observed = identity(regular(path.parent, item['path']))
        if any(observed[key] != item[key] for key in ('sha256', 'bytes', 'mode')):
            raise ValueError('Finalized observation changed: ' + item['path'])
    ready(path.parent / 'ready.json')
    return {'valid': True, 'scope': 'stopped observation artifact integrity; full measurement window coverage remains for collection/audit',
            'files': len(names)}
