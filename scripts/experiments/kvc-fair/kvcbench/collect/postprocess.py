"""Analyze an accepted snapshot and produce independently bound report inputs."""
from pathlib import Path
import json

from kvcbench.collect import index, documents, sources
from kvcbench.collect.analysis import batch
from kvcbench.collect.audits import coverage, resources, tcp, deployment, runtime, gate_inputs, postprocessing
from kvcbench.collect.transport import verify_received
from kvcbench.collect.capture import REQUIRED_STEPS
from kvcbench.core.configuration import configuration_profile
from kvcbench.core.result_state import report_completion, REPORT_DATA_INPUTS
from kvcbench.core.steps import Check
from kvcbench.core.timing import started, finished
from kvcbench.platform.files import atomic_json, read_json, reference, digest, regular
from kvcbench.platform.receipts import Workflow, verify_commit, file_reference

TOOL_ROOT = Path(__file__).resolve().parents[2]


def normalize_analysis(value):
    return {key: item for key, item in value.items() if key not in {'samples', 'generated_utc', 'samples_source'}}


def workflow_view(payload, receipt, local_workflow, roots, run_id, profile):
    titles = {'W01': '固定计划', 'W02': '复验构建产物（未重编译）', 'W03': '复验代码包', 'W04': '新部署',
              'W05': '主机基线', 'W06': '环境隔离', 'W07.admission': '资格入场', 'W07': '原生资格',
              'W07.smoke-admission': 'smoke入场', 'W07.smoke': '观测smoke', 'W08.admission': '正式运行入场',
              'W08': '观测就绪', 'W09': '正式矩阵', 'W10': '停止与排空', 'W11': '恢复', 'W12': '收集冻结', 'W13': '离线分析'}
    rows = []
    def append(path, root_mapping):
        commit = verify_commit(path, roots=root_mapping)
        result = read_json(path.parent / 'result.json')
        step = commit['identity']['step_id']
        rows.append({'id': step, 'step_id': step, 'title': titles.get(step, step), 'execution': result['execution'],
                     'acceptance': commit['acceptance'], 'duration_s': result['duration_s'],
                     'clock_domain': result['clock_domain'], 'commit_sha256': commit['commit_sha256']})
    for step in REQUIRED_STEPS:
        append(sorted((payload / 'controller/workflow/steps' / step).glob('attempt-*/commit.json'))[-1], roots)
    append(sorted((receipt / 'steps/W12').glob('attempt-*/commit.json'))[-1], roots)
    append(sorted((local_workflow.root / 'steps/W13').glob('attempt-*/commit.json'))[-1], None)
    return {'run_id': run_id, 'profile': profile, 'steps': rows,
            'scope': 'Closed upstream steps before audit/render. W14–W18 receipts and final delivery timings are outside the immutable report input snapshot. Durations retain their own host/boot clock domains.'}


def report_evidence(snapshot, receipt, out):
    result = []
    def add(source, destination):
        result.append({'source': file_reference(source), 'destination': destination})
    for path in sorted(snapshot.rglob('*')):
        if path.is_file():
            add(path, 'raw/snapshot/' + path.relative_to(snapshot).as_posix())
    for row in read_json(snapshot / 'evidence-index.json').get('directories', []):
        result.append({'destination': 'raw/snapshot/payload/' + row['path'], 'directory_mode': row['mode']})
    for path in sorted(receipt.rglob('*')):
        if path.is_file():
            add(path, 'raw/snapshot-receipt/' + path.relative_to(receipt).as_posix())
    suite = snapshot / 'payload/formal-batch-001'
    for path in sorted(suite.glob('r*-*/b*-*.json')):
        add(path, 'results/' + path.relative_to(suite).as_posix())
    for path in sorted(suite.glob('r*-*/case-outcomes/*.json')):
        add(path, 'results/' + path.relative_to(suite).as_posix())
    add(out / 'analysis/summary.csv', 'results/summary.csv')
    for path in sorted((out / 'audits').rglob('*')):
        if path.is_file():
            add(path, 'audits/' + path.relative_to(out / 'audits').as_posix())
    return sorted(result, key=lambda row: row['destination'])


def configuration(payload, analysis):
    products = read_json(payload / 'products.json')
    before = read_json(payload / 'controller/payload/environment-preparation/after.json')
    ds = read_json(payload / 'batch-gate-001/r0-datasystem/tcp-configuration.json')
    moon = read_json(payload / 'run/gate-r0-mooncake/master-argv.json')
    lm = sorted((payload / 'batch-gate-001/r0-lmcache/server-logs').glob('server-*-launch.json'))
    if len(lm) != 2:
        raise ValueError('Both LMCache native server launch records are required for the current configuration appendix')
    return [{'name': '运行与测量参数', 'value': {key: analysis['parameters'][key] for key in ('repeats', 'duration', 'warmup', 'put_keys', 'continue_readonly_errors', 'workload_protocol')}},
            {'name': '实际 cgroup 配额', 'value': before['settings']},
            {'name': 'DataSystem 两 Worker 原始配置', 'value': [row['live_argv'] for row in ds['workers']]},
            {'name': 'Mooncake TCP 配置', 'value': configuration_profile(products)},
            {'name': 'Mooncake Master 启动参数记录', 'value': moon},
            {'name': 'LMCache 启动参数记录（非持续 live 观测）', 'value': [read_json(path) for path in lm]},
            {'name': '原始构建主机与工具链参数', 'value': read_json(payload / 'native-build/inputs/build-spec.json')}]


def derive(received, out, source_bundle, *, device='253:2'):
    received, out, source_bundle = Path(received).resolve(strict=True), Path(out).absolute(), Path(source_bundle).resolve(strict=True)
    snapshot, receipt = received / 'snapshot', received / 'receipt'
    admission = verify_received(snapshot, receipt)
    package = sources.verify_bundle(source_bundle, TOOL_ROOT)
    if out.exists() or out.is_symlink() or any(path.is_symlink() for path in out.parents):
        raise FileExistsError('Use a fresh local derivation root')
    if any(source == out or source in out.parents or out in source.parents for source in (received, source_bundle)):
        raise ValueError('Derived outputs must be separate from their source inputs')
    out.mkdir(parents=True)
    run_id, profile = admission['run_id'], admission['profile']
    spec = {'schema_version': 1, 'run_id': run_id, 'profile': profile, 'received': str(received), 'device': device,
            'source_bundle': file_reference(source_bundle), 'collection_commit_sha256': admission['collection_commit_sha256']}
    atomic_json(out / 'derive-spec.json', spec, exclusive=True)
    atomic_json(out / 'transport-adoption.json', admission, exclusive=True)
    workflow = Workflow.create(out / 'workflow', run_id, profile, out / 'derive-spec.json')
    payload, suite = snapshot / 'payload', snapshot / 'payload/formal-batch-001'
    w13 = workflow.begin('W13', inputs=[snapshot / 'evidence-index.json', snapshot / 'collection-admission.json',
                                      Path(admission['collection_commit']), out / 'transport-adoption.json', source_bundle])
    try:
        w13.require_ready()
        analysis = batch.analyze(suite, device)
        analysis['run_id'] = run_id
        complete = report_completion(analysis)['complete']
        if not complete:
            raise ValueError('Normal derivation requires a complete independently verified native matrix: ' + repr(analysis['issues']))
        batch.export(analysis, out / 'analysis')
        atomic_json(out / 'series.json', analysis['samples'], exclusive=True)
        builds = [dict(read_json(payload / ('builds/' + name + '.json')), evidence='builds/' + name + '.json')
                  for name in ('datasystem', 'mooncake', 'lmcache')]
        atomic_json(out / 'builds.json', builds, exclusive=True)
        w13_commit = w13.finish(execution='completed', returncode=0, scope='offline complete native matrix and actual SDK-window resource reduction',
            checks=[Check('complete_verified_matrix', True, complete, complete)],
            artifacts=[reference(out, path) for path in ['analysis/analysis.json', 'analysis/summary.csv', 'analysis/analysis.md', 'series.json', 'builds.json']],
            validator_id='collect.derive.v1', validator_sources=[Path(__file__), Path(batch.__file__)],
            native_outcomes={'passed': analysis['passed'], 'counts': analysis['counts']})
    except BaseException as error:
        if not w13.closed:
            w13.finish(execution='failed', returncode=1, scope='failed offline analysis', checks=[Check('analysis', 'verified complete matrix', str(error), False)],
                       artifacts=[], validator_id='collect.derive.v1', validator_sources=[Path(__file__), Path(batch.__file__)])
        raise
    attempt = workflow.begin('W14', inputs=[out / 'analysis/analysis.json', out / 'series.json', out / 'builds.json', snapshot / 'evidence-index.json'],
                             upstream=[w13_commit], allowed_findings=['short_window_sampling'])
    try:
        attempt.require_ready()
        begin = started()
        verify_received(snapshot, receipt)
        recomputed = batch.analyze(suite, device)
        recomputed['run_id'] = run_id
        if normalize_analysis(recomputed) != normalize_analysis(read_json(out / 'analysis/analysis.json')):
            raise ValueError('Independent analysis re-read differs from the stored derivation')
        if read_json(out / 'series.json') != recomputed['samples']:
            raise ValueError('Stored resource series differs from the original sample reduction')
        audits = out / 'audits'
        (audits / 'resources').mkdir(parents=True)
        resource = resources.audit(suite)
        atomic_json(audits / 'resources/resource-audit.json', resource, exclusive=True)
        (audits / 'resources/resource-audit.md').write_text(resources.markdown(resource))
        repeated_resource = resources.audit(suite)
        if {key: value for key, value in resource.items() if key != 'generated_utc'} != {key: value for key, value in repeated_resource.items() if key != 'generated_utc'}:
            raise ValueError('Independent resource reduction differs from the stored audit')
        tcp_result = tcp.verify(payload / 'batch-gate-001')
        atomic_json(audits / 'tcp.json', tcp_result, exclusive=True)
        deployment_result = deployment.Audit(payload).run()
        atomic_json(audits / 'deployment.json', deployment_result, exclusive=True)
        stage, runtime_records = runtime.verify_runtime_inputs(suite, payload / 'runtime-inputs')
        pins = read_json(payload / 'runtime-inputs/configs/versions/kvc_20261020.json')['products']
        source_builds = runtime.verify_source_build_provenance(stage, runtime_records, builds, products=pins)
        gate, gate_proof = gate_inputs.verify_gate_evidence(payload / 'batch-gate-001', stage, read_json(suite / 'products.json'))
        tcp_proof, _ = gate_inputs.verify_tcp_evidence(audits / 'tcp.json', payload / 'batch-gate-001', TOOL_ROOT)
        isolation = runtime.verify_datasystem_isolation(payload / 'batch-gate-001')
        deployment_proof, _ = postprocessing.verify_deployment_evidence(audits / 'deployment.json', suite, payload / 'runtime-inputs', TOOL_ROOT)
        resource_proof, _ = postprocessing.verify_resource_evidence(audits / 'resources', suite, analysis, TOOL_ROOT)
        coverage_result = coverage.evaluate(analysis, resources.read_jsonl(suite / 'resources/samples.jsonl', required=True), resource)
        atomic_json(audits / 'coverage.json', coverage_result, exclusive=True)
        checks = [Check('native_matrix_recomputed', True, True, True),
                  Check('pinned_source_builds', True, source_builds, bool(source_builds and source_builds['verified'])),
                  Check('gate', True, gate_proof['passed'], gate_proof['passed']),
                  Check('tcp_payload', True, tcp_proof, tcp_result['valid'] is True),
                  Check('deployment_isolation', True, deployment_proof['verified'], deployment_proof['verified']),
                  Check('resource_reduction', True, resource_proof['verified'], resource_proof['verified']),
                  Check('resource_coverage', True, coverage_result, coverage_result['verified']),
                  Check('short_window_sampling', 0, len(coverage_result['findings']), not coverage_result['findings'], required=False)]
        config = configuration(payload, analysis)
        docs = documents.generate(payload, out / 'documents', analysis, builds, config, coverage_result)
        view = workflow_view(payload, receipt, workflow, admission['root_mapping'], run_id, profile)
        atomic_json(out / 'workflow.json', view, exclusive=True)
        evidence = report_evidence(snapshot, receipt, out)
        for name in ('datasystem', 'mooncake', 'lmcache'):
            evidence.append({'source': file_reference(payload / ('builds/' + name + '.json')), 'destination': 'builds/' + name + '.json'})
        atomic_json(out / 'evidence.json', sorted(evidence, key=lambda row: row['destination']), exclusive=True)
        inputs = {'analysis': file_reference(out / 'analysis/analysis.json'), 'series': file_reference(out / 'series.json'),
                  'builds': file_reference(out / 'builds.json'), 'workflow': file_reference(out / 'workflow.json'),
                  'evidence': file_reference(out / 'evidence.json')}
        issues = [check.record() for check in checks if check.required and not check.passed]
        audit = {'schema_version': 1, 'run_id': run_id, 'profile': profile, 'complete': not issues,
                 'evidence_verified': not issues, 'native_passed': analysis['passed'], 'issues': issues,
                 'findings': coverage_result['findings'], 'checks': [check.record() for check in checks],
                 'bindings': {name: inputs[name]['sha256'] for name in sorted(REPORT_DATA_INPUTS)},
                 'configuration': config, 'source_builds': source_builds, 'datasystem_isolation': isolation,
                 'postprocessing_package': package,
                 'document_bindings': {path.name: digest(path) for path in docs}, 'source_bundle_sha256': digest(source_bundle),
                 'timing_scope': 'audit computation and bound-input construction before final receipt sealing', **finished(begin)}
        atomic_json(out / 'audit.json', audit, exclusive=True)
        commit = attempt.finish(execution='completed', returncode=0, scope='independent original-input revalidation, matrix/resource/TCP/deployment checks and report bindings',
            checks=checks, artifacts=[reference(out, name) for name in ['audit.json', 'workflow.json', 'evidence.json']]
                + [reference(out, path.relative_to(out)) for path in docs]
                + [reference(out, path.relative_to(out)) for path in audits.rglob('*') if path.is_file()],
            validator_id='collect.audit.v1', validator_sources=[Path(__file__), Path(batch.__file__), Path(coverage.__file__), Path(tcp.__file__),
                Path(resources.__file__), Path(deployment.__file__), Path(postprocessing.__file__), Path(runtime.__file__), Path(gate_inputs.__file__)])
        if verify_commit(commit)['acceptance'] not in {'passed', 'passed_with_findings'}:
            raise ValueError('Independent audit did not accept normal report generation')
        descriptor = {'schema_version': 1, 'run_id': run_id, 'profile': profile, 'inputs': dict(inputs, audit=file_reference(out / 'audit.json')),
                      'documents': [file_reference(path) for path in docs], 'source_bundle': file_reference(source_bundle)}
        atomic_json(out / 'report-inputs.json', descriptor, exclusive=True)
        return {'out': str(out), 'run_id': run_id, 'profile': profile, 'report_inputs': str(out / 'report-inputs.json'),
                'audit_commit': str(commit), 'counts': analysis['counts'], 'native_passed': analysis['passed'], 'evidence_verified': True}
    except BaseException as error:
        if not attempt.closed:
            attempt.finish(execution='failed', returncode=1, scope='failed independent offline audit', checks=[Check('audit', 'verified original evidence', str(error), False)],
                artifacts=[], validator_id='collect.audit.v1', validator_sources=[Path(__file__)], details={'error': str(error)})
        raise
