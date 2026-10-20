"""Freeze an explicit, role-tagged evidence inventory into a fresh immutable tree."""
from pathlib import Path
import re
import shutil
import stat

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
        code_prefixes = {(), ('native-build',), ('collector-source',), ('source-bundle', 'payload'), ('runtime-inputs',)}
        code_at = path.parts.index('kvcbench') if 'kvcbench' in path.parts else None
        source_code = code_at is not None and path.parts[:code_at] in code_prefixes
        bundled_source = path.parts[:1] == ('collector-source',) or path.parts[:2] == ('source-bundle', 'payload')
        if (path.is_absolute() or path.as_posix() != name or '..' in path.parts
                or not path.parts or any(part.startswith('.') or part == '__pycache__'
                    or part in RESERVED and not (bundled_source or source_code and index > code_at)
                    for index, part in enumerate(path.parts))):
            raise ValueError('Unsafe, mutable or derived evidence path: ' + name)
        if 'workflow' in path.parts:
            at = path.parts.index('workflow')
            tail = path.parts[at + 1:]
            metadata = tail == ('workflow.json',)
            if not metadata and (len(tail) < 4 or tail[0] != 'steps' or not re.fullmatch(r'W(?:0[1-9]|1[01])(?:[._-][A-Za-z0-9_-]+)*', tail[1])):
                raise ValueError('Only selected upstream closed-step receipts may enter the index')
        if path.name == 'workflow-status.json':
            raise ValueError('Mutable workflow status is not source evidence')
        names.append(name)
    if names != sorted(set(names)):
        raise ValueError('Evidence members must be sorted and unique')
    known = set(names)
    if any(any(parent.as_posix() in known for parent in Path(name).parents) for name in names):
        raise ValueError('Evidence file and directory destinations conflict')
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
    if index.get('schema_version') not in {1, 2}:
        raise ValueError('Unsupported evidence index schema')
    identifier(index['run_id'])
    identifier(index['profile'])
    if index['schema_version'] == 2:
        roots = index.get('source_roots', {})
        if not roots or any(not Path(value).is_absolute() or '..' in Path(value).parts for value in roots.values()):
            raise ValueError('Frozen source origins must be explicit absolute roots')
        for row in [*index['files'], *index.get('directories', [])]:
            source = Path(row.get('source_path', ''))
            if (row.get('source_root') not in roots or not source.parts or source.is_absolute()
                    or '..' in source.parts or source.as_posix() != row.get('source_path')):
                raise ValueError('Frozen source origin is unsafe or incomplete')
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
    for row in index.get('directories', []):
        validate_members([{'path': row['path'], 'role': row['role']}])
        directory = real_directory(payload / row['path'])
        if stat.S_IMODE(directory.stat().st_mode) != row['mode']:
            raise ValueError('Frozen empty-directory mode differs: ' + row['path'])
        if any(directory.iterdir()):
            raise ValueError('Frozen empty-directory evidence is no longer empty: ' + row['path'])
    return {'valid': True, 'run_id': index['run_id'], 'profile': index['profile'], 'files': len(index['files']),
            'scope': 'frozen source inventory integrity; native outcomes and full workflow acceptance are separate'}


def freeze_sources(members, out, *, run_id, profile, roots, directories=()):
    begin = started()
    identifier(run_id)
    identifier(profile)
    out = Path(out).absolute()
    selected = sorted(members, key=lambda row: row['path'])
    validate_members([{'path': row['path'], 'role': row['role']} for row in selected])
    if out.exists() or out.is_symlink() or any(path.is_symlink() for path in out.parents):
        raise FileExistsError('Use a fresh evidence snapshot without symlink ancestors')
    source_roots = {key: real_directory(value) for key, value in roots.items()}
    if any(out == source or out in source.parents or source in out.parents for source in source_roots.values()):
        raise ValueError('The evidence snapshot must be separate from its source roots')
    originals = []
    for item in selected:
        if set(item) != {'path', 'role', 'source_root', 'source_path'} or item['source_root'] not in source_roots:
            raise ValueError('Explicit named source roots and regular source paths are required')
        source = regular(source_roots[item['source_root']], item['source_path'])
        originals.append((item, source))
    out.mkdir(parents=True)
    copied = []
    copied_directories = []
    try:
        for item, source in originals:
            before = identity(source)
            destination = out / 'payload' / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if identity(source) != before or identity(destination) != before:
                raise ValueError('Source or copied evidence changed during collection: ' + item['path'])
            copied.append({'path': item['path'], 'role': item['role'], **before,
                           'source_root': item['source_root'], 'source_path': item['source_path']})
        for item in directories:
            validate_members([{'path': item['path'], 'role': item['role']}])
            source_path = Path(item['source_path'])
            if source_path.is_absolute() or '..' in source_path.parts or not source_path.parts:
                raise ValueError('Unsafe empty-directory source path')
            source = real_directory(source_roots[item['source_root']] / source_path)
            if any(source.iterdir()):
                raise ValueError('An explicitly empty source directory changed during collection')
            destination = out / 'payload' / item['path']
            destination.mkdir(parents=True, exist_ok=False)
            mode = stat.S_IMODE(source.stat().st_mode)
            destination.chmod(mode)
            copied_directories.append({**item, 'mode': mode})
        atomic_json(out / 'evidence-index.json', {'schema_version': 2, 'run_id': run_id, 'profile': profile,
            'source_roots': {key: str(value) for key, value in source_roots.items()}, 'files': copied,
            'directories': copied_directories,
            'scope': 'explicit closed-source copies; source identities rechecked while copying; runtime admission is independently recorded by the collection receipt',
            **finished(begin)}, exclusive=True)
        return verify(out)
    except BaseException as error:
        atomic_json(out / 'failure.json', {'error': type(error).__name__, 'message': str(error),
                    'copied_files': copied, **finished(begin)}, exclusive=True)
        raise
