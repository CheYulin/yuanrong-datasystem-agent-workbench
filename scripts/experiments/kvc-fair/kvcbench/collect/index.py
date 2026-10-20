"""Freeze an explicit, role-tagged evidence inventory into a fresh immutable tree."""
from pathlib import Path
import re
import shutil

from kvcbench.core.steps import identifier
from kvcbench.core.timing import started, finished
from kvcbench.platform.files import atomic_json, identity, read_json, regular, real_directory

ROLES = {'runtime', 'build', 'qualification', 'measurement', 'observation', 'environment', 'failure'}
RESERVED = {'derived', 'report', 'delivery', '__pycache__'}


def terminal_suite(suite):
    summary = read_json(regular(suite, 'summary.json'))
    if (not summary.get('ended') or summary.get('execution_complete') is not True
            or summary.get('failed') or summary.get('monitor_exit') != 0 or summary.get('interference_exit') != 0):
        raise ValueError('Normal evidence freezing requires terminal execution and stopped monitors')
    drained = summary.get('drained', {})
    if (not isinstance(drained.get('members'), dict) or not drained['members']
            or any(drained['members'].values()) or drained.get('populated') != []):
        raise ValueError('Final resource-domain drain evidence is incomplete')
    return summary


def validate_members(members):
    if not isinstance(members, list) or not members:
        raise ValueError('Explicit nonempty evidence members are required')
    names = []
    for item in members:
        if set(item) != {'path', 'role'} or item['role'] not in ROLES:
            raise ValueError('Every evidence member needs a recognized source role')
        name = item['path']
        if not isinstance(name, str):
            raise ValueError('Evidence paths must be strings')
        path = Path(name)
        if (path.is_absolute() or path.as_posix() != name or '..' in path.parts
                or not path.parts or any(part in RESERVED or part.startswith('.') for part in path.parts)):
            raise ValueError('Unsafe, mutable or derived evidence path: ' + name)
        if 'workflow' in path.parts:
            at = path.parts.index('workflow')
            tail = path.parts[at + 1:]
            if len(tail) < 4 or tail[0] != 'steps' or not re.fullmatch(r'W(?:0[1-9]|1[01])(?:[._-][A-Za-z0-9_-]+)*', tail[1]):
                raise ValueError('Only selected upstream closed-step receipts may enter the index')
        if path.name == 'workflow-status.json':
            raise ValueError('Mutable workflow status is not source evidence')
        names.append(name)
    if names != sorted(set(names)):
        raise ValueError('Evidence members must be sorted and unique')
    return members


def freeze(source, members, out, *, run_id, profile):
    begin = started()
    identifier(run_id)
    identifier(profile)
    source, out = Path(source).resolve(strict=True), Path(out).absolute()
    validate_members(members)
    if out == source or source in out.parents or out in source.parents:
        raise ValueError('Evidence snapshot must be separate from the source tree')
    if out.exists() or out.is_symlink() or any(p.is_symlink() for p in out.parents):
        raise FileExistsError('Evidence snapshot root must be fresh without symlink ancestors')
    originals = [(item, regular(source, item['path'])) for item in members]
    out.mkdir(parents=True)
    copied = []
    try:
        for item, original in originals:
            before = identity(original)
            target = out / 'payload' / item['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
            if identity(original) != before or identity(target) != before:
                raise ValueError('Evidence changed while being frozen: ' + item['path'])
            copied.append({**item, **before})
        atomic_json(out / 'evidence-index.json', {'schema_version': 1, 'run_id': run_id, 'profile': profile,
            'source_root': str(source), 'scope': 'explicit frozen source inventory; upstream terminal/restore admission is the caller responsibility',
            'files': copied, **finished(begin)}, exclusive=True)
        return verify(out)
    except BaseException as exc:
        atomic_json(out / 'failure.json', {'error': type(exc).__name__, 'message': str(exc),
                    'copied_files': copied, **finished(begin)}, exclusive=True)
        raise


def verify(root):
    root = real_directory(root)
    payload = real_directory(root / 'payload')
    nodes = list(payload.rglob('*'))
    if any(path.is_symlink() for path in nodes):
        raise ValueError('Frozen evidence cannot contain symbolic links')
    index = read_json(regular(root, 'evidence-index.json'))
    if index.get('schema_version') != 1:
        raise ValueError('Unsupported evidence index schema')
    identifier(index['run_id'])
    identifier(index['profile'])
    validate_members([{key: row[key] for key in ('path', 'role')} for row in index['files']])
    if (root / 'failure.json').exists():
        raise ValueError('An incomplete evidence snapshot cannot pass')
    observed_paths = {p.relative_to(payload).as_posix() for p in nodes if p.is_file()}
    if observed_paths != {row['path'] for row in index['files']}:
        raise ValueError('Evidence index does not describe the exact frozen payload')
    for row in index['files']:
        observed = identity(regular(root / 'payload', row['path']))
        if any(row[key] != observed[key] for key in ('sha256', 'bytes', 'mode')):
            raise ValueError('Frozen evidence mismatch: ' + row['path'])
    return {'valid': True, 'run_id': index['run_id'], 'profile': index['profile'], 'files': len(index['files']),
            'scope': 'frozen source inventory integrity; native outcomes and full workflow acceptance are separate'}
