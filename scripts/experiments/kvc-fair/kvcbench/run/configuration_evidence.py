"""Read actual DataSystem process flags before SDK measurement."""
import hashlib
import json
from pathlib import Path

SOURCE = {"tag": "v0.9.2.rc44", "sha": "2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6"}

def digest(value):
    return hashlib.sha256(value).hexdigest()

def configuration_evidence(run_dir, endpoints, *, require_coordinator=False):
    manifest_path = run_dir / "processes.json"
    manifest = json.loads(manifest_path.read_text())
    evidence = []
    coordinator = None
    members = None
    if require_coordinator:
        if manifest.get('metadata_backend') != 'coordinator' or len(manifest['processes']) != 3 or {p['name'] for p in manifest['processes']} != {'coordinator', 'worker0', 'worker1'}:
            raise ValueError('A native Coordinator and exactly two Workers are required')
        members = [int(value) for value in (Path(manifest['cgroup']) / 'cgroup.procs').read_text().split()]
        if set(members) != {p['pid'] for p in manifest['processes']}:
            raise ValueError('The server resource domain must contain exactly the Coordinator and two Workers')
        process = next(p for p in manifest['processes'] if p['name'] == 'coordinator')
        coordinator = live_coordinator(process, manifest)
    for index in [0, 1]:
        path = run_dir / f"w{index}/worker.flags"
        raw = path.read_bytes()
        flags = dict(line[2:].split("=", 1) for line in raw.decode().splitlines() if line.startswith("--"))
        required = {"worker_address": endpoints[index], "ipc_through_shared_memory": "false",
                    "enable_rdma": "false", "enable_urma": "false"}
        if require_coordinator:
            required.update(etcd_address='', coordinator_address=manifest['coordinator_address'])
        if any(flags.get(k) != v for k, v in required.items()):
            raise ValueError("Worker TCP flags are absent or inconsistent: " + str(path))
        process = next(item for item in manifest["processes"] if item["name"] == f"worker{index}")
        pid = process["pid"]
        live_args = Path(f"/proc/{pid}/cmdline").read_bytes().decode().strip("\0").split("\0")
        live_stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if live_stat[0] == "Z" or str(process["start_ticks"]) != live_stat[19]:
            raise ValueError("Worker PID identity is no longer live")
        if any(f"--{k}={v}" not in live_args for k, v in required.items()):
            raise ValueError("Live worker command disagrees with TCP flag evidence")
        cgroups = Path(f'/proc/{pid}/cgroup').read_text()
        if require_coordinator and '0::' + manifest['cgroup'].removeprefix('/sys/fs/cgroup') not in cgroups.splitlines():
            raise ValueError('Worker is outside the charged server cgroup')
        evidence.append({"worker_index": index, "path": str(path), "sha256": digest(raw),
                         "flags": required, "pid": pid, "start_ticks": live_stat[19], "live_argv": live_args,
                         'cgroups': cgroups})
    return {"workers": evidence, "processes_path": str(manifest_path),
            "processes_sha256": digest(manifest_path.read_bytes()), "source": SOURCE,
            'metadata_backend': manifest.get('metadata_backend'), 'coordinator': coordinator, 'server_members': members}


def live_coordinator(process, manifest):
    pid = process['pid']
    def ticks():
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z' or fields[19] != str(process['start_ticks']):
            raise ValueError('Coordinator PID/start_ticks is no longer live')
        return fields[19]
    before = ticks()
    argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
    cgroups = Path(f'/proc/{pid}/cgroup').read_text()
    binary = Path(manifest['source']) / 'output/datasystem/service/datasystem_coordinator'
    if argv != process['command'] or Path(argv[0]).resolve() != binary.resolve() or ticks() != before:
        raise ValueError('Live Coordinator differs from its recorded native command')
    required = {'coordinator_address': manifest['coordinator_address'], 'coordinator_raft_initial_peers': '',
                'log_monitor': 'false', 'json_log_monitor': 'false', 'request_sample_rate': '0', 'access_sample_rate': '0'}
    if any([v for v in argv if v.startswith('--' + key + '=')] != ['--' + key + '=' + value] for key, value in required.items()):
        raise ValueError('Coordinator optional feature switches differ from the required profile')
    if '0::' + manifest['cgroup'].removeprefix('/sys/fs/cgroup') not in cgroups.splitlines():
        raise ValueError('Coordinator is outside the charged server cgroup')
    checksum = hashlib.sha256()
    with binary.open('rb') as stream:
        for block in iter(lambda: stream.read(8 << 20), b''):
            checksum.update(block)
    if checksum.hexdigest() != manifest['coordinator_binary_sha256']:
        raise ValueError('Coordinator executable bytes changed after startup')
    return {'pid': pid, 'start_ticks': before, 'live_argv': argv, 'cgroups': cgroups,
            'binary_sha256': checksum.hexdigest(), 'metadata_backend': 'coordinator', 'verified': True}
