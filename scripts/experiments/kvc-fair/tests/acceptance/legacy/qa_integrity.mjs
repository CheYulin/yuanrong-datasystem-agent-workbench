// Pure report acceptance checks; fixtures are never benchmark observations.
export const products=['datasystem','mooncake','lmcache'];
export const sizes=[1024,65536,1048576];
export const concurrencies=[2,8,16];
export const identity=r=>[r.operation,r.product,r.repeat,r.size,r.concurrency].join(':');
export const cellIdentity=r=>`r${r.repeat}-${r.product}-s${r.size}-c${r.concurrency}`;
const equal=(a,b)=>a===b||Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=Math.max(1,Math.abs(b))*1e-12;
const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
export const sameJSON=(a,b)=>JSON.stringify(canonical(a))===JSON.stringify(canonical(b));
export function expectedMatrix(){
  const cells=[],rows=[];
  for(const repeat of [0,1,2])for(const product of products)for(const size of sizes)for(const concurrency of concurrencies){
    const cell={repeat,product,size,concurrency};cells.push(cellIdentity(cell));
    for(const operation of size===1024?['put','get','exist_hit','exist_miss']:['put','get'])rows.push(identity({...cell,operation}));
  }
  return {cells,rows};
}
export function auditMatrix(data,{complete=false,expectedRows=null}={}){
  const errors=[],expected=expectedMatrix(),rows=data.rows||[],plan=data.plan||[],summary=data.summary||{};
  const ids=rows.map(identity),cellIds=plan.map(cellIdentity),completed=summary.completed||[];
  const compare=(actual,wanted,label,exact)=>{const set=new Set(actual),allowed=new Set(wanted);if(set.size!==actual.length)errors.push(label+' duplicates');const extra=actual.filter(x=>!allowed.has(x)),missing=exact?wanted.filter(x=>!set.has(x)):[];if(extra.length||missing.length)errors.push({label,unexpected:extra.slice(0,12),missing:missing.slice(0,12)});};
  compare(ids,expected.rows,'measurement identities',complete);
  compare(cellIds,expected.cells,'planned cell identities',complete);
  compare(completed,cellIds,'completed cell identities',complete);
  if(expectedRows!=null&&rows.length!==expectedRows)errors.push('expected row count');
  if(complete&&(!summary.ended||summary.fatal||(summary.failed||[]).length))errors.push('suite not successfully complete');
  if(complete&&rows.some(r=>r.valid!==true||r.error!=null||r.failures!==0||!Number.isFinite(r.success)||r.success<=0))errors.push('invalid or failed measurement');
  return {pass:errors.length===0,complete,rows:rows.length,valid:rows.filter(r=>r.valid===true).length,plannedCells:plan.length,completedCells:completed.length,errors};
}
export function parseCSV(text){
  const rows=[];let row=[],value='',quoted=false;
  for(let i=0;i<text.length;i++){
    const c=text[i];
    if(c==='"'){if(quoted&&text[i+1]==='"'){value+='"';i++;}else if(quoted||value==='')quoted=!quoted;else throw Error('Unexpected CSV quote');}
    else if(c===','&&!quoted){row.push(value);value='';}
    else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(value);rows.push(row);row=[];value='';}
    else value+=c;
  }
  if(quoted)throw Error('Unclosed CSV quote');
  if(value!==''||row.length){row.push(value);rows.push(row);}
  return rows;
}
export function auditCSV(text,expected){
  const errors=[];let records;
  try{records=parseCSV(text);}catch(error){return {pass:false,errors:[String(error)]};}
  const [header=[],...body]=records,fields=[...new Set(expected.flatMap(r=>Object.keys(r)))].sort();
  if(header.length!==new Set(header).size||!sameJSON([...header].sort(),fields))errors.push('CSV headers differ from payload fields');
  if(body.length!==expected.length)errors.push('CSV row count');
  const actual=body.map(values=>Object.fromEntries(header.map((key,i)=>[key,values[i]]))),map=new Map(expected.map(r=>[identity(r),r])),seen=new Set();
  actual.forEach((record,index)=>{
    if(body[index].length!==header.length){errors.push('CSV column count '+index);return;}
    const id=identity(record),row=map.get(id);if(!row||seen.has(id)){errors.push('CSV missing/duplicate/unexpected identity '+id);return;}seen.add(id);
    for(const field of fields){const value=row[field],text=record[field],match=value==null?text==='':typeof value==='boolean'?text===(value?'True':'False'):typeof value==='number'?text!==''&&equal(Number(text),value):text===String(value);if(!match&&errors.length<20)errors.push({identity:id,field,expected:value,actual:text});}
  });
  if(seen.size!==expected.length)errors.push('CSV identity coverage');
  return {pass:errors.length===0,rows:body.length,fields:header.length,errors};
}
export function auditRaw(row,raw,{profile=false}={}){
  const errors=[],p=raw.parameters||{},cell=cellIdentity(row),expectedEvidence=`results/${profile?'put-resource-profile/':''}${cell}/${row.operation}.json`;
  if(row.evidence!==expectedEvidence)errors.push('evidence path identity');
  const parameters={op:row.operation,size:row.size,concurrency:row.concurrency,run_id:`r${row.repeat}-s${row.size}-c${row.concurrency}`};
  for(const [key,value] of Object.entries(parameters))if(p[key]!==value)errors.push('raw parameter '+key);
  if(typeof p.out!=='string'||!p.out.endsWith(`/cells/${cell}/${row.operation}.json`))errors.push('raw output product/cell identity');
  const values={valid:raw.valid,error:raw.fatal??null,qps:raw.qps??null,throughput_mib_s:raw.throughput_mib_s??null,duration_s:raw.duration_s??null,success:raw.success??null,failures:raw.failures??null,mean_us:raw.mean_us??null,max_us:raw.max_us??null,short_window:raw.short_window??null,p50_us:raw.p50?.upper_us??null,p99_us:raw.p99?.upper_us??null,p99_lower_us:raw.p99?.lower_us??null,start_mono_ns:raw.measure_start_mono_ns??null,end_mono_ns:raw.measure_end_mono_ns??null};
  for(const [key,value] of Object.entries(values))if(row[key]!==value)errors.push('raw field '+key);
  if(raw.valid===true&&Object.values(raw.histogram||{}).reduce((sum,v)=>sum+v,0)!==row.success)errors.push('raw histogram count');
  return {pass:errors.length===0,identity:identity(row),evidence:row.evidence,errors};
}
export function auditPerformance(rows,{operation,size,metric,bars,scatter,unit}){
  const errors=[],labels={datasystem:'DataSystem',mooncake:'Mooncake',lmcache:'LMCache'},selected=rows.filter(r=>r.operation===operation&&r.size===size&&r.valid===true),expectedBars=[];
  for(const product of products)for(const concurrency of concurrencies){const own=selected.filter(r=>r.product===product&&r.concurrency===concurrency);if(own.length===3&&new Set(own.map(r=>r.repeat)).size===3&&own.every(r=>Number.isFinite(r[metric])))expectedBars.push({key:labels[product]+':'+concurrency,value:own.map(r=>r[metric]).sort((a,b)=>a-b)[1]});}
  if(unit!==(metric==='qps'?'QPS':'MiB/s'))errors.push('throughput axis unit');
  if(bars.length!==expectedBars.length||new Set(bars.map(b=>b.key)).size!==bars.length||expectedBars.some(e=>!bars.some(a=>a.key===e.key&&equal(a.value,e.value))))errors.push('median bars');
  const wanted=new Map(selected.map(r=>[identity(r),r]));
  if(scatter.length!==selected.length||new Set(scatter.map(s=>s.identity)).size!==scatter.length)errors.push('raw scatter coverage');
  for(const point of scatter){const row=wanted.get(point.identity);if(!row||point.name!==labels[row.product]||point.x!==concurrencies.indexOf(row.concurrency)||!equal(point.y,row[metric])){errors.push('raw scatter identity/value');break;}}
  return {pass:errors.length===0,operation,size,metric,rows:selected.length,expectedBars,actualBars:bars,errors};
}
export function classifyNetwork(issue,base){
  let url;try{url=new URL(issue.url,base);}catch{return {...issue,classification:'required',reason:'Malformed request URL'};}
  const origin=new URL(base).origin,optional=new Map([['/site-api/auth/status','Shared-site login status; benchmark report is public'],['/site-api/comments','Shared-site comments; benchmark controls and downloads are independent'],['/site-api/admin/pages','Shared-site admin page metadata; public report does not require administration']]);
  if(url.origin===origin&&optional.has(url.pathname))return {...issue,classification:'optional-site-feature',reason:optional.get(url.pathname),localFixture:['127.0.0.1','localhost','[::1]'].includes(url.hostname)};
  if(url.origin===origin&&url.pathname==='/favicon.ico')return {...issue,classification:'optional-icon',reason:'Browser tab icon'};
  return {...issue,classification:'required',reason:url.pathname.startsWith('/site-api/')?'Unknown site API endpoint is not exempt':'Report asset, data, download, or unclassified network request'};
}
