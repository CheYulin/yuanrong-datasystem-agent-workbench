const API = '/site-api/kvc-lab';
const $ = (id) => document.getElementById(id);
const statuses = { pending:'待执行', queued:'排队中', running:'运行中', passed:'验收通过', valid:'有效', success:'成功', succeeded:'成功', completed:'已完成', completed_with_failures:'完成（存在失败）', interrupted:'中断待核验', complete:'已完成', ready:'已就绪', failed:'失败', error:'错误', rejected:'拒绝', cancelled:'已停止', canceled:'已停止', not_supported:'不支持', na:'不适用', passed_with_findings:'通过并记录问题', missing:'未采集', invalid:'无效', stopped:'已停止' };
const successStatuses = new Set(['valid','passed','success','succeeded','complete','completed']);
const activeStatuses = new Set(['running','queued','pending','starting','stopping']);
const products = { datasystem:'DataSystem', mooncake:'Mooncake' };
const operations = { put:'PUT', get:'GET 命中', exist_hit:'EXIST 命中', exist_miss:'EXIST 未命中' };
const numericFields = ['nodes','workers_per_node','clients','server_cpus','client_cpus','master_cpus','memory_gib','duration_s','repeats'];
const groupFields = ['products','batches','value_sizes','operations'];
const colors = ['#3975b5','#2c9784','#9b78b8','#d19640','#609bae','#c16e72'];
const app = { authenticated:false, catalog:null, csrf:null, jobs:new Map(), selectedJob:null, selectedComparisons:new Set(), charts:new Map(), loading:false, pendingKeys:new Map(), pollBusy:false, versions:null, plan:null, planTimer:null, expandedRecords:new Set(), deploymentActive:false };

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}
function statusBadge(value) { const status=String(value || 'pending'); return element('span', statuses[status] || status, `badge ${/^[a-z_]+$/.test(status) ? status : 'pending'}`); }
function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
function number(value, digits=1) { return finite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:digits}) : '—'; }
function duration(value) { return finite(value) ? `${number(value,2)} s` : '—'; }
function sizeLabel(value) { const n=Number(value); return n>=1048576 ? `${number(n/1048576)} MiB` : n>=1024 ? `${number(n/1024)} KiB` : `${n} B`; }
function timestamp(value) { if (!value) return ''; const d=new Date(value); return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}); }
function safeLink(value) {
  if (typeof value !== 'string' || !value || value.startsWith('//')) return null;
  try { const url=new URL(value,location.href); return url.origin===location.origin && ['http:','https:'].includes(url.protocol) ? url.href : null; } catch { return null; }
}
function addLink(container, text, href) { const url=safeLink(href); if (!url) return; const a=element('a',text); a.href=url; a.target='_blank'; a.rel='noopener'; container.append(a); }
function setConnection(text) { const node=$('connection-text'); if (node) node.textContent=text; }
function readUser(value) { return typeof value==='string' ? value : value?.username || value?.name || ''; }
function accessDenied(message, loginUrl) {
  app.authenticated=false; app.csrf=null; app.catalog=null; app.jobs.clear(); app.selectedJob=null; app.selectedComparisons.clear(); app.expandedRecords.clear(); app.pendingKeys.clear(); app.plan=null; app.deploymentActive=false; clearTimeout(app.planTimer);
  app.charts.forEach((chart)=>chart.dispose()); app.charts.clear();
  $('protected-app').hidden=true; $('access-panel').hidden=false;
  $('account-status').textContent='需要管理员登录'; $('account-status').classList.remove('authenticated'); $('account-status').removeAttribute('title');
  $('access-title').textContent='请使用站点管理员账户登录'; $('access-message').textContent=message || '登录 yche.me 站点账户并通过管理员授权后，可读取部署状态、任务日志与性能结果。';
  $('login-link').href=safeLink(loginUrl) || `/login?next=${encodeURIComponent(location.pathname)}`;
  $('login-link').hidden=false; $('retry-auth').hidden=false;
  ['presets','version-rows','job-overview','step-list','comparison-jobs','result-rows','catalog-links','run-records-list','plan-rows','plan-status','deployment-status','result-scope','result-quality','case-summary','estimate-summary','client-summary','client-budget','worker-summary','server-budget'].forEach((id)=>$(id)?.replaceChildren());
  if($('result-quality'))$('result-quality').hidden=true;
  $('result-counts')?.querySelectorAll('strong').forEach(node=>{node.textContent='—';});
  if ($('job-logs')) $('job-logs').textContent='登录后可查看日志。';
  if ($('job-select')) $('job-select').replaceChildren(element('option','登录后可读取任务'));
  if ($('config-preview')) $('config-preview').textContent='';
  if ($('experiment-form')) $('experiment-form').reset();
  closeNavigation();
}
async function api(path, options={}) {
  const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),20000);
  try {
    const response=await fetch(`${API}${path}`, { credentials:'same-origin', cache:'no-store', ...options, headers:{Accept:'application/json', ...(options.headers || {})}, signal:controller.signal });
    const contentType=response.headers.get('content-type') || '';
    const body=contentType.includes('application/json') ? await response.json() : {};
    if (response.status===401 || response.status===403) { accessDenied(body.message || body.error,body.login_url); throw Object.assign(new Error('登录权限已失效，请重新登录。'),{accessDenied:true}); }
    if (!response.ok) throw new Error(body.message || body.error || `请求失败（HTTP ${response.status}）`);
    if (!contentType.includes('application/json')) throw new Error('服务未返回 JSON，请检查登录状态或 API 部署。');
    if(path!=='/catalog' && !app.authenticated)throw Object.assign(new Error('登录状态已失效，丢弃旧请求结果。'),{accessDenied:true});
    return body;
  } catch (error) { if (error.name==='AbortError') throw new Error('状态读取超时；后台任务可能仍在运行，请刷新原任务。'); throw error; }
  finally { clearTimeout(timeout); }
}
function readConfig() {
  const form=$('experiment-form'); const config={profile:form.elements.namedItem('profile').value};
  for (const name of numericFields) config[name]=Number(form.elements.namedItem(name).value);
  for (const name of groupFields) config[name]=Array.from(form.querySelectorAll(`input[name="${name}"]:checked`),input=>['batches','value_sizes'].includes(name) ? Number(input.value) : input.value);
  return config;
}
function applyConfig(config={}) {
  const form=$('experiment-form'); if (!form) return;
  if (config.profile && Array.from(form.elements.namedItem('profile').options).some(option=>option.value===config.profile)) form.elements.namedItem('profile').value=config.profile;
  for (const name of numericFields) { const select=form.elements.namedItem(name); if (config[name]!==undefined && Array.from(select.options).some(option=>Number(option.value)===Number(config[name]))) select.value=String(config[name]); }
  for (const name of groupFields) if (Array.isArray(config[name])) form.querySelectorAll(`input[name="${name}"]`).forEach((input)=>{input.checked=config[name].map(String).includes(input.value);});
  updateConfigSummary();
}
function configError(config) { for (const name of groupFields) if (!config[name].length) return `请至少选择一个${{products:'产品',batches:'批量大小',value_sizes:'数据大小',operations:'接口'}[name]}。`; if(config.server_cpus+config.client_cpus>38)return '服务端与客户端合计最多 38 个物理核；另留 2 核监控。'; if(config.master_cpus>config.server_cpus)return '中心组件预算不能超过服务端总预算。'; if(config.batches.some(batch=>config.value_sizes.some(size=>batch*size>16*1024*1024)))return '单次调用载荷最多 16 MiB。请将大值与大批量拆为不同实验。'; return null; }
function updateConfigSummary() {
  const metadata=$('experiment-form').elements.namedItem('profile').value==='metadata_scaling';
  $('experiment-form').querySelectorAll('input[name="operations"]').forEach(input=>{const unsupported=metadata && ['put','get'].includes(input.value);input.disabled=unsupported;if(unsupported)input.checked=false;});
  const config=readConfig();
  $('client-summary').textContent=`${config.clients} clients`;
  $('client-budget').textContent=`${config.client_cpus} 物理核 · 独立 cgroup`;
  $('worker-summary').textContent=`${config.nodes} 逻辑节点 × ${config.workers_per_node} worker`;
  $('server-budget').textContent=`${config.server_cpus} 物理核 · ${config.memory_gib} GiB · Coordinator / Master 计入预算`;
  const variants=config.profile==='worker_scaling' ? [1,2,4,8].filter(workers=>workers%config.nodes===0).length : config.profile==='client_scaling' ? [1,2,4,8,16,32].filter(clients=>clients<=config.clients).length : 1;
  const descriptions={baseline:'基础性能使用所选单一拓扑。服务端与客户端按物理核分配，监控另留 2 核；合计最多 38 核。',worker_scaling:`固定 ${config.server_cpus} 个服务端物理核与 ${config.nodes} 个逻辑节点，依次比较 workers 总数 ${[1,2,4,8].filter(workers=>workers%config.nodes===0).join(' / ')}；每节点 workers 由总数派生。`,client_scaling:`固定服务端拓扑与预算，依次比较 clients ${[1,2,4,8,16,32].filter(clients=>clients<=config.clients).join(' / ')}；上方 clients 为本次扫描上限。`,metadata_scaling:'仅比较 EXIST 命中 / 未命中；在 worker 扩展中扫描中心组件 1 / 2 / 4 核（不超过服务端总预算），测量 CPU、节流、吞吐与 P99。B 由下方选择，不预设瓶颈结论。'};
  $('profile-description').textContent=descriptions[config.profile];
  const count=variants*config.products.length*config.batches.length*config.value_sizes.length*config.operations.length*config.repeats;
  $('case-summary').textContent=`${config.nodes*config.workers_per_node} workers / 产品 · ${count} 个计时 case`;
  $('estimate-summary').textContent=`纯测量窗口 ${number(count*config.duration_s/60,1)} 分钟，另计部署 / 预热 / 验收`;
  $('config-preview').textContent=JSON.stringify(config,null,2);
  schedulePlan();
}
function schedulePlan() {
  clearTimeout(app.planTimer); app.plan=null; updateActionButtons();
  const config=readConfig(),error=configError(config); $('plan-status').classList.toggle('error',Boolean(error)); $('plan-rows').replaceChildren();
  if(error){$('plan-status').textContent=error;return;}
  $('plan-status').textContent='正在生成服务端执行计划；此操作不会启动部署。';
  app.planTimer=setTimeout(()=>previewPlan(config),350);
}
async function previewPlan(config) {
  const key=JSON.stringify(config);
  try {
    const data=await api('/plans',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':app.csrf},body:JSON.stringify({config})});
    if(!app.authenticated || JSON.stringify(readConfig())!==key)return;
    const plan=data.plan || data; app.plan={...plan,key};
    $('plan-status').classList.remove('error'); $('plan-status').textContent=`计划已校验：${plan.variants?.length ?? '—'} 个规格，${plan.expected_cases ?? '—'} 个测量 case；纯测量窗口 ${finite(plan.estimated_measurement_s) ? number(plan.estimated_measurement_s/60,1)+' 分钟' : '待估算'}。部署、预热和验收另计。`;
    if(finite(plan.expected_cases))$('case-summary').textContent=`服务端执行计划 · ${plan.expected_cases} 个测量 case`;
    if(finite(plan.estimated_measurement_s))$('estimate-summary').textContent=`纯测量窗口 ${number(plan.estimated_measurement_s/60,1)} 分钟，另计准备与验收`;
    for(const [index,variant] of (plan.variants || []).entries()){const config=variant.config || variant;const tr=element('tr');for(const value of [variant.id || variant.name || index+1,config.nodes,config.workers ?? config.worker_count ?? (finite(config.nodes)&&finite(config.workers_per_node) ? config.nodes*config.workers_per_node : '—'),config.clients,config.master_cpus])tr.append(element('td',value ?? '—'));$('plan-rows').append(tr);}
    updateActionButtons();
  }catch(error){if(!app.authenticated || JSON.stringify(readConfig())!==key)return;$('plan-status').classList.add('error');$('plan-status').textContent=`计划未通过：${error.message}`;updateActionButtons();}
}
function renderCatalog(catalog) {
  const container=$('presets');
  if (container) {
    container.replaceChildren();
    for (const preset of catalog.presets || []) {
      const button=element('button',null,'preset-card'); button.type='button'; button.dataset.preset=String(preset.id);
      button.append(element('strong',preset.title || preset.id),element('span',preset.description || ''));
      button.addEventListener('click',()=>{ applyConfig(preset.config || {}); container.querySelectorAll('button').forEach(node=>node.classList.toggle('selected',node===button)); $('action-message').textContent=`已载入“${preset.title || preset.id}”，可继续调整参数。`; });
      container.append(button);
    }
    if (!container.children.length) container.append(element('p','服务端尚未提供预设；可在下方直接配置。','empty'));
  }
  const versionRows=$('version-rows');
  if (versionRows) {
    versionRows.replaceChildren();
    const versions=Array.isArray(catalog.versions) ? catalog.versions : Object.entries(catalog.versions || {}).map(([product,value])=>({product,...(typeof value==='string' ? {tag:value} : value)}));
    for (const version of versions) { const tr=element('tr'); tr.append(element('td',products[version.product] || version.product || version.name)); const id=element('td'); id.append(element('strong',version.tag || 'Tag 未提供'),element('br'),element('code',version.commit || version.sha || '提交待记录')); tr.append(id,element('td',version.settings || version.config_summary || '有效配置以部署回执为准')); versionRows.append(tr); }
    if (!versions.length) { const tr=element('tr'); const td=element('td','版本信息待服务端提供；运行前由部署步骤验证。'); td.colSpan=3; tr.append(td); versionRows.append(tr); }
  }
  const links=$('catalog-links');
  if (links) { links.replaceChildren(); for (const entry of catalog.links || []) addLink(links,entry.title || entry.label || entry.url,entry.url || entry.href); }
  if ($('experiment-form')) applyConfig(catalog.defaults || {});
}
function mergeJob(job) { if (!job || !job.id) return; app.jobs.set(String(job.id),{...(app.jobs.get(String(job.id)) || {}),...job,id:String(job.id)}); }
function sortedJobs() { return Array.from(app.jobs.values()).sort((a,b)=>String(b.created_at || b.started_at || b.id).localeCompare(String(a.created_at || a.started_at || a.id))); }
function renderDeployment(deployment) {
  if(deployment?.kind==='deployment')deployment=deployment.deployment;
  app.deploymentActive=deployment?.active===true || (deployment?.active===undefined && ['ready','running'].includes(typeof deployment==='string' ? deployment : deployment?.status));
  const box=$('deployment-status'); if (!box) return; box.replaceChildren();
  if (!deployment) { box.append(statusBadge('pending'),element('span','当前无已确认的部署。')); return; }
  if (typeof deployment==='string') { box.append(statusBadge(deployment)); return; }
  const config=deployment.config || deployment;
  const status=deployment.status || (deployment.active===true ? 'ready' : deployment.active===false ? 'stopped' : 'pending');
  box.append(statusBadge(status),element('span',deployment.message || (deployment.active===false ? '当前部署未处于活跃状态，请核对回收回执。' : `${products[deployment.product] || deployment.product || '当前产品'} · ${config.nodes ?? '—'} 逻辑节点 × ${config.workers_per_node ?? '—'} workers / 节点 · ${config.clients ?? '—'} clients`)));
  if (deployment.job_id) addLink(box,'部署回执',`${API}/jobs/${encodeURIComponent(deployment.job_id)}`);
}
function renderJobChoices() {
  const selector=$('job-select'); if (!selector) return;
  const jobs=sortedJobs(); selector.replaceChildren();
  for (const job of jobs) { const option=element('option',`${job.id} · ${statuses[job.status] || job.status || '待执行'}`); option.value=job.id; selector.append(option); }
  if (!jobs.length) { const option=element('option','暂无任务'); option.value=''; selector.append(option); }
  if (!app.selectedJob || !app.jobs.has(app.selectedJob)) app.selectedJob=jobs[0]?.id || null;
  if (app.selectedJob) selector.value=app.selectedJob;
  if (!app.selectedComparisons.size && app.selectedJob) app.selectedComparisons.add(app.selectedJob);
  const comparisons=$('comparison-jobs'); comparisons.replaceChildren();
  for (const job of jobs.filter(job=>job.action==='run' || job.results?.length || job.id===app.selectedJob)) {
    const label=element('label'); const checkbox=element('input'); checkbox.type='checkbox'; checkbox.value=job.id; checkbox.checked=app.selectedComparisons.has(job.id);
    checkbox.addEventListener('change',async()=>{ if (checkbox.checked) {app.selectedComparisons.add(job.id); try { await loadJob(job.id); } catch(error) {setConnection(error.message);} } else app.selectedComparisons.delete(job.id); renderResults(); });
    label.append(checkbox,document.createTextNode(`${job.id} · ${statuses[job.status] || job.status || '待执行'}`)); comparisons.append(label);
  }
  updateActionButtons();
}
function renderStepRows(container, steps=[]) {
  container.replaceChildren();
  for (const [index,step] of steps.entries()) {
    const row=element('div',null,'step-row'); const body=element('div',null,'step-body'); body.append(element('strong',step.title || step.name || `步骤 ${index+1}`));
    const message=step.message || step.summary || step.acceptance?.summary; if (typeof message==='string') body.append(element('p',message));
    const links=element('div',null,'step-links');
    for (const key of ['receipt_url','acceptance_url','artifact_url','log_url','inputs_url']) if (step[key]) addLink(links,{receipt_url:'回执',acceptance_url:'验收',artifact_url:'中间产物',log_url:'日志',inputs_url:'输入'}[key],step[key]);
    for (const artifact of Array.isArray(step.artifacts) ? step.artifacts : []) addLink(links,artifact.title || artifact.name || '产物',artifact.url || artifact.href);
    if (links.children.length) body.append(links);
    row.append(element('span',String(index+1).padStart(2,'0'),'step-number'),body,statusBadge(step.status),element('span',duration(step.duration_s),'step-duration')); container.append(row);
  }
  if (!steps.length) container.append(element('p','任务创建后由服务端依次产生步骤与验收回执。','empty'));
}
function formatLogs(logs=[]) {
  const lines=(Array.isArray(logs) ? logs : [logs]).map(log=>typeof log==='string' ? log : `${log.timestamp || log.time || ''} ${log.level || ''} ${log.message || log.line || JSON.stringify(log)}`.trim());
  const clipped=lines.slice(-500).join('\n');
  return `${lines.length>500 ? `仅展示最后 500 条，共 ${lines.length} 条；完整日志见原始回执。\n` : ''}${clipped || '尚无日志。'}`;
}
function renderJob(job) {
  if (!job || !$('job-overview')) return;
  const box=$('job-overview'); box.replaceChildren(element('strong',job.id),statusBadge(job.status),element('small',`操作：${{deploy:'部署',run:'运行',stop:'停止'}[job.action] || job.action || '—'}`),element('small',`耗时 ${duration(job.duration_s)}`));
  if (job.created_at || job.started_at) box.append(element('small',timestamp(job.created_at || job.started_at)));
  addLink(box,'原始任务 JSON',`${API}/jobs/${encodeURIComponent(job.id)}`);
  appendArtifactLinks(box,job);
  renderStepRows($('step-list'),job.steps || []);
  $('job-logs').textContent=formatLogs(job.logs || []); $('log-count').textContent=Array.isArray(job.logs) ? `(${job.logs.length})` : '';
  renderStepChart(job.steps || []); updateActionButtons();
}
function updateActionButtons() {
  const busy=Array.from(app.jobs.values()).some(job=>activeStatuses.has(job.status));
  const planReady=!$('experiment-form') || app.plan?.key===JSON.stringify(readConfig());
  if ($('deploy-button')) $('deploy-button').disabled=app.loading || busy || !planReady;
  if ($('run-button')) $('run-button').disabled=app.loading || busy || !planReady;
  if ($('stop-button')) $('stop-button').disabled=app.loading || !(busy || app.deploymentActive);
}
async function loadJob(id) { const data=await api(`/jobs/${encodeURIComponent(id)}`); const job=data.job || data; mergeJob(job); if (app.selectedJob===String(id)) renderJob(app.jobs.get(String(id))); return job; }
async function refreshState() {
  const state=await api('/state');
  (state.jobs || []).forEach(mergeJob);
  if ($('deployment-status')) renderDeployment(state.deployment);
  renderJobChoices();
  const ids=new Set([app.selectedJob,...app.selectedComparisons].filter(Boolean));
  for (const id of ids) await loadJob(id);
  if ($('result-rows')) renderResults();
  if ($('run-records-list')) renderRunRecords();
  setConnection(`已连接 tiantiyun-80c128g · ${new Date().toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})} 更新`);
}
function chart(id) {
  if (!window.echarts || !$(id)) return null;
  if (!app.charts.has(id)) app.charts.set(id,window.echarts.init($(id),null,{renderer:'canvas'}));
  return app.charts.get(id);
}
function emptyChart(id, text) { const instance=chart(id); if (instance) instance.setOption({animation:false,title:{text,left:'center',top:'center',textStyle:{fontSize:12,fontWeight:'normal',color:'#7890a4'}},xAxis:{show:false},yAxis:{show:false},series:[]},true); else if ($(id)) $(id).replaceChildren(element('p',window.echarts ? text : '图表组件尚未加载；完整数值可查看下方结果表。','empty')); }
function baseChart() { return {animation:false,color:colors,textStyle:{fontFamily:'system-ui, sans-serif',fontSize:11,color:'#60758a'},tooltip:{trigger:'axis',confine:true,appendToBody:false,extraCssText:'max-width:260px;white-space:normal;overflow-wrap:anywhere'},legend:{bottom:0,type:'scroll',textStyle:{fontSize:10,color:'#61758a'}},grid:{left:58,right:24,top:35,bottom:65},aria:{enabled:true}}; }
function renderStepChart(steps) {
  const rows=steps.filter(step=>finite(step.duration_s));
  if (!rows.length) {emptyChart('step-chart','完成步骤后显示实际耗时');return;}
  const instance=chart('step-chart'); if (!instance) {emptyChart('step-chart','');return;}
  instance.setOption({...baseChart(),legend:{show:false},grid:{left:90,right:35,top:15,bottom:35},xAxis:{type:'value',name:'秒',splitLine:{lineStyle:{color:'#edf1f6'}}},yAxis:{type:'category',inverse:true,data:rows.map(step=>step.title || step.name),axisLabel:{width:75,overflow:'truncate'},axisTick:{show:false},axisLine:{show:false}},series:[{type:'bar',barMaxWidth:18,data:rows.map(step=>({value:step.duration_s,itemStyle:{color:['failed','error','rejected'].includes(step.status) ? '#bb7061' : '#598bb9',borderRadius:[0,3,3,0]}}))}]},true);
}
function allResults() { return Array.from(app.selectedComparisons).flatMap(id=>(app.jobs.get(id)?.results || []).map(row=>({...row,job_id:id,config:app.jobs.get(id)?.config || {}}))); }
function normalizeRow(row) {
  return {...row,nodes:row.nodes ?? row.config.nodes,workers:row.workers ?? (finite(row.nodes ?? row.config.nodes) && finite(row.workers_per_node ?? row.config.workers_per_node) ? (row.nodes ?? row.config.nodes)*(row.workers_per_node ?? row.config.workers_per_node) : null),clients:row.clients ?? row.config.clients,server_cpus:row.server_cpus ?? row.config.server_cpus,client_cpus:row.client_cpus ?? row.config.client_cpus,master_cpus:row.master_cpus ?? row.config.master_cpus,memory_gib:row.memory_gib ?? row.config.memory_gib,batch:row.batch ?? row.batch_size,size:row.size ?? row.value_size,repeat:row.repeat ?? row.repetition,keys_s:row.keys_s ?? null,batch_qps:row.batch_qps ?? null,p99_us:row.p99_us ?? null};
}
function median(values) { const sorted=values.slice().sort((a,b)=>a-b); const middle=Math.floor(sorted.length/2); return sorted.length%2 ? sorted[middle] : (sorted[middle-1]+sorted[middle])/2; }
function groupLabel(row,axis) {
  const parts=[products[row.product] || row.product,`中心${row.master_cpus ?? '?'}`,`CPU${row.server_cpus ?? '?'}/${row.client_cpus ?? '?'}`,`${row.memory_gib ?? '?'}GiB`];
  for(const [key,label] of [['nodes','N'],['workers','W'],['clients','C']])if(key!==axis)parts.push(`${label}${row[key] ?? '?'}`);
  parts.push(`B${row.batch}`,sizeLabel(row.size));
  if(row.build_sha || row.commit)parts.push(String(row.build_sha || row.commit).slice(0,10));
  return parts.join(' · ');
}
function renderResults() {
  const all=allResults().map(normalizeRow);
  const counts=[all.length,all.filter(row=>successStatuses.has(row.status)).length,all.filter(row=>['failed','error','rejected','invalid'].includes(row.status)).length,all.filter(row=>!successStatuses.has(row.status) && !['failed','error','rejected','invalid'].includes(row.status)).length];
  $('result-counts').querySelectorAll('strong').forEach((node,index)=>{node.textContent=number(counts[index],0);});
  const operation=$('filter-operation').value,batch=$('filter-batch').value,size=$('filter-size').value,axis=$('filter-axis').value,metric=$('filter-metric').value;
  const filtered=all.filter(row=>row.operation===operation && (batch==='all' || Number(row.batch)===Number(batch)) && (size==='all' || Number(row.size)===Number(size)));
  const valid=filtered.filter(row=>successStatuses.has(row.status));
  $('result-scope').textContent=`已选择 ${app.selectedComparisons.size} 个任务。当前性能筛选：${filtered.length} 条记录，其中 ${valid.length} 条有效、${filtered.length-valid.length} 条状态未进入成功性能图。`;
  const shortCount=all.filter(row=>row.short_window===true).length,lowSampleCount=all.filter(row=>row.low_sample_p99===true).length;
  $('result-quality').hidden=shortCount===0&&lowSampleCount===0;
  $('result-quality').textContent=`所选任务包含 ${shortCount} 条短测量窗口、${lowSampleCount} 条 P99 样本不足记录。标记始终保留在完整表的证据列；短窗口吞吐和低样本 P99 请结合实际时长、样本量判断。`;
  const groups=new Map(); const xValues=new Set();
  for (const row of valid) if (finite(row[axis]) && finite(row[metric])) { const key=groupLabel(row,axis); if (!groups.has(key)) groups.set(key,new Map()); const values=groups.get(key); if (!values.has(row[axis])) values.set(row[axis],[]); values.get(row[axis]).push(row[metric]); xValues.add(row[axis]); }
  const xs=Array.from(xValues).sort((a,b)=>a-b); const instance=chart('performance-chart');
  if (!xs.length) emptyChart('performance-chart','该接口 / 负载尚无有效性能数据；请查看完整状态表');
  else if (instance) instance.setOption({...baseChart(),xAxis:{type:'category',name:{clients:'Clients',workers:'Workers',nodes:'逻辑节点'}[axis],nameLocation:'middle',nameGap:27,data:xs,axisLine:{lineStyle:{color:'#cbd9e5'}},axisTick:{show:false}},yAxis:{type:'value',name:{keys_s:'keys / 秒',batch_qps:'API 调用 / 秒',p99_us:'P99 / μs'}[metric],splitLine:{lineStyle:{color:'#edf1f6'}}},series:Array.from(groups,([name,values])=>({name,type:xs.length>1 ? 'line' : 'bar',connectNulls:false,barMaxWidth:65,symbolSize:7,data:xs.map(x=>values.has(x) ? median(values.get(x)) : null)}))},true);
  $('performance-caption').textContent=`图 3. ${operations[operation]} 的${{keys_s:'吞吐',batch_qps:'API 调用速率',p99_us:'P99 尾延迟'}[metric]}随${{clients:' clients',workers:' workers 总数',nodes:'逻辑节点'}[axis]}变化；仅对图中相同分组的有效轮次取中位数，缺失位置保留空值。`;
  const scatterGroups=new Map();
  for (const row of valid) if (finite(row.keys_s) && finite(row.p99_us)) { const key=groupLabel(row,axis); if (!scatterGroups.has(key)) scatterGroups.set(key,[]); scatterGroups.get(key).push([row.keys_s,row.p99_us,row.job_id,row[axis],row.repeat ?? '—']); }
  const scatter=chart('latency-chart');
  if (!scatterGroups.size) emptyChart('latency-chart','尚无同时包含吞吐与 P99 的有效记录');
  else if (scatter) scatter.setOption({...baseChart(),tooltip:{trigger:'item',confine:true,extraCssText:'max-width:260px;white-space:normal;overflow-wrap:anywhere',formatter:(params)=>`${escapeHtml(params.seriesName)}<br>keys/s: ${number(params.value[0])}<br>P99: ${number(params.value[1])} μs<br>${escapeHtml(params.value[2])} · 轮次 ${escapeHtml(params.value[4])}`},xAxis:{type:'value',name:'keys / 秒',nameLocation:'middle',nameGap:27,splitLine:{lineStyle:{color:'#edf1f6'}}},yAxis:{type:'value',name:'P99 / μs',splitLine:{lineStyle:{color:'#edf1f6'}}},series:Array.from(scatterGroups,([name,data])=>({name,type:'scatter',symbolSize:10,data,itemStyle:{opacity:.8}}))},true);
  renderCenterResources(valid,axis);
  renderResultTable(all);
}
function renderCenterResources(rows,axis) {
  const groups=new Map(),xValues=new Set();let hasValues=false;
  for(const row of rows){
    if(!finite(row[axis]))continue;
    xValues.add(row[axis]);const key=groupLabel(row,axis);
    if(!groups.has(key))groups.set(key,new Map());const group=groups.get(key);
    if(!group.has(row[axis]))group.set(row[axis],{cpu:[],throttled:[]});const values=group.get(row[axis]);
    if(finite(row.master_cpu_cores)&&finite(row.master_cpus)&&row.master_cpus>0){values.cpu.push(row.master_cpu_cores/row.master_cpus*100);hasValues=true;}
    if(finite(row.master_throttled_usec)){values.throttled.push(row.master_throttled_usec/1000);hasValues=true;}
  }
  if(!hasValues){emptyChart('center-resource-chart','当前筛选尚无有效中心 CPU / 节流观测；缺失值不补零');return;}
  const instance=chart('center-resource-chart');if(!instance){emptyChart('center-resource-chart','');return;}
  const xs=Array.from(xValues).sort((a,b)=>a-b),series=[];
  for(const [index,[name,values]]of Array.from(groups).entries()){
    const color=colors[index%colors.length];
    series.push({name:`CPU% · ${name}`,type:'line',yAxisIndex:0,connectNulls:false,symbolSize:7,itemStyle:{color},lineStyle:{color},data:xs.map(x=>values.get(x)?.cpu.length ? median(values.get(x).cpu) : null)});
    series.push({name:`节流ms · ${name}`,type:'bar',yAxisIndex:1,barMaxWidth:25,itemStyle:{color,opacity:.28},data:xs.map(x=>values.get(x)?.throttled.length ? median(values.get(x).throttled) : null)});
  }
  instance.setOption({...baseChart(),grid:{left:48,right:52,top:40,bottom:70},xAxis:{type:'category',name:{clients:'Clients',workers:'Workers',nodes:'逻辑节点'}[axis],nameLocation:'middle',nameGap:27,data:xs,axisLine:{lineStyle:{color:'#cbd9e5'}},axisTick:{show:false}},yAxis:[{type:'value',name:'CPU利用 / %',min:0,splitLine:{lineStyle:{color:'#edf1f6'}}},{type:'value',name:'节流 / ms',min:0,splitLine:{show:false}}],series},true);
}
function escapeHtml(value) { return String(value).replace(/[&<>"']/g,(char)=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char])); }
function renderResultTable(rows) {
  const tbody=$('result-rows'); tbody.replaceChildren();
  for (const row of rows) {
    const tr=element('tr');
    tr.append(element('td',`${row.job_id} / ${row.repeat ?? '—'}`),element('td',products[row.product] || row.product || '—'),element('td',`${row.nodes ?? '—'} / ${row.workers ?? '—'} / ${row.clients ?? '—'}`),element('td',`服务 ${row.server_cpus ?? '—'} / 客户端 ${row.client_cpus ?? '—'} / 中心 ${row.master_cpus ?? '—'} 核；${row.memory_gib ?? '—'} GiB`),element('td',operations[row.operation] || row.operation || '—'),element('td',`${row.batch ?? '—'} / ${row.size===undefined ? '—' : sizeLabel(row.size)}`));
    const status=element('td'); status.append(statusBadge(row.status || 'missing')); tr.append(status);
    const valid=successStatuses.has(row.status); tr.append(element('td',valid ? number(row.keys_s) : '—'),element('td',valid ? number(row.batch_qps) : '—'),element('td',valid ? number(row.p99_us) : '—'));
    tr.append(element('td',number(row.server_cpu_cores,3)),element('td',number(row.client_cpu_cores,3)),element('td',number(row.master_cpu_cores,3)),element('td',finite(row.master_throttled_usec) ? number(row.master_throttled_usec/1000,3) : '—'),element('td',number(row.peak_charged_memory_gib,3)));
    const evidence=element('td'); if (row.message || row.error || row.reason) evidence.append(element('span',typeof(row.message || row.error || row.reason)==='string' ? (row.message || row.error || row.reason) : JSON.stringify(row.message || row.error || row.reason)));
    const warnings=element('div',null,'evidence-warnings');
    if(row.short_window===true)warnings.append(element('span',`short_window · 短窗口${finite(row.duration_s) ? ' '+duration(row.duration_s) : ''}`,'badge measurement_warning'));
    if(row.low_sample_p99===true)warnings.append(element('span','low_sample_p99 · P99 样本不足','badge measurement_warning'));
    if(warnings.children.length)evidence.append(warnings);
    if(row.put_scope){const scope=element('p',String(row.put_scope).includes('bounded fresh insert') ? 'PUT 范围：有界 fresh 插入，非定时 upsert。' : `PUT 范围：${String(row.put_scope)}`,'put-scope');scope.title=String(row.put_scope);evidence.append(scope);}
    const links=element('div',null,'step-links'); addLink(links,'原始记录',row.raw_url || row.artifact_url || row.evidence_url); if (links.children.length) evidence.append(links); if (!evidence.textContent) evidence.textContent=valid ? '实际测量记录' : '原因尚未提供'; tr.append(evidence); tbody.append(tr);
  }
  if (!rows.length) {const tr=element('tr');const td=element('td','尚无结果。页面不会预置性能数据。','empty');td.colSpan=16;tr.append(td);tbody.append(tr);}
}
function uniqueKey() { if (crypto.randomUUID) return crypto.randomUUID(); const bytes=crypto.getRandomValues(new Uint8Array(16)); return Array.from(bytes,b=>b.toString(16).padStart(2,'0')).join(''); }
function appendArtifactLinks(container,job) {
  const entries=Array.isArray(job.artifacts) ? job.artifacts : Object.entries(job.artifacts || {}).map(([name,value])=>typeof value==='string' ? {name,url:value} : {name,...value});
  for(const entry of entries){const artifact=typeof entry==='string' ? {name:entry} : entry;if(!artifact.name && !artifact.url)continue;addLink(container,artifact.title || artifact.name || '下载产物',artifact.url || `${API}/jobs/${encodeURIComponent(job.id)}/artifacts/${encodeURIComponent(artifact.name)}`);}
}
async function submitAction(action) {
  if (app.loading || !app.authenticated) return;
  const config=action==='stop' ? {} : readConfig();
  const error=action==='stop' ? null : configError(config); if (error) { $('action-message').textContent=error; $('action-message').classList.add('error'); return; }
  const requestKey=`${action}:${JSON.stringify(config)}`; if (!app.pendingKeys.has(requestKey)) app.pendingKeys.set(requestKey,uniqueKey());
  app.loading=true; updateActionButtons(); $('action-message').classList.remove('error'); $('action-message').textContent='正在提交任务，请等待服务端回执。';
  try {
    const data=await api('/jobs',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':app.csrf},body:JSON.stringify({action,config,idempotency_key:app.pendingKeys.get(requestKey)})});
    const job=data.job || data; if (!job.id) throw new Error('服务端缺少任务 ID；请刷新状态核对。');
    app.pendingKeys.delete(requestKey); mergeJob(job); app.selectedJob=String(job.id); if (action==='run') app.selectedComparisons.add(String(job.id));
    $('action-message').textContent=`任务 ${job.id} 已创建。可在第 04 节查看进度与逐步验收。`;
    renderJobChoices(); await loadJob(job.id); renderResults(); $('progress').scrollIntoView({behavior:'smooth',block:'start'});
  } catch(error) { if (app.authenticated) { $('action-message').textContent=`${error.message} 如提交结果未知，请先刷新任务；相同配置重试会沿用本次幂等键。`; $('action-message').classList.add('error'); } }
  finally { app.loading=false; updateActionButtons(); }
}
function closeNavigation() { $('navigation')?.classList.remove('open'); if ($('nav-backdrop')) $('nav-backdrop').hidden=true; $('menu-toggle')?.setAttribute('aria-expanded','false'); }
function configureNavigation() {
  $('menu-toggle')?.addEventListener('click',()=>{ const open=!$('navigation').classList.contains('open'); $('navigation').classList.toggle('open',open); $('nav-backdrop').hidden=!open; $('menu-toggle').setAttribute('aria-expanded',String(open)); });
  $('nav-backdrop')?.addEventListener('click',closeNavigation);
  document.addEventListener('keydown',(event)=>{if(event.key==='Escape')closeNavigation();});
  document.querySelectorAll('.lab-navigation nav a').forEach(link=>link.addEventListener('click',()=>{if(matchMedia('(max-width:900px)').matches)closeNavigation();}));
  if ('IntersectionObserver' in window) { const observer=new IntersectionObserver((entries)=>{ for(const entry of entries) if(entry.isIntersecting)document.querySelectorAll('.lab-navigation nav a').forEach(link=>{const active=link.hash===`#${entry.target.id}`;link.classList.toggle('active',active);if(active)link.setAttribute('aria-current','location');else link.removeAttribute('aria-current');}); },{rootMargin:'-10% 0px -65% 0px'});document.querySelectorAll('main section[id]').forEach(section=>observer.observe(section)); }
}
function renderRunRecords() {
  const container=$('run-records-list'); if (!container) return; container.replaceChildren();
  for (const job of sortedJobs()) {
    const card=element('article',null,'record-card'); const heading=element('div',null,'record-heading'); heading.append(element('strong',job.id),statusBadge(job.status)); addLink(heading,'原始任务 JSON',`${API}/jobs/${encodeURIComponent(job.id)}`); card.append(heading,element('p',`${{deploy:'部署',run:'运行',stop:'停止'}[job.action] || job.action || '—'} · ${timestamp(job.created_at || job.started_at)} · 实测耗时 ${duration(job.duration_s)}`));
    const button=element('button','展开步骤、配置与日志','button'); const details=element('div'); details.hidden=true;
    const fill=(full)=>{details.replaceChildren();const links=element('div',null,'links');appendArtifactLinks(links,full);const steps=element('div',null,'step-list');renderStepRows(steps,full.steps || []);const config=element('details');config.append(element('summary','提交的配置'),element('pre',JSON.stringify(full.config || {},null,2)));const logs=element('details');logs.append(element('summary','运行日志'),element('pre',formatLogs(full.logs || []),'log-view'));details.append(links,steps,config,logs);details.hidden=false;button.textContent='收起记录';};
    if(app.expandedRecords.has(job.id))fill(job);
    button.addEventListener('click',async()=>{ if (!details.hidden) {app.expandedRecords.delete(job.id);details.hidden=true;button.textContent='展开步骤、配置与日志';return;} button.disabled=true; try { const full=await loadJob(job.id);app.expandedRecords.add(job.id);fill(full); } catch(error) {button.textContent=error.message;} finally {button.disabled=false;} }); card.append(button,details);container.append(card);
  }
  if (!container.children.length) container.append(element('p','尚无真实任务记录。请先返回实验台部署并运行。','empty'));
}
async function boot() {
  $('retry-auth').hidden=true; $('access-title').textContent='正在验证访问权限'; $('access-message').textContent='请使用 yche.me 站点账户登录；实验部署、日志与结果需要管理员权限。';
  try {
    const catalog=await api('/catalog');
    if (!readUser(catalog.user) || typeof catalog.csrf_token!=='string' || !catalog.csrf_token) { accessDenied('当前账户无权限或登录凭据不完整，请使用站点管理员账户重新登录。',catalog.login_url); return; }
    app.authenticated=true; app.catalog=catalog; app.csrf=catalog.csrf_token;
    $('access-panel').hidden=true; $('protected-app').hidden=false; $('account-status').textContent=`${readUser(catalog.user)} · 私有实验台`; $('account-status').title=$('account-status').textContent; $('account-status').classList.add('authenticated');
    renderCatalog(catalog); await refreshState();
    if(document.querySelector('.mermaid') && window.mermaid){window.mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral',sequence:{useMaxWidth:true}});try{await window.mermaid.run({querySelector:'.mermaid'});}catch(error){document.documentElement.dataset.mermaidError=error.message;}}
  } catch(error) { if (error.accessDenied) return; if (app.authenticated) setConnection(error.message); else { $('access-title').textContent='暂时无法连接实验服务'; $('access-message').textContent=error.message; $('retry-auth').hidden=false; } }
}
configureNavigation();
$('retry-auth')?.addEventListener('click',boot);
$('refresh-state')?.addEventListener('click',async()=>{try{await refreshState();}catch(error){setConnection(error.message);}});
$('experiment-form')?.addEventListener('change',()=>{updateConfigSummary();$('presets').querySelectorAll('button').forEach(button=>button.classList.remove('selected'));});
$('experiment-form')?.addEventListener('submit',event=>event.preventDefault());
for(const action of ['deploy','run','stop']) $(`${action}-button`)?.addEventListener('click',()=>submitAction(action));
$('job-select')?.addEventListener('change',async(event)=>{app.selectedJob=event.target.value || null;if(!app.selectedJob)return;app.selectedComparisons.add(app.selectedJob);try{await loadJob(app.selectedJob);renderJobChoices();renderResults();}catch(error){setConnection(error.message);}});
for(const id of ['filter-operation','filter-batch','filter-size','filter-axis','filter-metric']) $(id)?.addEventListener('change',renderResults);
window.addEventListener('resize',()=>{app.charts.forEach(instance=>instance.resize());if(!matchMedia('(max-width:900px)').matches)closeNavigation();});
setInterval(async()=>{if(!app.authenticated || document.hidden || app.pollBusy || app.loading)return;app.pollBusy=true;try{await refreshState();}catch(error){if(app.authenticated)setConnection(`状态更新失败：${error.message}。保留原任务，请勿重复启动。`);}finally{app.pollBusy=false;}},4000);
boot();
