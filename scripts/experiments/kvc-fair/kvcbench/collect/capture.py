"""Freeze terminal runtime, controller and native-build evidence under their resource locks."""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import subprocess

from kvcbench.collect import index
from kvcbench.core.source_state import source_state
from kvcbench.core.steps import Check
from kvcbench.platform.cgroup import drain
from kvcbench.platform.files import atomic_json, identity, read_json, reference, regular, real_directory
from kvcbench.platform.locks import open_path
from kvcbench.platform.receipts import Workflow, verify_commit
from kvcbench.platform.package import inventory

REQUIRED_STEPS = ('W01', 'W02', 'W03', 'W04', 'W05', 'W06', 'W07.admission', 'W07',
                  'W07.smoke-admission', 'W07.smoke', 'W08.admission', 'W08', 'W09', 'W10', 'W11')
ORIGIN_PREFIXES = {'runtime': '', 'control': 'controller', 'native': 'native-build',
                   'bundle': 'source-bundle', 'invocation': 'invocation', 'revalidator': 'native-revalidator',
                   'collector': 'collector-source'}


def latest_commit(workflow, step):
    attempts = sorted((workflow / 'steps' / step).glob('*'), key=lambda path: path.name.removeprefix('.partial-'))
    if not attempts or attempts[-1].name.startswith('.partial-') or not (attempts[-1] / 'commit.json').is_file():
        raise ValueError('Collection requires a committed terminal step: ' + step)
    path = attempts[-1] / 'commit.json'
    if verify_commit(path)['acceptance'] not in {'passed', 'passed_with_findings'}:
        raise ValueError('Collection requires accepted runtime evidence: ' + step)
    return path


def members(runtime, control, native, bundle, stage, invocation=None, catalog=None, collector=None, directories=None):
    roots = {'runtime': runtime, 'control': control, 'native': native, 'bundle': bundle}
    selected = {}
    skipped = []

    def add(root_name, relative, destination, role):
        row = {'source_root': root_name, 'source_path': str(relative), 'path': str(destination), 'role': role}
        if str(destination) in selected and selected[str(destination)] != row:
            raise ValueError('Two sources claim an evidence destination: ' + str(destination))
        selected[str(destination)] = row

    def tree(root_name, prefix, destination, role):
        directory = roots[root_name] / prefix
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError('Required closed evidence directory is absent: ' + str(directory))
        for path in sorted(directory.rglob('*')):
            relative = path.relative_to(roots[root_name])
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts) or 'etcd-data' in relative.parts:
                continue
            if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if roots[root_name] in parent.parents):
                skipped.append({'source_root': root_name, 'path': relative.as_posix(), 'reason': 'symlink'})
                continue
            if path.is_file():
                target = Path(destination) / path.relative_to(directory)
                add(root_name, relative.as_posix(), target.as_posix(), role)
            elif not path.is_dir():
                skipped.append({'source_root': root_name, 'path': relative.as_posix(), 'reason': 'nonregular node'})
            elif directories is not None and not any(path.iterdir()):
                directories.append({'source_root': root_name, 'source_path': relative.as_posix(),
                    'path': (Path(destination) / path.relative_to(directory)).as_posix(), 'role': role})

    add('runtime', 'batch-stage.json', 'batch-stage.json', 'runtime')
    for name, expected in stage['inputs'].items():
        source = regular(runtime, name)
        if identity(source) != {key: expected[key] for key in ('sha256', 'bytes', 'mode')}:
            raise ValueError('Frozen execution input changed before collection: ' + name)
        role = 'build' if name.startswith(('builds/', 'builds-provenance/')) else 'runtime'
        add('runtime', name, name, role)
        add('runtime', name, 'runtime-inputs/' + name, role)
    for name in ('batch-gate-001', 'batch-smoke-001', 'formal-batch-001'):
        tree('runtime', name, name, 'measurement' if name == 'formal-batch-001' else 'qualification')
    tree('runtime', 'run', 'run', 'runtime')
    tree('control', Path('.'), 'controller', 'runtime')
    for prefix in ('inputs', 'configs', 'kvcbench', 'scripts', 'commands', 'compiler-inputs', 'build-metadata', 'workflow'):
        tree('native', prefix, 'native-build/' + prefix, 'build')
    if (native / 'revalidation').is_dir():
        tree('native', 'revalidation', 'native-build/revalidation', 'build')
    for name in ('build-result.json', 'build-cleanup.json', 'build-resources.json'):
        regular(native, name)
        add('native', name, 'native-build/' + name, 'build')
    for name, expected in stage['build_artifacts'].items():
        relative = Path(name).relative_to(native).as_posix()
        if identity(regular(native, relative)) != expected:
            raise ValueError('Native build output changed before collection: ' + name)
        add('native', relative, 'native-build/' + relative, 'build')
    name = 'datasystem/build/CMakeCache.txt'
    regular(native, name)
    add('native', name, 'native-build/' + name, 'build')
    name = 'datasystem/build/_deps/iconv-src/config.log'
    if (native / name).is_file():
        add('native', name, 'native-build/' + name, 'build')
    if catalog is not None:
        name = Path(catalog).relative_to(native).as_posix()
        regular(native, name)
        add('native', name, 'native-build/' + name, 'build')
    if stage.get('source_build_mode'):
        provenance = read_json(regular(runtime, 'builds-provenance/validation.json'))
        metadata_name = provenance['products']['datasystem']['metadata']
        if Path(metadata_name).parts[0] == 'revalidation':
            recorded = read_json(regular(native, str(Path(metadata_name).parent / 'result.json')))
            if recorded.get('accepted') is not True or not recorded.get('validator'):
                raise ValueError('Selected build revalidation lacks its original successful validator identity')
            root = real_directory(os.path.commonpath(list(recorded['validator'])))
            roots['revalidator'] = root
            for path, expected in recorded['validator'].items():
                relative = Path(path).relative_to(root).as_posix()
                if identity(regular(root, relative)) != expected:
                    raise ValueError('Original source-build revalidator changed before collection')
                add('revalidator', relative, 'native-revalidator/' + relative, 'build')
    for name in ('manifest.json', 'archive.json', 'bundle.zip'):
        regular(bundle, name)
        add('bundle', name, 'source-bundle/' + name, 'runtime')
    tree('bundle', 'payload', 'source-bundle/payload', 'runtime')
    if invocation is not None:
        roots['invocation'] = real_directory(invocation)
        if read_json(regular(roots['invocation'], 'all-command.json')).get('returncode') != 0:
            raise ValueError('Outer invocation did not complete successfully')
        tree('invocation', Path('.'), 'invocation', 'runtime')
    if collector is not None:
        roots['collector'] = real_directory(collector)
        for name in inventory(roots['collector']):
            add('collector', name, 'collector-source/' + name, 'runtime')
    return sorted(selected.values(), key=lambda row: row['path']), roots, skipped


def checkouts(runtime):
    records = {}
    for product in ('datasystem', 'mooncake', 'lmcache'):
        metadata = read_json(regular(runtime, 'builds/' + product + '.json'))
        source = metadata['source_directory']
        commands = [['git', '-C', source, *arguments] for arguments in [
            ['rev-parse', 'HEAD'], ['rev-parse', metadata['tag'] + '^{commit}'],
            ['status', '--porcelain', '--untracked-files=normal'], ['status', '--porcelain', '--untracked-files=no']]]
        values = [subprocess.check_output(command, text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS='0')).strip() for command in commands]
        state = source_state(product, values[2], values[3])
        if values[0] != metadata['sha'] or values[1] != metadata['sha'] or not state['source_worktree_policy_valid']:
            raise ValueError('Source checkout no longer matches the measured pinned build: ' + product)
        records[product] = {'commands': commands, 'head': values[0], 'tag_sha': values[1], **state}
    return records


def capture(control, out, *, invocation=None):
    control, out = real_directory(control), Path(out).absolute()
    spec = read_json(regular(control, 'spec.json'))
    runtime, native, bundle = [real_directory(spec[name]) for name in ('root', 'native_root', 'bundle')]
    state = read_json(regular(control, 'runtime-result.json'))
    summary = index.terminal_suite(runtime / 'formal-batch-001')
    if (state.get('execution_complete') is not True or state.get('native_passed') is not summary['valid']
            or state.get('run_id') != spec['run_id'] or state.get('profile') != spec['profile']):
        raise ValueError('Controller and terminal suite disagree on actual completion')
    upstream = [latest_commit(control / 'workflow', step) for step in REQUIRED_STEPS]
    if out.exists() or out.is_symlink():
        raise FileExistsError('Use a fresh collection attempt')
    receipt_root = out.with_name(out.name + '-receipt')
    if any(out == source or source in out.parents or out in source.parents
           or receipt_root == source or source in receipt_root.parents for source in (runtime, control, native, bundle)):
        raise ValueError('Collection and receipt roots must be separate from immutable sources')
    workflow = Workflow.create(receipt_root, spec['run_id'], spec['profile'], control / 'spec.json')
    attempt = workflow.begin('W12', upstream=upstream, allow_upstream_findings=True)
    attempt.require_ready()
    try:
        with ExitStack() as locks:
            build_lock = locks.enter_context(open_path(native / 'suite.lock'))
            fcntl.flock(build_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            runtime_lock = locks.enter_context(open_path(runtime / 'suite.lock'))
            fcntl.flock(runtime_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            stage = read_json(regular(runtime, 'batch-stage.json'))
            if stage.get('root') != str(runtime) or stage.get('base') != str(native) or stage.get('cgroup') != spec['cgroup']:
                raise ValueError('Collection specification differs from the frozen runtime resource domain')
            held = os.fstat(runtime_lock.fileno())
            if (stage['shared_suite_lock_inode'], stage['shared_suite_lock_device']) != (held.st_ino, held.st_dev):
                raise ValueError('Collection is holding the wrong runtime resource lock')
            with open_path(Path(spec['resource_lock'])) as canonical:
                current = os.fstat(canonical.fileno())
                if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                    raise ValueError('Canonical runtime resource lock was replaced')
            drained = drain(Path(spec['cgroup']))
            sources = checkouts(runtime)
            directories = []
            selection, roots, skipped = members(runtime, control, native, bundle, stage, invocation,
                                                Path(spec['build_catalog']), Path(__file__).resolve().parents[2], directories)
            result = index.freeze_sources(selection, out, run_id=spec['run_id'], profile=spec['profile'],
                                          roots=roots, directories=directories)
            drain(Path(spec['cgroup']))
            root_mapping = {str(source): str(out / 'payload' / ORIGIN_PREFIXES[name]) for name, source in roots.items()}
            atomic_json(out / 'collection-admission.json', {'run_id': spec['run_id'], 'profile': spec['profile'],
                'drained': drained, 'source_checkouts': sources, 'root_mapping': root_mapping, 'skipped_nodes': skipped,
                'selection_scope': 'Closed result/log/code/build/receipt files; Coordinator etcd-data and nonregular nodes are excluded. Native artifact bytes are included for independent offline build-receipt verification.',
                'workflow_spec': str(control / 'spec.json')}, exclusive=True)
            payloads = [reference(out, name) for name in ('evidence-index.json', 'collection-admission.json')]
            payloads += [reference(out / 'payload', row['path']) for row in read_json(out / 'evidence-index.json')['files']]
            commit = attempt.finish(execution='completed', returncode=0, scope='terminal, restored, locked source collection and immutable evidence inventory',
                checks=[Check('accepted_terminal_steps', len(REQUIRED_STEPS), len(upstream), len(upstream) == len(REQUIRED_STEPS)),
                        Check('frozen_inventory', True, result['valid'], result['valid'])], artifacts=payloads,
                validator_id='collect.capture.v1', validator_sources=[Path(__file__), Path(index.__file__)], details=result)
        return {**result, 'out': str(out), 'collection_commit': str(commit), 'receipt_root': str(receipt_root)}
    except BaseException as error:
        if not attempt.closed:
            attempt.finish(execution='failed', returncode=1, scope='failed terminal collection; partial copies are not admitted',
                checks=[Check('collection', 'complete verified snapshot', str(error), False)], artifacts=[],
                validator_id='collect.capture.v1', validator_sources=[Path(__file__), Path(index.__file__)], details={'error': str(error)})
        raise
