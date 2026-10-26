"""One persistent job consumer; bounded SSH RPC never executes request supplied commands."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import subprocess
import time
from .service import LabService
from .store import TERMINAL


class Controller:
    def __init__(self, settings):
        self.settings = settings
        self.service = LabService(settings)
        self.store = self.service.store
        self.stopping = False
        if not re.fullmatch(r'[A-Za-z0-9_.@-]+', settings['ssh_target']):
            raise ValueError('Invalid configured SSH target')

    def argv(self, action):
        remote = ['env', 'PYTHONDONTWRITEBYTECODE=1',
                  'PYTHONPATH=' + self.settings['remote_code_root'] + ':' + self.settings['remote_fair_root'],
                  self.settings.get('remote_python', 'python3'), '-B', '-m', 'kvclab.remote',
                  '--config', self.settings['remote_config'], '--action', action]
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15',
                '-o', 'ServerAliveCountMax=4', self.settings['ssh_target'], shlex.join(remote)]

    def rpc(self, action, request):
        completed = subprocess.run(self.argv(action), input=json.dumps(request).encode(), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=35)
        messages = []
        for line in completed.stdout.decode(errors='replace').splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    messages.append(value)
            except ValueError:
                pass
        if completed.returncode or not messages:
            raise RuntimeError('远端控制命令未成功返回；未重放实验')
        return messages[-1]

    def record(self, job_id, payload):
        self.store.event(job_id, payload)
        root = self.service.root / 'jobs' / job_id
        root.mkdir(parents=True, exist_ok=True)
        with (root / 'events.jsonl').open('a') as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + '\n')
            stream.flush()

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
                    if complete:
                        self.record(job_id, complete)
                        self.store.finish(job_id, complete.get('status', 'failed'))
                        break
                    if state.get('running') is False:
                        self.record(job_id, dict(kind='log', message='原远端进程已消失且无终态产物；保留中断，不自动重放写请求'))
                        self.store.finish(job_id, 'interrupted', '远端原执行句柄已消失，未产生可验收终态')
                        break
                    if self.store.get(job_id)['cancel_requested']:
                        self.rpc('stop', {'job_id': job_id})
                except (RuntimeError, subprocess.TimeoutExpired, OSError) as error:
                    if time.monotonic() - last_notice > 30:
                        self.record(job_id, dict(kind='log', message='正在核实原远端任务状态；连接失败不视为终止，也不重新启动任务'))
                        last_notice = time.monotonic()
                time.sleep(2)

    def execute(self, job):
        job_id = job['id']
        root = self.service.root / 'jobs' / job_id
        root.mkdir(parents=True, exist_ok=True)
        request = {key: job[key] for key in ('action', 'config', 'plan')}
        request['job_id'] = job_id
        (root / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2))
        reproduce = '#!/usr/bin/env bash\nset -euo pipefail\n# Use a fresh job ID for a new experiment; the original identity is replay-protected.\n' + shlex.join(self.argv('execute')) + " <<'KVC_REQUEST'\n" + json.dumps(request, ensure_ascii=False, indent=2) + '\nKVC_REQUEST\n'
        (root / 'reproduce.sh').write_text(reproduce)
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
                                self.record(job_id, event)
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
            self.store.finish(job_id, final['status'], final.get('error'))
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
