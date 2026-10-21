import urllib.request,hashlib,gzip,json
from pathlib import Path
root=Path('/home/t14s/workspace/scratch/2026-10-21-kvc-layout-fix');base='https://yche.me/perf/kvc-coordinator-modular-20261021/';results=[]
for remote,local in [('assets/report.css','report.css'),('docs/_reader/reader.css','documents.css')]:
 for suffix in ['', '.gz']:
  with urllib.request.urlopen(urllib.request.Request(base+remote+suffix+'?v=layout-20261021-02',headers={'User-Agent':'Mozilla/5.0','Cache-Control':'no-cache','Accept-Encoding':'identity'}),timeout=45) as response:data=response.read()
  if suffix:data=gzip.decompress(data)
  assert data==(root/local).read_bytes(),remote+suffix
  results.append({'path':remote+suffix,'sha256':hashlib.sha256(data).hexdigest(),'passed':True})
(root/'public-css-result.json').write_text(json.dumps({'passed':True,'results':results},indent=2)+'\n');print('4 public CSS/plain-gzip byte comparisons passed')
