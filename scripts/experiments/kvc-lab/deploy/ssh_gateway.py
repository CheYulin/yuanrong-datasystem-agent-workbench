"""Forced-command SSH entry; the installed copy receives its fixed local config."""
import os
from pathlib import Path
import sys

command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
if command not in ('execute', 'state', 'stop', 'artifact'):
    print('Only the fixed kvc-lab protocol is supported', file=sys.stderr)
    raise SystemExit(126)
root = Path(__file__).resolve().parent
os.environ['PYTHONPATH'] = str(root) + ':' + '/opt/kvc-fair-20261026'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.execv(sys.executable, [sys.executable, '-B', '-m', 'kvclab.remote', '--config', '/etc/kvc-lab/runner.json', '--action', command])
