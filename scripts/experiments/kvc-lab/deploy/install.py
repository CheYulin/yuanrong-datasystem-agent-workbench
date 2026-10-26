"""Install the protected lab using existing verified SSH aliases; never copy personal keys."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tarfile
import time


def run(argv, **kwargs):
    return subprocess.run(argv, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout


def remote(host, code, data=None, python='python3'):
    command = shlex.join([python, '-c', code])
    output = run(['ssh', '-o', 'BatchMode=yes', host, command], input=json.dumps(data).encode() if data is not None else b'')
    return json.loads(output) if output.strip() else None


def transfer(host, source, destination, evidence, name):
    bundle = evidence / (name + '.tar.gz')
    with tarfile.open(bundle, 'w:gz') as archive:
        for path in sorted(source.rglob('*')):
            if any(part in ('__pycache__', '.qa', '.git') for part in path.relative_to(source).parts) or path.is_symlink():
                continue
            if path.is_file():
                archive.add(path, arcname=str(path.relative_to(source)), recursive=False)
    remote(host, "import json,sys;from pathlib import Path;p=Path(json.load(sys.stdin));p.mkdir(parents=True,exist_ok=True)", destination)
    # tar receives a trusted source archive; no request-supplied path reaches this command.
    command = shlex.join(['tar', '-xzf', '-', '-C', destination])
    with bundle.open('rb') as stream:
        run(['ssh', '-o', 'BatchMode=yes', host, command], stdin=stream)
    return hashlib.sha256(bundle.read_bytes()).hexdigest()



def source_bundle(root, evidence):
    archive = evidence / 'source.tar.gz'
    files = {}
    sources = [('scripts/experiments/kvc-lab', root), ('scripts/experiments/kvc-fair', root.parent / 'kvc-fair')]
    rfc = root.parents[2] / 'rfc/2026-10-26-kvc-control-lab'
    if rfc.is_dir():
        sources.append(('rfc/2026-10-26-kvc-control-lab', rfc))
    with tarfile.open(archive, 'w:gz') as output:
        for prefix, source in sources:
            for path in sorted(source.rglob('*')):
                if path.is_symlink() or not path.is_file() or any(part in ('__pycache__', '.qa', '.git') for part in path.relative_to(source).parts):
                    continue
                name = prefix + '/' + str(path.relative_to(source))
                files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
                output.add(path, arcname=name, recursive=False)
    identity = hashlib.sha256(archive.read_bytes()).hexdigest()
    record = dict(sha256=identity, files=files, scope='Installed controller/runner, reusable measurement scripts, and RFC; native product binaries and credentials excluded')
    (evidence / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    return identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--evidence', required=True)
    parser.add_argument('--study-dir', help='Frozen accepted report data directory; contains only study.json/audit.json/observability.json/study-manifest.json')
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    study = None
    if args.study_dir:
        import sys
        sys.path.insert(0, str(root))
        from validation.build_study import validate_directory
        study = validate_directory(args.study_dir).resolve(strict=True)
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=False)
    code_root, fair_root = '/opt/kvc-lab-20261026', '/opt/kvc-fair-20261026'
    # Refuse a hot replacement: source changes during an active SDK run invalidate provenance.
    safe = remote(args.target, "import json,sys,os;from pathlib import Path;p=Path('/home/cache/kvc-lab/active-execution.json');v=json.loads(p.read_text()) if p.exists() else {};pid=v.get('pid');live=bool(pid and Path('/proc/'+str(pid)+'/stat').exists() and not v.get('terminal'));print(json.dumps({'safe':not live}))")
    if not safe['safe']:
        raise RuntimeError('An active target execution exists; wait on its original handle before installing')
    remote(args.site, """import sqlite3,subprocess;from pathlib import Path
p=Path('/var/lib/kvc-lab/jobs.sqlite3')
if p.exists():
 db=sqlite3.connect(p,timeout=15);db.execute('BEGIN IMMEDIATE')
 if db.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]:raise RuntimeError('Site still owns a running job; installation is forbidden')
 subprocess.run(['systemctl','stop','kvc-lab-controller.service'],check=True)
 db.commit();db.close()
""")
    source_identity = source_bundle(root, evidence)
    hashes = {'source': source_identity}
    hashes['site_code'] = transfer(args.site, root, code_root, evidence, 'site-code')
    hashes['target_code'] = transfer(args.target, root, code_root, evidence, 'target-code')
    from render_docs import render
    render(evidence / 'docs')
    hashes['docs'] = transfer(args.site, evidence / 'docs', code_root + '/web/docs', evidence, 'docs-code')
    hashes['vendor'] = transfer(args.site, root.parent / 'kvc-fair/kvcbench/report/assets', code_root + '/web/vendor', evidence, 'vendor-code')
    if study is not None:
        snapshot = evidence / 'study-snapshot'
        snapshot.mkdir(mode=0o700)
        for path in sorted(study.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise ValueError('Frozen report source changed before transfer')
            (snapshot / path.name).write_bytes(path.read_bytes())
        validate_directory(snapshot)
        manifest = (snapshot / 'study-manifest.json').read_bytes()
        transaction = os.urandom(12).hex()
        staging = code_root + '/.study-stage-' + transaction
        remote(args.site, "import json,sys;from pathlib import Path;Path(json.load(sys.stdin)).mkdir(mode=0o700)", staging)
        hashes['study'] = transfer(args.site, snapshot, staging, evidence, 'study-data')
        published = remote(args.site, """import ctypes,hashlib,json,os,sys;from pathlib import Path
x=json.load(sys.stdin);stage=Path(x['stage']);destination=Path(x['destination']);backups=Path(x['backups'])
expected=x['files'];names=set(expected)|{'study-manifest.json'}
def verify(directory):
 if directory.is_symlink() or not directory.is_dir() or {p.name for p in directory.iterdir()}!=names:raise ValueError('Report directory file set mismatch')
 for path in directory.iterdir():
  if path.is_symlink() or not path.is_file():raise ValueError('Report entry must be a regular file')
  content=path.read_bytes();actual={'sha256':hashlib.sha256(content).hexdigest(),'size':len(content)}
  if path.name=='study-manifest.json':
   if actual!=x['manifest']:raise ValueError('Report manifest identity mismatch')
  elif actual!=expected[path.name]:raise ValueError('Report file identity mismatch: '+path.name)
verify(stage)
if destination.is_symlink() or destination.exists() and not destination.is_dir():raise ValueError('Existing report destination is not a real directory')
if backups.is_symlink():raise ValueError('Report backup directory cannot be a symlink')
backups.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(backups,0o700)
if stage.stat().st_dev!=destination.parent.stat().st_dev or backups.stat().st_dev!=stage.stat().st_dev:raise ValueError('Atomic report replacement requires a single filesystem')
backup=backups/x['transaction'];receipt=backups/(x['transaction']+'.json')
if backup.exists() or receipt.exists():raise ValueError('Report transaction identity already exists')
had_old=destination.exists();switched=False;old_location=stage
exchange=None
if had_old:
 exchange=getattr(ctypes.CDLL(None,use_errno=True),'renameat2',None)
 if exchange is None:raise RuntimeError('Atomic report directory exchange is unavailable')
 exchange.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint];exchange.restype=ctypes.c_int
def swap(left,right):
 if exchange(-100,os.fsencode(left),-100,os.fsencode(right),2)!=0:raise OSError(ctypes.get_errno(),'Atomic report directory exchange failed')
try:
 for path in stage.iterdir():os.chmod(path,0o644)
 os.chmod(stage,0o755)
 if had_old:swap(stage,destination)
 else:os.replace(stage,destination)
 switched=True;verify(destination)
 if had_old:
  os.replace(stage,backup);old_location=backup
 result={'installed':True,'directory':str(destination),'previous_backup':str(backup)if had_old else None,'manifest_sha256':x['manifest']['sha256'],'file_count':len(names),'atomic_exchange':had_old}
 temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(result,indent=2)+'\\n');os.chmod(temporary,0o600);os.replace(temporary,receipt)
except Exception as error:
 if switched:
  try:
   if had_old:swap(destination,old_location)
   else:os.replace(destination,stage)
  except Exception as rollback:raise RuntimeError('Report publish failed and rollback requires manual recovery: '+str(destination)+' / '+str(old_location))from rollback
 if stage.is_dir():os.chmod(stage,0o700)
 raise RuntimeError('Report publish failed; previous directory restored; staged data retained')from error
print(json.dumps(result))
""", dict(stage=staging, destination=code_root + '/web/data', backups=code_root + '/.study-backups',
          transaction=transaction, files=json.loads(manifest)['files'],
          manifest=dict(sha256=hashlib.sha256(manifest).hexdigest(), size=len(manifest))))
        (evidence / 'study-deploy-result.json').write_text(json.dumps(published, indent=2) + '\n')
    hashes['fair_code'] = transfer(args.target, root.parent / 'kvc-fair', fair_root, evidence, 'fair-code')
    remote(args.site, "from pathlib import Path;Path('/var/lib/kvc-lab/sources').mkdir(mode=0o700,parents=True,exist_ok=True)")
    for suffix in ['.tar.gz', '.json']:
        with (evidence / ('source' + suffix)).open('rb') as stream:
            destination = '/var/lib/kvc-lab/sources/' + source_identity + suffix
            run(['ssh', args.site, shlex.join(['tee', destination])], stdin=stream)
        remote(args.site, "import json,sys,os;os.chmod(json.load(sys.stdin),0o600)", destination)
    hostkey = run(['ssh', args.target, 'cat /etc/ssh/ssh_host_ed25519_key.pub']).decode().strip()
    resolved = {}
    for line in run(['ssh', '-G', args.target]).decode().splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in ('hostname', 'user', 'port'):
            resolved[parts[0]] = parts[1]
    key = remote(args.site, """import json,subprocess,os;from pathlib import Path
p=Path('/etc/kvc-lab');p.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(p,0o700)
f=p/'control-key'
if not f.exists():subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-C','kvc-lab-controller-20261026','-f',str(f)],check=True)
os.chmod(f,0o600);print(json.dumps({'public_key':Path(str(f)+'.pub').read_text().strip()}))""")
    remote(args.target, """import json,sys,os;from pathlib import Path
x=json.load(sys.stdin);p=Path('/etc/kvc-lab');p.mkdir(mode=0o700,parents=True,exist_ok=True)
f=p/'runner.json';f.write_text(json.dumps(x['config'],indent=2)+'\\n');os.chmod(f,0o600)
source=Path(x['code'])/'deploy/ssh_gateway.py';(Path(x['code'])/'ssh_gateway.py').write_bytes(source.read_bytes())
d=Path.home()/'.ssh';d.mkdir(mode=0o700,exist_ok=True);f=d/'authorized_keys';old=f.read_text() if f.exists() else ''
line='restrict,command="/usr/bin/python3 '+x['code']+'/ssh_gateway.py" '+x['key']
if line not in old.splitlines():
 if 'kvc-lab-controller-20261026' in old:raise RuntimeError('Different controller identity already registered')
 f.write_text(old.rstrip()+'\\n'+line+'\\n');os.chmod(f,0o600)
print(json.dumps({'installed':True}))""", dict(code=code_root, key=key['public_key'], config=dict(native_root=args.native_root, build_catalog=args.catalog, fair_root=fair_root, state_root='/home/cache/kvc-lab', cgroup_root='/sys/fs/cgroup/kvc-lab', max_server_cpus=24, max_client_cpus=12, max_memory_gib=80)))
    installed = remote(args.site, """import ast,json,sys,os,re,secrets,subprocess;from pathlib import Path
x=json.load(sys.stdin);private=Path('/etc/kvc-lab');private.mkdir(mode=0o700,parents=True,exist_ok=True)
(private/'known_hosts').write_text('kvc-lab-target '+x['hostkey']+'\\n')
r=x['resolved'];(private/'ssh_config').write_text('Host kvc-lab-target\\n  HostName '+r['hostname']+'\\n  Port '+r['port']+'\\n  User '+r['user']+'\\n  HostKeyAlias kvc-lab-target\\n  IdentitiesOnly yes\\n  IdentityFile /etc/kvc-lab/control-key\\n  UserKnownHostsFile /etc/kvc-lab/known_hosts\\n  StrictHostKeyChecking yes\\n')
os.chdir('/var/www/html/server');sys.path.insert(0,os.getcwd());from database import get_db
with get_db() as db:users=[row[0] for row in db.execute('SELECT username FROM users')]
js=Path('/var/www/html/assets/js/site.js').read_text();m=re.search(r"ADMIN_USER\\s*=\\s*['\\\"]([^'\\\"]+)['\\\"]",js)
allowed=[m.group(1)] if m and m.group(1) in users else users if len(users)==1 else []
if not allowed:raise RuntimeError('Cannot resolve existing site administrator')
f=private/'controller.json';settings=json.loads(f.read_text()) if f.exists() else {'csrf_secret':secrets.token_hex(32)}
settings.update(source_sha256=x['source_sha256'],state_root='/var/lib/kvc-lab',allowed_users=allowed,ssh_target='kvc-lab-target',ssh_gateway=True,ssh_identity='/etc/kvc-lab/control-key',ssh_known_hosts='/etc/kvc-lab/known_hosts',ssh_config='/etc/kvc-lab/ssh_config',remote_code_root=x['code'],remote_fair_root=x['fair'],remote_config='/etc/kvc-lab/runner.json')
f.write_text(json.dumps(settings,indent=2)+'\\n');os.chmod(f,0o600)
Path('/var/lib/kvc-lab').mkdir(mode=0o700,exist_ok=True)
app=Path('app.py');old=app.read_text();backup=private/'app.py.before'
if not backup.exists():backup.write_text(old)
begin='    # KVC_LAB_BEGIN';end='    # KVC_LAB_END'
if begin in old:
 left=old.index(begin);right=old.index(end,left)+len(end);old=old[:left]+old[right:]
block='''    # KVC_LAB_BEGIN
    import sys
    from pathlib import Path
    sys.path.insert(0, '/opt/kvc-lab-20261026')
    from auth import login_required
    from kvclab.service import LabService
    from kvclab.site_api import register_lab
    lab_settings = json.loads(Path('/etc/kvc-lab/controller.json').read_text())
    register_lab(app, login_required, set(lab_settings['allowed_users']), LabService(lab_settings), '/opt/kvc-lab-20261026/web')
    # KVC_LAB_END
'''
if old.count('    return app')!=1:raise RuntimeError('Unexpected existing app factory')
new=old.replace('    return app',block+'\\n    return app');ast.parse(new);app.write_text(new)
for name in ['known_hosts','ssh_config']:os.chmod(private/name,0o600)
unit='''[Unit]
Description=Authenticated KVC experiment controller
After=network-online.target
[Service]
Type=simple
WorkingDirectory=/opt/kvc-lab-20261026
ExecStart=/opt/yche-site-venv/bin/python -B -m kvclab.controller --config /etc/kvc-lab/controller.json
Restart=on-failure
RestartSec=3
TimeoutStopSec=300
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/kvc-lab
Environment=PYTHONDONTWRITEBYTECODE=1
[Install]
WantedBy=multi-user.target
'''
Path('/etc/systemd/system/kvc-lab-controller.service').write_text(unit)
subprocess.run(['/opt/yche-site-venv/bin/python','-B','-c','from app import create_app; a=create_app(); assert any(r.rule=="/site-api/kvc-lab/catalog" for r in a.url_map.iter_rules())'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','enable','kvc-lab-controller.service'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['systemctl','restart','kvc-lab-controller.service'],check=True)
subprocess.run(['systemctl','is-active','--quiet','kvc-lab-controller.service'],check=True)
subprocess.run(['systemctl','kill','--kill-who=main','--signal=HUP','yche-site-api.service'],check=True)
print(json.dumps({'installed':True,'api':'/site-api/kvc-lab','ui':'/site-api/kvc-lab/ui/','allowed_users_count':len(allowed)}))""", dict(code=code_root, fair=fair_root, hostkey=hostkey, resolved=resolved, source_sha256=source_identity))
    result = dict(installed=installed, bundle_sha256=hashes, duration_s=time.monotonic()-started)
    (evidence / 'install-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
