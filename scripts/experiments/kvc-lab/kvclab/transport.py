"""Fixed, private-configured SSH transport shared by controller and artifact reads."""
import json
import re
import shlex
import subprocess


def ssh_argv(settings, action):
    if action not in ('execute', 'state', 'stop', 'artifact') or not re.fullmatch(r'[A-Za-z0-9_.@-]+', settings['ssh_target']):
        raise ValueError('Invalid fixed SSH protocol')
    remote = ['env', 'PYTHONDONTWRITEBYTECODE=1',
              'PYTHONPATH=' + settings['remote_code_root'] + ':' + settings['remote_fair_root'],
              settings.get('remote_python', 'python3'), '-B', '-m', 'kvclab.remote',
              '--config', settings['remote_config'], '--action', action]
    options = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=4']
    if settings.get('ssh_config'):
        options.extend(['-F', settings['ssh_config']])
    if settings.get('ssh_identity'):
        options.extend(['-i', settings['ssh_identity']])
    if settings.get('ssh_known_hosts'):
        options.extend(['-o', 'UserKnownHostsFile=' + settings['ssh_known_hosts'], '-o', 'StrictHostKeyChecking=yes'])
    return options + [settings['ssh_target'], action if settings.get('ssh_gateway') else shlex.join(remote)]


def rpc(settings, action, request):
    completed = subprocess.run(ssh_argv(settings, action), input=json.dumps(request).encode(), stdout=subprocess.PIPE,
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
