"""Independently verify native Coordinator provenance and every DataSystem epoch."""
from pathlib import Path

from kvcbench.core.artifact_extension import COORDINATOR, validate as validate_extension
from kvcbench.platform.archives import verify_tar_inputs
from kvcbench.platform.files import read_json, regular, identity, digest


def verify(payload, builds, deployment):
    stage = read_json(regular(payload, 'batch-stage.json'))
    if stage.get('datasystem_metadata_backend') != 'coordinator':
        return {'verified': True, 'applicable': False, 'metadata_backend': 'legacy-etcd',
                'scope': 'Historical ETCD baseline only; this is not acceptance of the required final Coordinator comparison.'}
    metadata = next(row for row in builds if row['product'] == 'datasystem')
    binary = regular(payload / 'native-build', COORDINATOR)
    binary_identity = identity(binary)
    if (metadata['artifact_hashes'].get(COORDINATOR) != binary_identity['sha256']
            or metadata['artifact_sizes'].get(COORDINATOR) != binary_identity['bytes']):
        raise ValueError('Coordinator binary differs from its actual build inventory')
    archives = [name for name in metadata['artifact_hashes'] if name.endswith('.tar.gz')]
    if len(archives) != 1:
        raise ValueError('Exactly one bound DataSystem build archive is required')
    archive = regular(payload / 'native-build', archives[0])
    if digest(archive) != metadata['artifact_hashes'][archives[0]]:
        raise ValueError('Original DataSystem tar differs from its build record')
    verify_tar_inputs(archive, {'datasystem/service/datasystem_coordinator': binary_identity})
    adoption = metadata.get('artifact_adoption')
    if adoption:
        original_path = regular(payload / 'native-build', adoption['original_metadata'])
        if identity(original_path) != adoption['original_identity'] or adoption['identity'] != binary_identity:
            raise ValueError('Coordinator adoption changed its original metadata or binary identity')
        validate_extension(read_json(original_path), metadata)
    expected_group = stage['cgroup'] + '/server'
    if len(deployment['expected_clusters']) != 21 or len(set(deployment['expected_clusters'])) != 21:
        raise ValueError('Exactly 21 distinct DataSystem service epochs are required')
    rows = []
    for cluster in deployment['expected_clusters']:
        action, cell = cluster.split('-', 1)
        if action == 'gate':
            relative = 'batch-gate-001/' + cell + '/tcp-configuration.json'
        else:
            cell, epoch = cell.split('-datasystem-', 1)
            phase = 'batch-smoke-001' if action == 'smoke' else 'formal-batch-001'
            relative = phase + '/' + cell + '-datasystem/epochs/' + epoch + '/tcp-configuration.json'
        raw = read_json(regular(payload, relative))
        manifest_path = regular(payload, 'run/' + cluster + '/processes.json')
        manifest = read_json(manifest_path)
        coordinator = raw.get('coordinator') or {}
        processes = manifest['processes']
        if (raw.get('metadata_backend') != 'coordinator' or manifest.get('metadata_backend') != 'coordinator'
                or manifest.get('cgroup') != expected_group or len(processes) != 3
                or {row['name'] for row in processes} != {'coordinator', 'worker0', 'worker1'}
                or raw.get('processes_sha256') != digest(manifest_path)
                or coordinator.get('verified') is not True
                or coordinator.get('binary_sha256') != binary_identity['sha256']
                or manifest.get('coordinator_binary_sha256') != binary_identity['sha256']
                or set(raw.get('server_members') or []) != {row['pid'] for row in processes}
                or len(raw.get('workers', [])) != 2 or {row['worker_index'] for row in raw['workers']} != {0, 1}
                or manifest.get('source') != stage['root'] + '/datasystem'):
            raise ValueError('Coordinator epoch lacks complete native/live/accounting evidence: ' + cluster)
        process = next(row for row in processes if row['name'] == 'coordinator')
        address = manifest['coordinator_address']
        if (process['pid'] != coordinator['pid'] or str(process['start_ticks']) != str(coordinator['start_ticks'])
                or process['command'] != coordinator['live_argv']
                or process['command'][0] != stage['root'] + '/' + COORDINATOR
                or '0::' + expected_group.removeprefix('/sys/fs/cgroup') not in coordinator['cgroups'].splitlines()):
            raise ValueError('Coordinator live identity or server cgroup differs: ' + cluster)
        flags = (regular(payload, 'run/' + cluster + '/coordinator.flags')).read_text().splitlines()
        if flags != process['command'][1:]:
            raise ValueError('Coordinator flags differ from the recorded actual command')
        required = {'coordinator_address': address, 'coordinator_raft_initial_peers': '', 'log_monitor': 'false',
                    'json_log_monitor': 'false', 'request_sample_rate': '0', 'access_sample_rate': '0'}
        for key, value in required.items():
            if [flag for flag in flags if flag.startswith('--' + key + '=')] != ['--' + key + '=' + value]:
                raise ValueError('Coordinator profile flag is absent, duplicated or enabled: ' + key)
        for worker in raw['workers']:
            owned = next(row for row in processes if row['name'] == 'worker' + str(worker['worker_index']))
            if (worker['pid'] != owned['pid'] or str(worker['start_ticks']) != str(owned['start_ticks'])
                    or worker['live_argv'] != owned['command']
                    or worker['flags'].get('etcd_address') != '' or worker['flags'].get('coordinator_address') != address
                    or '--etcd_address=' not in worker['live_argv'] or '--coordinator_address=' + address not in worker['live_argv']
                    or '0::' + expected_group.removeprefix('/sys/fs/cgroup') not in worker['cgroups'].splitlines()):
                raise ValueError('Worker is not observed using the charged native Coordinator: ' + cluster)
        rows.append({'cluster': cluster, 'proof': relative, 'verified': True, 'coordinator_pid': coordinator['pid']})
    if len(rows) != 21:
        raise ValueError('The Coordinator profile requires all 21 DataSystem service epochs')
    return {'verified': True, 'applicable': True, 'metadata_backend': 'coordinator', 'epochs': rows,
            'binary': binary_identity, 'archive': archives[0], 'inventory_extension': adoption is not None,
            'scope': 'Exact native binary versus original build tar; original metadata extension bindings; live Coordinator/two-Worker argv/PID/cgroup observations in every gate/smoke/formal epoch, all charged to server. No HA or recovery performance claim.'}
