"""Materialize explicit runtime entry files without exposing old source-root commands."""
from pathlib import Path
import shutil

from kvcbench.platform.files import regular, identity

ENTRYPOINTS = ('batch_benchmark', 'batch_gate', 'batch_datasystem', 'batch_mooncake', 'batch_lmcache',
              'cgroup', 'benchmark', 'monitor', 'light_monitor', 'profile_put', 'batch_ds_deadline_diagnostic',
              'batch_ds_diagnostic_runner', 'batch_diagnostic', 'batch_queue_probe', 'tcp_probe_ds_sizes', 'tcp_wire_probe')
PRODUCTS = {'datasystem': ('runtime.py', 'adapter_datasystem.py'),
            'mooncake': ('adapter.py', 'peer.py', 'process_identity.py'),
            'lmcache': ('runtime.py', 'adapter_lmcache.py', 'adapter_lmcache_tcp.py')}
SHELLS = {'mooncake': ('env.sh', 'start.sh', 'stop.sh', 'launch_master.sh'),
          'lmcache': ('start_server.sh', 'start_server_tcp.sh')}


def available(bundle):
    return (Path(bundle) / 'kvcbench/run/entrypoints').is_dir()


def datasystem_runtime_source(bundle):
    path = 'kvcbench/run/entrypoints/products/datasystem/runtime.py' if available(bundle) else 'datasystem/runtime.py'
    try:
        return regular(bundle, path)
    except FileNotFoundError as error:
        raise ValueError("An explicit DataSystem runtime resource is required") from error


def materialize(bundle, out):
    bundle, out = Path(bundle), Path(out)
    selected = {name + '.py': 'kvcbench/run/entrypoints/' + name + '.py' for name in ENTRYPOINTS}
    for product, files in PRODUCTS.items():
        prefix = 'scripts/datasystem' if product == 'datasystem' else product
        selected.update({prefix + '/' + name: 'kvcbench/run/entrypoints/products/' + product + '/' + name for name in files})
    for product, files in SHELLS.items():
        selected.update({product + '/' + name: 'kvcbench/run/products/' + product + '/' + name for name in files})
    for destination, source in selected.items():
        original = regular(bundle, source)
        before = identity(original)
        target = out / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if identity(original) != before or identity(target) != before:
            raise ValueError('Deployment entry resource changed during materialization: ' + source)
        if target.suffix == '.sh':
            target.chmod(target.stat().st_mode & 0o777 | 0o111)
    return selected
