"""Own suite-long monitors; seal startup snapshots separately from final streams."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from kvcbench.core.timing import started, finished
from kvcbench.platform.cgroup import placement
from kvcbench.platform.command import stop_owned
from kvcbench.platform.files import atomic_json, reference


REQUIRED_TOOLS = ('iostat', 'mpstat', 'pidstat', 'dstat')


class Session:
    def __init__(self, out, cgroup, *, interval=.05, cpus=tuple(range(0, 32, 2)), required_tools=REQUIRED_TOOLS):
        self.out, self.cgroup = Path(out), Path(cgroup)
        self.interval, self.cpus, self.required_tools = interval, tuple(cpus), tuple(required_tools)
        self.children, self.stream = {}, None
        self.begin = None

    def start(self, timeout=15):
        self.out.mkdir(parents=True, exist_ok=False)
        self.begin = started()
        self.stream = (self.out / 'controller.log').open('xb')
        package_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ, PYTHONPATH=str(package_root), PYTHONDONTWRITEBYTECODE='1', LC_ALL='C', TZ='UTC')
        commands = {
            'host': [sys.executable, '-m', 'kvcbench.observe.host', '--out', str(self.out),
                     '--interval', str(self.interval), '--cgroup-root', str(self.cgroup)],
            'interference': [sys.executable, '-m', 'kvcbench.observe.interference', '--out', str(self.out / 'interference.jsonl'),
                             '--cgroup-root', str(self.cgroup), '--cpus', ','.join(map(str, self.cpus))]}
        try:
            for name, argv in commands.items():
                self.children[name] = subprocess.Popen(argv, stdout=self.stream, stderr=subprocess.STDOUT,
                    env=env, start_new_session=True, preexec_fn=placement(self.cgroup / 'monitor'))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if any(child.poll() is not None for child in self.children.values()):
                    raise RuntimeError('A suite monitor exited before readiness')
                try:
                    manifest = json.loads((self.out / 'monitor-manifest.json').read_text())
                    lines = (self.out / 'samples.jsonl').read_text().splitlines()
                    first, latest = json.loads(lines[0]), json.loads(lines[-1])
                    tools = manifest['commands']
                    groups = first['groups']
                    counters_ready = all(isinstance(groups[role]['cpu.stat']['usage_usec'], int)
                                         and isinstance(groups[role]['memory.current'], int) for role in ('server', 'client'))
                    tools_ready = all(tools.get(name, {}).get('available') is True and
                                      tools[name].get('pid') and name in latest.get('tool_returncodes', {})
                                      and latest['tool_returncodes'][name] is None
                                      for name in self.required_tools)
                    interference_ready = bool((self.out / 'interference.jsonl').stat().st_size)
                    if counters_ready and tools_ready and interference_ready:
                        atomic_json(self.out / 'ready.json', {'schema_version': 1, 'ready': True,
                            'scope': 'startup only; full sampling coverage remains unverified',
                            'cgroup': str(self.cgroup), 'cpus': list(self.cpus), 'required_tools': list(self.required_tools),
                            'children': {name: {'pid': child.pid, 'argv': commands[name]} for name, child in self.children.items()},
                            'first_sample': first, 'readiness_sample': latest, 'startup_manifest': manifest, **finished(self.begin)}, exclusive=True)
                        return self.out / 'ready.json'
                except (OSError, ValueError, KeyError, TypeError, IndexError):
                    pass
                time.sleep(.05)
            raise TimeoutError('Required monitor samples/tools did not become ready')
        except BaseException:
            self.stop()
            raise

    def stop(self):
        exits = {}
        for name, child in self.children.items():
            forced = False
            if child.poll() is None:
                try:
                    child.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    forced = True
                    stop_owned(child)
            exits[name] = {'returncode': child.returncode, 'forced': forced}
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.begin is None:
            return None
        files = [reference(self.out, p.relative_to(self.out)) for p in sorted(self.out.iterdir())
                 if p.is_file() and p.name != 'final.json']
        result = {'schema_version': 1, 'closed': all(child.poll() is not None for child in self.children.values()),
                  'normal_exit': set(exits) == {'host', 'interference'} and all(row['returncode'] == 0 and not row['forced'] for row in exits.values()),
                  'children': exits, 'files': files, **finished(self.begin)}
        atomic_json(self.out / 'final.json', result, exclusive=True)
        self.begin = None
        return result
