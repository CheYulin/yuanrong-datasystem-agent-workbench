"""Explicit W17 upload/registration and independently repeatable W18 public acceptance."""
from pathlib import Path
import json
import re
import shlex
import uuid

from kvcbench.core.steps import Check, ACCEPTED
from kvcbench.delivery import acceptance, public, install, publish
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, read_json, reference
from kvcbench.platform.receipts import Workflow, verify_commit
from kvcbench.pipeline.steps import perform, files


def latest(workflow, step):
    candidates = sorted((workflow.root / 'steps' / step).glob('attempt-*/commit.json'))
    if not candidates or list((workflow.root / 'steps' / step).glob('.partial-*')):
        raise ValueError('A closed upstream delivery receipt is required: ' + step)
    commit = candidates[-1]
    if verify_commit(commit)['acceptance'] not in ACCEPTED:
        raise ValueError('Delivery upstream is not accepted: ' + step)
    return commit


def command(out, name, argv, cwd):
    result = execute(argv, cwd=cwd, log=out / (name + '.log'), record=out / (name + '-command.json'), timeout=1800)
    if result['returncode'] != 0:
        raise ValueError('Actual publication command failed: ' + name)


def upload(control, spec):
    control = Path(control).resolve(strict=True)
    workflow = Workflow(control / 'workflow')
    w16 = latest(workflow, 'W16')
    runtime = read_json(control / 'spec.json')
    report = Path(runtime['report'])
    host, site, relative = spec['host'], Path(spec['site']), spec['relative']
    path = Path(relative)
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host) or not site.is_absolute()
            or '..' in site.parts or not re.fullmatch(r'perf/[A-Za-z0-9][A-Za-z0-9_-]*', relative)):
        raise ValueError('Explicit SSH alias, absolute site and fresh perf/version path are required')
    atomic_json(control / 'publication-spec.json', spec, exclusive=True)
    def action():
        acceptance.manifest(report)
        out = control / ('upload-' + uuid.uuid4().hex)
        out.mkdir()
        remote = site / path.parent / ('.kvc-upload-' + uuid.uuid4().hex)
        ssh = lambda argv: ['ssh', host, shlex.join([str(value) for value in argv])]
        command(out, 'mkdir', ssh(['mkdir', '-p', remote / 'report']), control)
        command(out, 'upload-report', ['rsync', '-a', '--chmod=D755,F644', str(report) + '/', host + ':' + str(remote / 'report') + '/'], control)
        for name, source in [('install.py', Path(install.__file__)), ('publish.py', Path(publish.__file__))]:
            command(out, 'upload-' + name[:-3], ['scp', str(source), host + ':' + str(remote / name)], control)
        command(out, 'install-register', ssh(['python3', remote / 'install.py', '--source', remote / 'report', '--site', site,
            '--relative', relative, '--title', spec['title'], '--date', spec['date'], '--record', remote / 'registration.json',
            '--publisher', remote / 'publish.py']), control)
        import json
        result = json.loads((out / 'install-register.log').read_text().splitlines()[-1])
        atomic_json(out / 'result.json', result, exclusive=True)
        return [Check('uploaded_registered_manifest', True, True, True)], files(out), result
    commit = perform(workflow, 'W17', action=action, scope='fresh immutable full upload and exact homepage registration with publication lock/CAS/gzip',
        sources=[Path(__file__), Path(install.__file__), Path(publish.__file__)], inputs=[control / 'publication-spec.json'], upstream=[w16])
    return {'upload_commit': str(commit), 'control': str(control)}


def check(control):
    control = Path(control).resolve(strict=True)
    workflow = Workflow(control / 'workflow')
    parent = latest(workflow, 'W17')
    spec = read_json(control / 'publication-spec.json')
    local = read_json(control / 'spec.json')
    def action():
        out = control / ('public-acceptance-' + uuid.uuid4().hex)
        out.mkdir()
        with (out / 'range-progress.jsonl').open('x') as stream:
            def progress(part):
                stream.write(json.dumps(part) + '\n')
                stream.flush()
            result = public.verify(spec['url'], local['report'], spec['relative'], on_segment=progress)
        atomic_json(out / 'downloads.json', result, exclusive=True)
        browser = Path(acceptance.__file__).with_name('browser.mjs')
        command(out, 'browser-public', [local['node'], browser, result['url'], out / 'browser', local['playwright'], local['chrome']], control)
        if read_json(out / 'browser/result.json')['passed'] is not True:
            raise ValueError('Actual public responsive browser check failed')
        atomic_json(out / 'result.json', result, exclusive=True)
        return [Check('public_delivery', True, result['passed'], result['passed'])], files(out), result
    commit = perform(workflow, 'W18', action=action, scope='actual public downloads and responsive browser acceptance',
        sources=[Path(__file__), Path(public.__file__), Path(public.range_download.__file__), public.SITE_PROFILE, Path(acceptance.__file__), Path(acceptance.__file__).with_name('browser.mjs')], upstream=[parent])
    return {'public_acceptance_commit': str(commit), 'url': spec['url'].rstrip('/') + '/index.html'}
