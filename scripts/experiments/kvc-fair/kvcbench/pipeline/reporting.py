"""Seal W15 report generation and W16 real local/offline delivery acceptance."""
from pathlib import Path
import sys

from kvcbench.core.steps import Check, ACCEPTED
from kvcbench.delivery import acceptance
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json, read_json, digest, reference
from kvcbench.platform.receipts import Workflow, verify_commit
from kvcbench.pipeline.steps import perform, files
from kvcbench.report import render


def local(derived, report, control, *, node, playwright, chrome):
    derived, report, control = Path(derived).resolve(strict=True), Path(report).absolute(), Path(control).absolute()
    descriptor = derived / 'report-inputs.json'
    spec = read_json(descriptor)
    candidates = sorted((derived / 'workflow/steps/W14').glob('attempt-*/commit.json'))
    if not candidates or list((derived / 'workflow/steps/W14').glob('.partial-*')):
        raise ValueError('Local delivery requires a closed W14 audit')
    upstream = candidates[-1]
    accepted = verify_commit(upstream)
    if (accepted['acceptance'] not in ACCEPTED or accepted['identity']['run_id'] != spec['run_id']
            or accepted['identity']['profile'] != spec['profile']):
        raise ValueError('W14 audit is not accepted for these report inputs')
    if control.exists() or control.is_symlink() or any(p.is_symlink() for p in control.parents):
        raise FileExistsError('Use a fresh report delivery control directory')
    if any(control == source or control in source.parents or source in control.parents
           for source in (derived, report)):
        raise ValueError('Report, input and delivery control directories must be independent')
    control.mkdir(parents=True)
    atomic_json(control / 'spec.json', {'run_id': spec['run_id'], 'profile': spec['profile'],
        'derived': str(derived), 'report': str(report), 'node': str(node), 'playwright': str(playwright),
        'chrome': str(chrome)}, exclusive=True)
    workflow = Workflow.create(control / 'workflow', spec['run_id'], spec['profile'], control / 'spec.json')

    def generate():
        result = render.render(descriptor, report)
        return [Check('publication_eligible', True, result['publication_eligible'], result['publication_eligible'])], files(report), result

    w15 = perform(workflow, 'W15', action=generate, scope='complete immutable report generation from accepted W14-bound inputs',
                  sources=[Path(__file__), Path(render.__file__)], inputs=[descriptor], upstream=[upstream])

    def validate():
        out = control / 'local-acceptance'
        out.mkdir()
        original = {'manifest': acceptance.manifest(report), 'links': acceptance.links(report)}
        extracted = acceptance.extract(report / 'reproduce.zip', out / 'clean-extract')
        portable = {'manifest': acceptance.manifest(extracted / 'report'), 'links': acceptance.links(extracted / 'report')}
        def command(name, argv, cwd):
            row = execute(argv, cwd=cwd, log=out / (name + '.log'), record=out / (name + '-command.json'), timeout=600)
            if row['returncode'] != 0:
                raise ValueError('Actual local acceptance command failed: ' + name)
        command('relocate', [sys.executable, '-m', 'kvcbench.report.relocate', '--root', extracted,
                            '--out', out / 'relocated-inputs.json'], extracted)
        command('regenerate', [sys.executable, '-m', 'kvcbench.report.render', '--inputs', out / 'relocated-inputs.json',
                              '--out', out / 'regenerated'], extracted)
        regenerated = {'manifest': acceptance.manifest(out / 'regenerated'), 'links': acceptance.links(out / 'regenerated')}
        if digest(report / 'data.json') != digest(out / 'regenerated/data.json'):
            raise ValueError('Clean extracted reproduction changed the displayed original data')
        browser = Path(acceptance.__file__).with_name('browser.mjs')
        for label, source in [('browser-local', report), ('browser-offline', extracted / 'report')]:
            command(label, [node, browser, source, out / label, playwright, chrome], control)
            if read_json(out / label / 'result.json')['passed'] is not True:
                raise ValueError('Actual browser acceptance failed: ' + label)
        result = {'passed': True, 'run_id': spec['run_id'], 'profile': spec['profile'],
                  'original': original, 'portable': portable, 'regenerated': regenerated,
                  'displayed_data_sha256': digest(report / 'data.json'),
                  'reproduction_zip_sha256': digest(report / 'reproduce.zip'),
                  'scope': 'Exact local manifest and links; clean safe extraction preserving modes; original frozen code relocates/regenerates identical displayed data; actual local and extracted browser checks at 1440/390/320px.'}
        atomic_json(out / 'result.json', result, exclusive=True)
        selected = [path for path in out.iterdir() if path.is_file() and
                    (path.name == 'result.json' or path.name.endswith(('-command.json', '.log')))]
        selected += [path for label in ('browser-local', 'browser-offline') for path in (out / label).iterdir() if path.is_file()]
        artifacts = [reference(out, path.relative_to(out)) for path in sorted(selected)]
        return [Check('local_delivery', True, True, True)], artifacts, result

    w16 = perform(workflow, 'W16', action=validate, scope='real local, clean extraction, offline regeneration and responsive browser acceptance',
                  sources=[Path(__file__), Path(acceptance.__file__), Path(acceptance.__file__).with_name('browser.mjs')], upstream=[w15])
    return {'report': str(report), 'control': str(control), 'report_commit': str(w15), 'local_acceptance_commit': str(w16)}
