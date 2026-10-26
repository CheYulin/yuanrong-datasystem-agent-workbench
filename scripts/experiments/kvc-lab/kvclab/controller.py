"""One persistent job consumer; bounded SSH RPC never executes request supplied commands."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import tempfile
import time
from .service import LabService
from .store import TERMINAL
from . import spec
from .transport import ssh_argv, rpc


class Controller:
    def __init__(self, settings):
        self.settings = settings
        self.service = LabService(settings)
        self.store = self.service.store
        self.stopping = False
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', settings['ssh_target']):
            raise ValueError('Invalid configured SSH target')

    def argv(self, action):
        return ssh_argv(self.settings, action)

    def rpc(self, action, request):
        return rpc(self.settings, action, request)

    def record(self, job_id, payload, remote=False):
        if not self.store.event(job_id, payload, remote=remote):
            return False
        root = self.service.root / 'jobs' / job_id
        root.mkdir(parents=True, exist_ok=True)
        with (root / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + '\n')
            stream.flush()
        return True

    def backfill(self, job_id):
        snapshot = self.rpc('artifact', {'job_id': job_id, 'name': 'events.jsonl'})
        data = base64.b64decode(snapshot['data'], validate=True)
        if len(data) != snapshot['size'] or hashlib.sha256(data).hexdigest() != snapshot['sha256']:
            raise RuntimeError('远端事件日志传输校验失败')
        receipt = self.store.ingest_remote_log(job_id, data)
        root = self.service.root / 'jobs' / job_id
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=root, delete=False) as stream:
            for event in self.store.events(job_id):
                stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        temporary.replace(root / 'events.jsonl')
        (root / 'recovery-events.json').write_text(json.dumps(receipt, indent=2) + '\n')
        return receipt

    def accept_complete(self, job_id, complete):
        status = complete.get('status')
        if status not in TERMINAL:
            raise RuntimeError('远端任务终态无效')
        error = complete.get('error')
        deployment = complete.get('deployment', {})
        cleanup_error = complete.get('cleanup_error') or complete.get('cleanup_errors')
        cleanup_error = cleanup_error or isinstance(error, str) and 'Cleanup error:' in error
        if cleanup_error or status in ('failed', 'cancelled', 'interrupted') and deployment.get('active'):
            self.store.cleanup_required(True)
        self.record(job_id, complete, remote=True)
        self.store.finish(job_id, status, error)
        if self.store.get(job_id)['action'] == 'stop' and status == 'succeeded' and not cleanup_error and not deployment.get('active'):
            self.store.cleanup_required(False)

    def reproduce_script(self, request):
        execute = shlex.join(self.argv('execute'))
        original = shlex.join(self.argv('state'))
        return ('#!/usr/bin/env bash\nset -euo pipefail\n'
                '# Run on the installed site host using its private SSH configuration.\n'
                '# Default: new UUID and a new experiment. --original: read original state only.\n'
                'case "${1:-}" in\n'
                f'  "") kvc_replay_mode=new; kvc_replay_argv=({execute}) ;;\n'
                f'  --original) kvc_replay_mode=original; kvc_replay_argv=({original}) ;;\n'
                '  *) echo "Usage: bash reproduce.sh [--original]" >&2; exit 64 ;;\n'
                'esac\n'
                'python3 - "$kvc_replay_mode" <<\'KVC_REQUEST_PY\' | "${kvc_replay_argv[@]}"\n'
                'import json, sys, uuid\n'
                f'request = json.loads({json.dumps(request, ensure_ascii=False)!r})\n'
                'if sys.argv[1] == "original":\n'
                '    request = {"job_id": request["job_id"]}\n'
                'else:\n'
                '    request["job_id"] = uuid.uuid4().hex\n'
                'print(json.dumps(request, ensure_ascii=False))\n'
                'KVC_REQUEST_PY\n')

    def source_provenance(self, job_id):
        identity = self.settings.get('source_sha256')
        if not isinstance(identity, str) or re.fullmatch(r'[0-9a-f]{64}', identity) is None:
            return
        source = self.service.root / 'sources' / (identity + '.tar.gz')
        if not source.is_file() or source.is_symlink():
            return
        actual = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                actual.update(block)
        if actual.hexdigest() != identity:
            raise RuntimeError('安装源码包身份不匹配，拒绝绑定任务')
        self.record(job_id, dict(kind='provenance', source_sha256=identity))

    def recover(self):
        with self.store.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE status='running'")]
        for job_id in ids:
            last_notice = 0
            while not self.stopping:
                try:
                    state = self.rpc('state', {'job_id': job_id})
                    root = self.service.root / 'jobs' / job_id
                    root.mkdir(parents=True, exist_ok=True)
                    (root / 'remote-state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
                    complete = state.get('complete')
                    has_events = any(row.get('name') == 'events.jsonl' for row in state.get('artifacts', []))
                    if complete or state.get('running') or has_events:
                        receipt = self.backfill(job_id)
                        if complete and receipt['partial_bytes']:
                            raise RuntimeError('远端终态事件日志仍含不完整记录')
                    if complete:
                        self.accept_complete(job_id, complete)
                        break
                    if state.get('running') is False:
                        self.record(job_id, dict(kind='log', message='原远端进程已消失且无终态产物；保留中断，不自动重放写请求'))
                        self.store.cleanup_required(True)
                        plan = spec.preview_plan({})
                        self.store.submit(self.store.get(job_id)['user'], 'stop', plan['config'], plan, 'recover-' + job_id)
                        self.store.finish(job_id, 'interrupted', '远端原执行句柄已消失；回收门禁已启用，未重放写请求')
                        break
                    if self.store.get(job_id)['cancel_requested']:
                        self.rpc('stop', {'job_id': job_id})
                except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as error:
                    if time.monotonic() - last_notice > 30:
                        self.record(job_id, dict(kind='log', message='正在核实原远端任务状态与完整事件；连接或校验失败不视为终止，也不重新启动任务'))
                        last_notice = time.monotonic()
                time.sleep(2)

    def execute(self, job):
        job_id = job['id']
        root = self.service.root / 'jobs' / job_id
        root.mkdir(parents=True, exist_ok=True)
        request = {key: job[key] for key in ('action', 'config', 'plan')}
        request['job_id'] = job_id
        (root / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        (root / 'reproduce.sh').write_text(self.reproduce_script(request))
        self.source_provenance(job_id)
        final = None
        cancel_sent = False
        with (root / 'ssh.stderr.log').open('ab') as errors:
            process = subprocess.Popen(self.argv('execute'), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
            process.stdin.write(json.dumps(request).encode())
            process.stdin.close()
            os.set_blocking(process.stdout.fileno(), False)
            poller = selectors.DefaultSelector()
            poller.register(process.stdout, selectors.EVENT_READ)
            pending = b''
            try:
                while True:
                    for key, _ in poller.select(timeout=.5):
                        part = os.read(key.fd, 65536)
                        if part:
                            pending += part
                            if len(pending) > 16 * 1024 * 1024:
                                raise RuntimeError('远端单行事件超出上限')
                            while b'\n' in pending:
                                line, pending = pending.split(b'\n', 1)
                                try:
                                    event = json.loads(line)
                                except ValueError:
                                    event = dict(kind='log', message=line.decode(errors='replace')[:5000])
                                if not isinstance(event, dict):
                                    continue
                                self.record(job_id, event, remote=True)
                                if event.get('kind') == 'complete':
                                    final = event
                        else:
                            poller.unregister(key.fileobj)
                    if (self.stopping or self.store.get(job_id)['cancel_requested']) and not cancel_sent:
                        try:
                            self.rpc('stop', {'job_id': job_id})
                            cancel_sent = True
                            self.record(job_id, dict(kind='log', message='已请求取消，仅等待原远端进程清理并终止'))
                        except (RuntimeError, subprocess.TimeoutExpired, OSError):
                            pass
                    if process.poll() is not None and not poller.get_map():
                        break
                returncode = process.wait()
            finally:
                poller.close()
                process.stdout.close()
        if final and final.get('status') in TERMINAL and (returncode == 0 or final['status'] != 'succeeded'):
            self.accept_complete(job_id, final)
        else:
            self.record(job_id, dict(kind='log', message='SSH已结束但未收到可验收终态；核对同一远端任务，禁止自动重跑'))
            self.recover()

    def loop(self, once=False):
        lock = (self.service.root / 'controller.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.recover()
        while not self.stopping:
            job = self.store.claim()
            if job:
                try:
                    self.execute(job)
                except Exception as error:
                    self.record(job['id'], dict(kind='log', message='控制器异常，开始核实原任务终态；不会重放写请求'))
                    self.recover()
            elif not once:
                time.sleep(1)
            if once:
                break


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    settings = json.loads(Path(args.config).read_text())
    controller = Controller(settings)
    def stop(signum, frame):
        controller.stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    controller.loop(once=args.once)


if __name__ == '__main__':
    main()
