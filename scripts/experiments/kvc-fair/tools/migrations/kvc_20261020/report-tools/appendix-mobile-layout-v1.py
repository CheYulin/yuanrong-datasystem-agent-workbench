patches={'.build-appendix table{width:100%;': '.build-appendix table{width:100%;min-width:760px;', '<table><thead><tr><th>产品</th><th>实际源码 tag</th>': '<table id="appendix-version-table"><thead><tr><th>产品</th><th>实际源码 tag</th>', '.build-appendix a{overflow-wrap:anywhere}': '.build-appendix a{overflow-wrap:anywhere}#appendix-version-table td:nth-child(-n+2){white-space:nowrap}'}

from pathlib import Path
import hashlib,gzip,json,os,tempfile,fcntl
sha=lambda x:hashlib.sha256(x).hexdigest()
lock=Path('/tmp/kvc-yche-publish.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
updates={};before={};rows=[]
for name,expected in [('kvc-tcp-20261020','3d742eed0520b53ab21636403e0a24b6e47df09a136cdc3797cdbc220891252e'),('kvc-batch-tcp-20261020','c9fdcc570191693b5f2793a67c2fe40f35eb39d09b3c604844fbeb9893b476fd')]:
 root=Path('/var/www/html/perf')/name
 old={n:(root/n).read_bytes() for n in ['index.html','index.html.gz','SHA256SUMS']}
 assert sha(old['index.html'])==expected
 entries={n:h for h,n in (line.split('  ',1) for line in old['SHA256SUMS'].decode().splitlines())}
 text=old['index.html'].decode()
 for a,b in patches.items():assert text.count(a)==1;text=text.replace(a,b)
 changed={'index.html':text.encode(),'index.html.gz':gzip.compress(text.encode(),mtime=0)}
 for n,v in old.items():changed['build-appendix/mobile-layout/'+expected+'/'+n]=v
 changed['build-appendix/mobile-layout.json']=(json.dumps({'kind':'mobile_version_table_readability','prior_html_sha256':expected,'scope':'Horizontally scroll tables on mobile; keep product and tag intact','data_and_zip_changed':False},indent=2)+'\n').encode()
 for n,v in changed.items():
  p=root/n;assert not p.exists() or n in old
  updates[p]=v;entries[n]=sha(v)
 updates[root/'SHA256SUMS']=''.join(h+'  '+n+'\n' for n,h in sorted(entries.items())).encode()
 before.update({root/n:v for n,v in old.items()})
 rows.append({'report':name,'html_sha256':sha(text.encode()),'manifest_entries':len(entries)})
def write(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);fd,tmp=tempfile.mkstemp(prefix='.appendix-mobile-',dir=p.parent)
 with os.fdopen(fd,'wb') as f:f.write(v);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,0o644);os.replace(tmp,p)
written=[]
try:
 for p,v in updates.items():
  if p in before:assert p.read_bytes()==before[p]
  write(p,v);written.append(p)
except BaseException:
 for p in reversed(written):
  if p in before:write(p,before[p])
  else:p.unlink()
 raise
print(json.dumps({'passed':True,'reports':rows}))
