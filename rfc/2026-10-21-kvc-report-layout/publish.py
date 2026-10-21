import pathlib,hashlib,gzip,json,datetime,os
root=pathlib.Path('/var/www/html/perf/kvc-coordinator-modular-20261021')
revision='layout-20261021-02'
backup=pathlib.Path('/home/cache/kvc-presentation-revisions')/revision
backup.mkdir(parents=True,exist_ok=False)
updates={'assets/report.css':pathlib.Path('/tmp/report.css').read_bytes(),'docs/_reader/reader.css':pathlib.Path('/tmp/documents.css').read_bytes()}
for name in ['index.html', 'docs/index.html', 'docs/build.html', 'docs/results.html', 'docs/scripts-design.html', 'docs/test-cases.html']:
 old=(root/name).read_bytes()
 needle=b'assets/report.css' if name=='index.html' else b'_reader/reader.css'
 assert old.count(needle)==1,name
 updates[name]=old.replace(needle,needle+b'?v=layout-20261021-02')
sha=lambda b:hashlib.sha256(b).hexdigest()
oldmanifest=(root/'SHA256SUMS').read_bytes();records={line.split('  ',1)[1]:line.split('  ',1)[0] for line in oldmanifest.decode().splitlines()}
for name in updates:
 assert sha((root/name).read_bytes())==records[name],name
for name in ['data.json','reproduce.zip']:
 h=hashlib.sha256()
 with (root/name).open('rb') as f:
  for part in iter(lambda:f.read(8*1024*1024),b''):h.update(part)
 assert h.hexdigest()==records[name],name
for name in [*updates,'SHA256SUMS']:
 p=backup/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/name).read_bytes())
 if (root/(name+'.gz')).exists():(backup/(name+'.gz')).write_bytes((root/(name+'.gz')).read_bytes())
receipt={'revision':revision,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'CSS presentation and versioned stylesheet links only; experimental data, archived reproduction ZIP and original acceptance receipts unchanged. Original ZIP reproduces original presentation; apply CSS revision for updated layout.','baseline_manifest_sha256':sha(oldmanifest),'changes':{n:{'before':records[n],'after':sha(b)} for n,b in updates.items()},'unchanged':{n:records[n] for n in ['data.json','reproduce.zip']}}
updates['provenance/'+revision+'/SHA256SUMS.before']=oldmanifest
updates['provenance/'+revision+'/revision.json']=(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n').encode()
for name,b in list(updates.items()):
 updates[name+'.gz']=gzip.compress(b,mtime=0)
for name,b in updates.items():
 p=root/name;p.parent.mkdir(parents=True,exist_ok=True);temp=p.with_name(p.name+'.layout-tmp');temp.write_bytes(b);temp.chmod(0o644);os.replace(temp,p);records[name]=sha(b)
manifest=''.join(f'{v}  {k}\n' for k,v in sorted(records.items()) if k not in ['SHA256SUMS','SHA256SUMS.gz']).encode()
for name,b in [('SHA256SUMS',manifest),('SHA256SUMS.gz',gzip.compress(manifest,mtime=0))]:
 p=root/name;temp=p.with_name(p.name+'.layout-tmp');temp.write_bytes(b);temp.chmod(0o644);os.replace(temp,p)
(backup/'result.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(receipt,ensure_ascii=False))
