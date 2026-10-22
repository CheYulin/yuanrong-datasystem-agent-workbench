import concurrent.futures, hashlib, json, time, urllib.request
from pathlib import Path
root=Path('/home/t14s/workspace/scratch/2026-10-22-kvc-result-coverage');start=time.monotonic();proof=json.loads((root/'exist-source-audit.json').read_text())
def verify(record):
 url='https://yche.me/perf/kvc-coordinator-modular-20261021/'+record['evidence']
 with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept-Encoding':'identity'}),timeout=45) as response:
  data=response.read();status=response.status
 observed=hashlib.sha256(data).hexdigest();native=json.loads(data)
 passed=status==200 and observed==record['sha256'] and native['valid']==(record['status']=='valid')
 return {**record,'url':url,'http_status':status,'observed_sha256':observed,'native_valid':native['valid'],'passed':passed}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(verify,proof['records']))
out={'passed':len(results)==72 and all(r['passed'] for r in results),'duration_s':time.monotonic()-start,'records':results}
(root/'public-exist-audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:out[k] for k in ['passed','duration_s']}));raise SystemExit(0 if out['passed'] else 1)
