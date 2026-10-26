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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--native-root', required=True)
    parser.add_argument('--catalog', required=True)
    parser.add_argument('--evidence', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(__file__).resolve().parents[1]
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=False)
    code_root, fair_root = '/opt/kvc-lab-20261026', '/opt/kvc-fair-20261026'
    # Refuse a hot replacement: source changes during an active SDK run invalidate provenance.
    safe = remote(args.target, "import json,sys,os;from pathlib import Path;p=Path('/home/cache/kvc-lab/active-execution.json');v=json.loads(p.read_text()) if p.exists() else {};pid=v.get('pid');live=bool(pid and Path('/proc/'+str(pid)+'/stat').exists() and not v.get('terminal'));print(json.dumps({'safe':not live}))")
    if not safe['safe']:
        raise RuntimeError('An active target execution exists; wait on its original handle before installing')
    hashes = {}
    hashes['site_code'] = transfer(args.site, root, code_root, evidence, 'site-code')
    hashes['target_code'] = transfer(args.target, root, code_root, evidence, 'target-code')
    hashes['fair_code'] = transfer(args.target, root.parent / 'kvc-fair', fair_root, evidence, 'fair-code')
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
settings.update(state_root='/var/lib/kvc-lab',allowed_users=allowed,ssh_target='kvc-lab-target',ssh_gateway=True,ssh_identity='/etc/kvc-lab/control-key',ssh_known_hosts='/etc/kvc-lab/known_hosts',ssh_config='/etc/kvc-lab/ssh_config',remote_code_root=x['code'],remote_fair_root=x['fair'],remote_config='/etc/kvc-lab/runner.json')
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
subprocess.run(['systemctl','enable','--now','kvc-lab-controller.service'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['systemctl','kill','--kill-who=main','--signal=HUP','yche-site-api.service'],check=True)
print(json.dumps({'installed':True,'api':'/site-api/kvc-lab','ui':'/site-api/kvc-lab/ui/','allowed_users_count':len(allowed)}))""", dict(code=code_root, fair=fair_root, hostkey=hostkey, resolved=resolved))
    result = dict(installed=installed, bundle_sha256=hashes, duration_s=time.monotonic()-started)
    (evidence / 'install-result.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
