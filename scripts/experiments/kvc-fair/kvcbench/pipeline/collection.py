"""Transfer a completed snapshot and verify its preserved remote receipt graph."""
import os
from pathlib import Path
import re

from kvcbench.collect.transport import verify_received
from kvcbench.platform.command import execute
from kvcbench.platform.files import atomic_json


def receive(host, remote_snapshot, remote_receipt, out):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', host):
        raise ValueError('Use an explicit SSH host alias')
    for value in (remote_snapshot, remote_receipt):
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(path)):
            raise ValueError('Explicit safe absolute remote evidence paths required')
    out = Path(out).absolute()
    if out.exists() or out.is_symlink() or any(path.is_symlink() for path in out.parents):
        raise FileExistsError('Use a fresh local receive directory')
    out.mkdir(parents=True)
    for name, remote in [('snapshot', remote_snapshot), ('receipt', remote_receipt)]:
        destination = out / name
        destination.mkdir()
        record = execute(['rsync', '-aSsz', '--', host + ':' + str(remote) + '/', str(destination) + '/'],
            cwd=out, env=dict(os.environ), log=out / 'commands' / (name + '.log'), record=out / 'commands' / (name + '-command.json'))
        if record['returncode'] != 0 or record['error']:
            raise ValueError('Evidence transfer failed; partial files retained without acceptance')
    try:
        verified = verify_received(out / 'snapshot', out / 'receipt')
    except Exception as error:
        atomic_json(out / 'failure.json', {'error': str(error)}, exclusive=True)
        raise
    atomic_json(out / 'transport-verification.json', verified, exclusive=True)
    return {**verified, 'out': str(out)}
