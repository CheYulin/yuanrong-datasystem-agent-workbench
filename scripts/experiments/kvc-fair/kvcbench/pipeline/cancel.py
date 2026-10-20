"""Request guarded cancellation of this control's currently owned smoke/measure process."""
import os
from pathlib import Path
import signal
import socket

from kvcbench.core.timing import started, finished
from kvcbench.platform.files import atomic_json, read_json, real_directory, identity


def cancel(control, out, reason):
    control, out = real_directory(control), Path(out).absolute()
    if not reason.strip() or out.exists() or out.is_symlink() or any(p.is_symlink() for p in out.parents):
        raise ValueError('A reason and fresh cancellation record directory are required')
    spec = read_json(control / 'spec.json')
    candidates = []
    for step, action in [('W07.smoke', 'smoke'), ('W09', 'measure')]:
        for path in (control / 'workflow/steps' / step).glob('.partial-*/start.json'):
            candidates.append((path, action))
    if len(candidates) != 1:
        raise ValueError('Exactly one active owned smoke/measurement attempt is required')
    path, action = candidates[0]
    record = read_json(path)
    if (record['clock_domain']['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip()
            or record['clock_domain']['host'] != socket.gethostname()
            or record['identity']['run_id'] != spec['run_id'] or record['identity']['profile'] != spec['profile']):
        raise ValueError('Attempt belongs to another host boot')
    pid = record['clock_domain']['pid']
    with os.fdopen(os.pidfd_open(pid), 'rb') as descriptor:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        ticks = fields[19]
        argv = Path(f'/proc/{pid}/cmdline').read_bytes().decode().strip('\0').split('\0')
        cwd = Path(f'/proc/{pid}/cwd').resolve(strict=True)
        expected = ['-m', 'kvcbench', 'workflow', action, '--control', str(control)]
        age_at_step = record['start_mono_ns'] / 1e9 - int(ticks) / os.sysconf('SC_CLK_TCK')
        if (fields[0] == 'Z' or not 0 <= age_at_step <= 60 or cwd != Path(spec['root'])
                or argv[1:] not in [expected, ['-B', *expected]]
                or Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19] != ticks):
            raise ValueError('PID lifetime, command or cwd differs from the owned workflow attempt')
        out.mkdir(parents=True)
        begin = started()
        intent = {'run_id': spec['run_id'], 'control': str(control), 'attempt_start': str(path),
                  'pid': pid, 'start_ticks': ticks, 'argv': argv, 'cwd': str(cwd), 'reason': reason,
                  'signal': 'SIGINT', 'scope': 'Only the live owned workflow process. SDK cleanup and restoration remain independently verified by its W10/W11 or explicit recovery; no request replay.',
                  'validator_source': identity(Path(__file__)), **begin}
        atomic_json(out / 'intent.json', intent, exclusive=True)
        signal.pidfd_send_signal(descriptor.fileno(), signal.SIGINT)
        result = {**intent, 'signal_sent': True, 'cleanup_verified': False, **finished(begin)}
        atomic_json(out / 'result.json', result, exclusive=True)
        return result
