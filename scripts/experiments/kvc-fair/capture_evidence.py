#!/usr/bin/env python3
"""Copy small source-build logs and script snapshots only after measurements stop."""
import argparse
import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--suite', type=Path, required=True)
    parser.add_argument('--cgroup-root', type=Path, default=Path('/sys/fs/cgroup/kvcfair20261020'))
    args = parser.parse_args()
    root, suite = args.root.resolve(), args.suite.resolve()
    lock = (root / 'suite.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    summary = json.loads((suite / 'summary.json').read_text())
    if not summary.get('ended'):
        raise RuntimeError('Evidence snapshot requires a finished suite')
    for role in ['server', 'client', 'monitor']:
        if (args.cgroup_root / role / 'cgroup.procs').read_text().strip():
            raise RuntimeError('Experiment cgroup remains populated: ' + role)
    destination = suite / 'build-and-script-evidence'
    if destination.exists():
        record = json.loads((destination / 'snapshot-manifest.json').read_text())
        for row in record['files']:
            if hashlib.sha256((destination / row['path']).read_bytes()).hexdigest() != row['sha256']:
                raise RuntimeError('Saved evidence snapshot hash mismatch: ' + row['path'])
        print(json.dumps({'output': str(destination), 'already_captured': True, 'files': len(record['files'])}))
        return
    sources = set(root.glob('*.py'))
    for sub in ['scripts/datasystem', 'mooncake', 'lmcache', 'configs']:
        sources.update(p for p in (root / sub).glob('*') if p.is_file() and p.suffix in {'.py', '.sh', '.lock', '.json', '.log', '.txt', '.exit', '.sha256'})
    sources.add(root / 'products.json')
    for sub in ['datasystem/.evidence', 'mooncake/.evidence', 'lmcache/.evidence']:
        sources.update(p for p in (root / sub).rglob('*') if p.is_file() and p.suffix in {'.log', '.txt', '.json', '.exit', '.sha256'})
    staging = Path(tempfile.mkdtemp(prefix='.evidence-snapshot-', dir=suite))
    rows = []
    initial = json.loads((suite / 'script-hashes.json').read_text())
    try:
        for source in sorted(sources):
            if source.is_symlink():
                continue
            if source.stat().st_size > 20 << 20:
                raise RuntimeError('Unexpectedly large evidence file: ' + str(source))
            relative = source.relative_to(root)
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            rows.append({'source': str(source), 'path': str(relative), 'sha256': digest,
                         'bytes': target.stat().st_size, 'matches_current_session_script_hash': digest == initial[str(relative)] if str(relative) in initial else None})
        (staging / 'snapshot-manifest.json').write_text(json.dumps({
            'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': rows,
            'scope': 'Snapshot of scripts present and source-build/smoke evidence after measurements stopped. Match against per-session script hashes and approved changes to determine what executed; post-run helpers are not implied to have run during measurement.'}, indent=2))
        staging.rename(destination)
    except BaseException:
        shutil.rmtree(staging)
        raise
    print(json.dumps({'output': str(destination), 'files': len(rows)}))


if __name__ == '__main__':
    main()
