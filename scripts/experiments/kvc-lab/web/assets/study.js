const API = '/site-api/kvc-lab';
const RUNS = ['baseline', 'worker', 'client', 'metadata', 'nodes2', 'nodes4'];
const RUN_LABELS = {baseline: '基础 · 4B × 4 接口 × 3 轮', worker: 'Worker · 1 / 2 / 4 / 8', client: 'Client · 1 至 32', metadata: '中心配额 · 1 / 2 / 4 核', nodes2: '同机逻辑节点 · N=2', nodes4: '同机逻辑节点 · N=4'};
const PRODUCTS = {datasystem: 'DataSystem', mooncake: 'Mooncake'};
const OPERATIONS = {put: 'PUT', get: 'GET 命中', exist_hit: 'EXIST 命中', exist_miss: 'EXIST 未命中'};
const METRICS = {keys_s: 'keys/s', batch_qps: 'API/s', p99_us: 'P99 / μs', master_cpu_quota_utilization_pct: '中心配额利用率 / %', master_throttled_usec: '中心节流 / ms'};
const FIELDS = ['product', 'profile', 'operation', 'batch', 'size', 'nodes', 'workers', 'workers_per_node', 'clients', 'server_cpus', 'client_cpus', 'master_cpus', 'memory_gib', 'build_sha', 'source_sha256'];
const REQUIRED_METRICS = ['keys_s', 'batch_qps', 'p99_us', 'duration_s', 'server_cpu_cores', 'client_cpu_cores', 'master_cpu_cores', 'master_throttled_usec', 'peak_charged_memory_gib', 'server_cpu_quota_utilization_pct', 'client_cpu_quota_utilization_pct', 'master_cpu_quota_utilization_pct'];
const COLORS = {datasystem: '#2a65a7', mooncake: '#198778'};
const finite = value => typeof value === 'number' && Number.isFinite(value);
const integer = value => Number.isInteger(value) && value >= 0;
const list = value => Array.isArray(value) ? value : [];
const rowValue = (row, key) => key === 'size' ? row.size ?? row.value_size : row[key];
const identity = row => JSON.stringify(FIELDS.map(key => rowValue(row, key)));
const sameGroup = (left, right) => FIELDS.every(key => rowValue(left, key) === rowValue(right, key));
const esc = value => String(value ?? '—').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
const number = (value, digits = 2) => finite(value) ? value.toLocaleString('zh-CN', {maximumFractionDigits: digits}) : '—';
const compact = value => !finite(value) ? '—' : Math.abs(value) >= 1e6 ? `${number(value / 1e6, 2)}M` : Math.abs(value) >= 1e3 ? `${number(value / 1e3, 1)}k` : number(value, 1);
const sizeLabel = value => value === 1024 ? '1 KiB' : value === 65536 ? '64 KiB' : value === 1048576 ? '1 MiB' : `${number(value, 0)} B`;
const short = value => typeof value === 'string' && value.length ? value.slice(0, 12) : '—';
const repeatCount = name => name === 'baseline' ? 3 : 1;

export function completeCoverage(run) {
  const coverage = run?.coverage;
  return Boolean(run?.passed === true && run.status === 'passed' && coverage
    && integer(coverage.expected) && coverage.expected > 0
    && coverage.actual_remote_completion_rows === coverage.expected
    && ['missing', 'duplicates', 'unexpected'].every(key => Array.isArray(coverage[key]) && coverage[key].length === 0)
    && coverage.invalid_count === 0 && Array.isArray(run.rows) && run.rows.length === coverage.expected
    && run.rows.every(row => row.audit_passed === true && row.status === 'valid'));
}

export function normalizeStudy(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.runs) || !Array.isArray(data.summaries)) throw new Error('审计文件结构不匹配，未绘制性能数据。');
  const runs = new Map();
  const duplicateRuns = new Set();
  for (const run of data.runs) {
    if (!RUNS.includes(run?.name)) continue;
    if (runs.has(run.name)) duplicateRuns.add(run.name);
    runs.set(run.name, run);
  }
  const groupCounts = new Map();
  for (const summary of data.summaries) groupCounts.set(identity(summary), (groupCounts.get(identity(summary)) || 0) + 1);
  const formal = data.summaries.filter(summary => list(summary?.runs).some(name => RUNS.includes(name)));
  return {data, runs, duplicateRuns, groupCounts, summaries: formal, excludedRunCount: data.runs.filter(run => !RUNS.includes(run?.name)).length};
}

export function summaryEligibility(model, summary, metrics = REQUIRED_METRICS) {
  const reasons = [];
  const names = list(summary?.runs);
  const observations = summary?.observations;
  if (!names.length || new Set(names).size !== names.length || names.some(name => !RUNS.includes(name))) reasons.push('来源不是唯一的正式运行');
  if ((model.groupCounts.get(identity(summary)) || 0) !== 1) reasons.push('统计组身份重复');
  if (!integer(observations) || observations < 1) reasons.push('观测数缺失');
  if (['short_window_count', 'low_sample_p99_count'].some(key => !integer(summary?.[key]) || summary[key] > observations)) reasons.push('质量标记不齐全');
  if (!/^[a-f0-9]{40}$/.test(summary?.build_sha || '') || !/^[a-f0-9]{64}$/.test(summary?.source_sha256 || '')) reasons.push('版本身份缺失');
  let requiredObservations = 0;
  const expectedIdentities = [];
  for (const name of names) {
    const run = model.runs.get(name);
    const count = repeatCount(name);
    requiredObservations += count;
    if (!completeCoverage(run) || model.duplicateRuns.has(name)) reasons.push(`${name} 未通过完整矩阵验收`);
    const rows = list(run?.rows).filter(row => sameGroup(row, summary));
    if (rows.length !== count || new Set(rows.map(row => row.variant_id)).size !== 1) reasons.push(`${name} 规格 / 轮数不完整或重复`);
    for (let repeat = 0; repeat < count; repeat++) {
      const selected = rows.filter(row => row.repeat === repeat);
      if (selected.length !== 1 || selected[0]?.audit_passed !== true || selected[0]?.status !== 'valid') reasons.push(`${name} r${repeat} 缺失、重复或无效`);
      expectedIdentities.push(JSON.stringify([name, run?.job_id, repeat]));
    }
    if (rows.some(row => !integer(row.repeat) || row.repeat >= count)) reasons.push(`${name} 存在意外轮次`);
  }
  if (observations !== requiredObservations) reasons.push(`需要 ${requiredObservations} 个完整观测`);
  const declared = list(summary?.repeat_identities).map(row => JSON.stringify([row.run, row.job_id, row.repeat]));
  if (declared.length !== expectedIdentities.length || new Set(declared).size !== declared.length || expectedIdentities.some(key => !declared.includes(key))) reasons.push('重复轮次身份不匹配');
  for (const metric of metrics) {
    const stat = summary?.[metric];
    if (!stat || stat.count !== observations || !['median', 'min', 'max'].every(key => finite(stat[key])) || stat.min < 0 || stat.min > stat.median || stat.median > stat.max) reasons.push(`${metric} 指标不齐全`);
  }
  return {passed: reasons.length === 0, reasons: [...new Set(reasons)], observations: integer(observations) ? observations : null};
}

export function selectSummaries(model, chapter, operation, size, product = 'all') {
  return model.summaries.filter(summary => {
    if (summary.operation !== operation || summary.size !== size || (product !== 'all' && summary.product !== product)) return false;
    const names = list(summary.runs);
    if (chapter === 'baseline') return names.includes('baseline') && summary.profile === 'baseline' && summary.nodes === 1 && [1, 16, 256, 4096].includes(summary.batch);
    if (summary.batch !== 1) return false;
    if (chapter === 'worker') return names.includes('worker') && summary.profile === 'worker_scaling' && summary.nodes === 1;
    if (chapter === 'client') return names.includes('client') && summary.profile === 'client_scaling' && summary.nodes === 1;
    if (chapter === 'metadata') return names.includes('metadata') && summary.profile === 'metadata_scaling' && summary.nodes === 1;
    if (chapter === 'node') return summary.workers === 4 && ((summary.nodes === 1 && names.includes('worker') && summary.profile === 'worker_scaling') || (summary.nodes === 2 && names.includes('nodes2') && summary.profile === 'baseline') || (summary.nodes === 4 && names.includes('nodes4') && summary.profile === 'baseline'));
    return false;
  });
}

function seriesIdentity(summary, chapter) {
  const excluded = new Set(['operation', 'profile', 'workers_per_node']);
  excluded.add(chapter === 'baseline' ? 'batch' : chapter === 'client' ? 'clients' : chapter === 'node' ? 'nodes' : 'workers');
  return JSON.stringify(FIELDS.filter(key => !excluded.has(key)).map(key => rowValue(summary, key)));
}

export function seriesGroups(model, summaries, chapter, axis, metric, expectedProducts = Object.keys(PRODUCTS)) {
  const groups = new Map();
  for (const summary of summaries) {
    const key = seriesIdentity(summary, chapter);
    if (!groups.has(key)) groups.set(key, {product: summary.product, reference: summary, summaries: []});
    groups.get(key).summaries.push(summary);
  }
  for (const product of expectedProducts) if (![...groups.values()].some(group => group.product === product)) groups.set(`missing-${product}`, {product, reference: null, summaries: []});
  return [...groups.values()].sort((a, b) => String(a.product).localeCompare(String(b.product)) || (a.reference?.master_cpus || 0) - (b.reference?.master_cpus || 0)).map(group => ({...group, points: axis.map(value => {
    const candidates = group.summaries.filter(summary => summary[chapter === 'baseline' ? 'batch' : chapter === 'client' ? 'clients' : chapter === 'node' ? 'nodes' : 'workers'] === value);
    const summary = candidates.length === 1 ? candidates[0] : null;
    const gate = summary ? summaryEligibility(model, summary) : {passed: false, reasons: [candidates.length > 1 ? '同一横坐标有多组冲突记录' : '缺少完整且通过的统计组']};
    const stat = gate.passed ? summary[metric] : null;
    return {value: stat ? stat.median / (metric === 'master_throttled_usec' ? 1000 : 1) : null, summary, gate, stat};
  })}));
}

const app = {model: null, charts: new Map(), versions: [], generation: 0, observer: null};
const byId = id => document.getElementById(id);
const chartIds = ['baseline-put', 'baseline-get', 'baseline-exist-hit', 'baseline-exist-miss', 'worker-throughput', 'worker-latency', 'client-throughput', 'client-latency', 'metadata-throughput', 'metadata-resources', 'node-throughput', 'node-latency'];

function protectedLink(path) {
  if (typeof path !== 'string' || !path.startsWith(`${API}/`)) return null;
  const url = new URL(path, location.origin);
  return url.origin === location.origin && url.pathname.startsWith(`${API}/`) ? url.pathname + url.search + url.hash : null;
}

function jobLink(run, name) {
  return /^[a-f0-9]{32}$/.test(run?.job_id || '') ? `${API}/jobs/${run.job_id}${name ? `/artifacts/${name}` : ''}` : null;
}

function clearProtected() {
  app.model = null;
  app.versions = [];
  app.generation++;
  app.observer?.disconnect();
  for (const chart of app.charts.values()) chart.dispose();
  app.charts.clear();
  for (const id of ['study-status', 'overview', 'findings', 'run-rows', 'version-rows', 'summary-rows', 'step-records', 'observability', 'audit-identity', 'updated-at', 'baseline-quality', 'worker-quality', 'client-quality', 'metadata-quality', 'node-quality']) byId(id).replaceChildren();
  for (const element of document.querySelectorAll('[data-study-findings]')) element.remove();
  byId('study-app').hidden = true;
  closeNavigation();
}

function showAccess(title, message, auth = false) {
  clearProtected();
  byId('access-panel').hidden = false;
  byId('access-title').textContent = title;
  byId('access-message').textContent = message;
  byId('login-link').hidden = !auth;
  byId('retry-load').hidden = false;
  byId('login-link').href = `/login?next=${encodeURIComponent(location.pathname)}`;
}

async function getJson(path) {
  const response = await fetch(path, {method: 'GET', credentials: 'same-origin', cache: 'no-store', headers: {Accept: 'application/json'}});
  if (response.status === 401 || response.status === 403) {
    showAccess('需要站点管理员登录', '当前会话无法读取本报告。请使用已获授权的站点账户登录。', true);
    const error = new Error('访问权限已失效');
    error.auth = true;
    throw error;
  }
  if (!response.ok) throw new Error(response.status === 404 ? '报告数据尚未发布；未绘制任何性能数值。' : `读取失败（HTTP ${response.status}）`);
  return response.json();
}

function badge(status, label) {
  return `<span class="badge ${esc(['passed', 'valid', 'succeeded', 'failed', 'error', 'interrupted', 'waiting', 'incomplete', 'completed_with_failures'].includes(status) ? status : 'incomplete')}">${esc(label)}</span>`;
}

function renderStatus() {
  const model = app.model;
  const passed = RUNS.filter(name => completeCoverage(model.runs.get(name)) && !model.duplicateRuns.has(name)).length;
  const missing = RUNS.filter(name => !model.runs.has(name));
  const failed = model.data.status === 'failed' || [...model.runs.values()].some(run => run.passed === false) || model.duplicateRuns.size > 0;
  const allPassed = model.data.status === 'passed' && passed === RUNS.length;
  const state = failed ? 'failed' : allPassed ? 'passed' : 'waiting';
  const banner = byId('study-status');
  banner.className = `status-banner ${state}`;
  banner.innerHTML = `<strong>${state === 'passed' ? '六组运行均通过独立审计' : state === 'failed' ? '验收存在失败 · 不作完整研究结论' : '研究证据尚未齐全'}</strong>正式组 ${passed} / ${RUNS.length} 通过；审计文件状态 ${esc(model.data.status || '未知')}。${missing.length ? ` 未收录：${missing.map(name => esc(name)).join(' / ')}。` : ''}${model.excludedRunCount ? ` 已排除 ${model.excludedRunCount} 个非正式运行（含探针）。` : ''} 局部通过的数据可展示，其余保持空值。`;
  const recorded = [...model.runs.values()];
  const coverage = recorded.map(run => run.coverage);
  const totals = key => coverage.length && coverage.every(value => integer(value?.[key])) ? coverage.reduce((sum, value) => sum + value[key], 0) : null;
  const windows = recorded.flatMap(run => list(run.rows));
  const measured = windows.length && windows.every(row => finite(row.duration_s)) ? windows.reduce((sum, row) => sum + row.duration_s, 0) : null;
  byId('overview').innerHTML = [
    ['正式实验组', `${passed} / 6`, '通过独立审计 / 计划组'],
    ['原生完成记录', number(totals('actual_remote_completion_rows'), 0), `已收录组预期 ${number(totals('expected'), 0)} 条`],
    ['可用完整统计组', number(model.summaries.filter(summary => summaryEligibility(model, summary).passed).length, 0), '版本、资源指标与轮次均齐全'],
    ['实测窗口合计', finite(measured) ? `${number(measured / 60, 1)} min` : '—', '逐 case 累加，不是端到端耗时']
  ].map(([title, value, detail]) => `<div class="metric-card"><span>${esc(title)}</span><strong>${esc(value)}</strong><small>${esc(detail)}</small></div>`).join('');
  const created = model.data.created_wall_ns;
  byId('updated-at').textContent = finite(created) ? `独立审计生成：${new Date(created / 1e6).toLocaleString('zh-CN', {hour12: false})}` : '审计生成时间未提供';
  byId('audit-identity').textContent = `Auditor SHA256: ${model.data.auditor_sha256 || '未提供'}`;
  renderFindings();
  renderObservability();
}

function renderFindings() {
  byId('findings').replaceChildren();
  for (const element of document.querySelectorAll('[data-study-findings]')) element.remove();
  const sections = {baseline: 'baseline', worker: 'workers', client: 'clients', metadata: 'metadata', nodes: 'nodes', evidence: 'acceptance'};
  for (const finding of list(app.model.data.findings)) {
    if (!finding || typeof finding.text !== 'string' || !sections[finding.section]) continue;
    const element = document.createElement('div');
    element.className = 'finding';
    element.textContent = finding.text;
    for (const id of list(finding.job_ids)) {
      const url = jobLink({job_id: id});
      if (!url) continue;
      const link = document.createElement('a');
      link.href = url;
      link.textContent = ` 任务 ${short(id)} ↗`;
      element.append(link);
    }
    if (finding.section === 'baseline') byId('findings').append(element);
    else {
      element.dataset.studyFindings = finding.section;
      const section = byId(sections[finding.section]);
      const anchor = section.querySelector('.controls, .table-scroll');
      if (anchor) section.insertBefore(element, anchor);
      else section.append(element);
    }
  }
}

function renderObservability() {
  const target = byId('observability');
  target.replaceChildren();
  const value = app.model.data.observability;
  if (!value) return;
  const text = typeof value === 'string' ? value : value.text || value.summary;
  if (typeof text === 'string' && text) {
    const paragraph = document.createElement('p');
    paragraph.className = 'note';
    paragraph.textContent = text;
    target.append(paragraph);
  }
  const runs = Array.isArray(value) ? value : list(value.runs);
  for (const run of runs) {
    if (!run || typeof run !== 'object') continue;
    const paragraph = document.createElement('p');
    paragraph.className = 'note';
    paragraph.textContent = `${run.name || run.run || '未命名运行'}：IO 窗口 known ${number(run.known_windows, 0)} / unknown ${number(run.unknown_windows, 0)}；读取增量 ${number(run.read_bytes, 0)} B / 写入增量 ${number(run.write_bytes, 0)} B。采样首尾增量不是精确 SDK 总量。${typeof run.text === 'string' ? ` ${run.text}` : ''}`;
    target.append(paragraph);
  }
}

function quality(summary) {
  return [summary?.short_window_count ? `short_window ${summary.short_window_count} 个观测` : '', summary?.low_sample_p99_count ? `low_sample_p99 ${summary.low_sample_p99_count} 个观测` : '', ['short_window_count', 'low_sample_p99_count'].some(key => !integer(summary?.[key])) ? '质量标记未完整提供' : '', summary?.operation === 'put' ? 'PUT：有界 fresh 插入，不是定时 upsert' : ''].filter(Boolean);
}

function renderQuality(id, summaries) {
  const element = byId(id);
  const shortWindows = summaries.reduce((sum, summary) => sum + (integer(summary.short_window_count) ? summary.short_window_count : 0), 0);
  const lowSamples = summaries.reduce((sum, summary) => sum + (integer(summary.low_sample_p99_count) ? summary.low_sample_p99_count : 0), 0);
  const rejected = summaries.filter(summary => !summaryEligibility(app.model, summary).passed).length;
  const notes = [];
  if (shortWindows) notes.push(`short_window：${shortWindows} 个观测的实测窗口不足 10 秒，保留短窗口警告。`);
  if (lowSamples) notes.push(`low_sample_p99：${lowSamples} 个观测存在尾延迟样本不足标记。`);
  if (summaries.some(summary => summary.operation === 'put')) notes.push('PUT 为有界 fresh 插入，容量约束可能使窗口短于请求时长。');
  if (rejected) notes.push(`${rejected} 个统计组未通过完整绘图门禁，图中保留空值，原因见第 6 章。`);
  element.hidden = notes.length === 0;
  element.textContent = notes.join(' ');
}

function groupName(group, chapter, groups) {
  const summary = group.reference;
  let name = PRODUCTS[group.product] || group.product;
  if (!summary) return `${name} · 尚无完整组`;
  if (chapter === 'metadata') name += ` · 中心配额 ${summary.master_cpus}`;
  const sameProduct = groups.filter(item => item.product === group.product && item.reference);
  const variantBudget = new Set(sameProduct.map(item => JSON.stringify([item.reference.server_cpus, item.reference.client_cpus, item.reference.memory_gib, item.reference.clients, item.reference.workers, item.reference.nodes, item.reference.build_sha, item.reference.source_sha256]))).size;
  if (sameProduct.length > (chapter === 'metadata' ? 3 : 1) || (chapter !== 'metadata' && variantBudget > 1)) name += ` · S${summary.server_cpus}/C${summary.client_cpus}/M${summary.master_cpus}/R${summary.memory_gib} · ${short(summary.build_sha)}`;
  return name;
}

function groupColor(group, chapter) {
  if (chapter !== 'metadata' || !group.reference) return COLORS[group.product] || '#73889a';
  const shades = {datasystem: {1: '#82a9d2', 2: '#2a65a7', 4: '#163e6c'}, mooncake: {1: '#80bdb0', 2: '#198778', 4: '#12594f'}};
  return shades[group.product]?.[group.reference.master_cpus] || COLORS[group.product] || '#73889a';
}

function tooltip(point, metric, seriesName) {
  const summary = point?.summary;
  if (!summary || !point.gate?.passed || !point.stat) return '';
  const divisor = metric === 'master_throttled_usec' ? 1000 : 1;
  const stat = point.stat;
  return `<strong>${esc(seriesName)}</strong><br>${esc(OPERATIONS[summary.operation] || summary.operation)} · B${summary.batch} · ${esc(sizeLabel(summary.size))}<br>${esc(METRICS[metric] || metric)}：${number(stat.median / divisor)} [${number(stat.min / divisor)}, ${number(stat.max / divisor)}]<br>n=${summary.observations} · 来源 ${esc(list(summary.runs).join(' / '))}<br>N${summary.nodes} / W${summary.workers} / C${summary.clients} · CPU S${summary.server_cpus}/C${summary.client_cpus}/中心配额${summary.master_cpus} · ${summary.memory_gib} GiB<br>实测窗口 ${number(summary.duration_s?.median, 3)} s · 平均 CPU 服务端 ${number(summary.server_cpu_cores?.median)} / 客户端 ${number(summary.client_cpu_cores?.median)} / 中心 ${number(summary.master_cpu_cores?.median)} 核<br>Build ${esc(short(summary.build_sha))} · Source ${esc(short(summary.source_sha256))}${quality(summary).map(note => `<br><span style="color:#986c22">${esc(note)}</span>`).join('')}`;
}

function baseOption(axis, axisName, metric) {
  const narrow = window.innerWidth <= 450;
  return {animation: false, textStyle: {fontFamily: 'sans-serif'}, color: Object.values(COLORS), legend: {type: 'scroll', top: 7, left: 5, right: 8, textStyle: {fontSize: 10}, pageIconSize: 10}, grid: {top: 75, left: narrow ? 44 : 55, right: 16, bottom: 50, containLabel: false}, tooltip: {trigger: 'axis', confine: true, appendToBody: false, textStyle: {fontSize: 10}, extraCssText: 'max-width:min(440px,80vw);white-space:normal;overflow-wrap:anywhere;line-height:1.9', formatter: entries => list(Array.isArray(entries) ? entries : [entries]).map(entry => entry.data?.study ? tooltip(entry.data.study, entry.data.metric || metric, entry.seriesName) : '').filter(Boolean).join('<br><br>') || '未通过完整验收或未采集'}, xAxis: {type: 'category', data: axis.map(String), name: axisName, nameLocation: 'middle', nameGap: 29, axisLabel: {fontSize: 10, interval: 0}, axisLine: {lineStyle: {color: '#b7c9d8'}}, axisTick: {show: false}}, yAxis: {type: 'value', name: METRICS[metric] || metric, nameTextStyle: {fontSize: 10, color: '#61768c'}, axisLabel: {fontSize: 10, formatter: compact}, splitLine: {lineStyle: {color: '#e9eff5'}}, min: 0}, series: []};
}

function draw(id, option) {
  if (!window.echarts) {
    byId(id).textContent = '图表依赖尚未就绪；数值与验收状态可在第 6 章读取。';
    byId(id).classList.add('empty');
    return;
  }
  let chart = app.charts.get(id);
  if (!chart) {
    chart = window.echarts.init(byId(id), null, {renderer: 'canvas'});
    app.charts.set(id, chart);
  }
  chart.setOption(option, {notMerge: true});
}

function rangeRenderer(index, count, bar) {
  return (params, api) => {
    const [x, median, min, max] = [0, 1, 2, 3].map(key => api.value(key));
    if (![x, median, min, max].every(finite)) return null;
    const center = api.coord([x, median]);
    const low = api.coord([x, min]);
    const high = api.coord([x, max]);
    const width = Math.min(23, Math.abs(api.size([1, 0])[0]) * .62 / count);
    const offset = bar ? (index - (count - 1) / 2) * width * 1.25 : 0;
    const color = api.visual('color');
    const actualX = center[0] + offset;
    const children = [];
    if (bar) {
      const base = api.coord([x, 0]);
      children.push({type: 'rect', shape: {x: actualX - width / 2, y: center[1], width, height: Math.max(0, base[1] - center[1])}, style: {fill: color, opacity: .84}});
    }
    if (min !== max || bar) children.push({type: 'line', shape: {x1: actualX, y1: low[1], x2: actualX, y2: high[1]}, style: {stroke: color, lineWidth: 1.4}}, ...[low, high].map(coordinate => ({type: 'line', shape: {x1: actualX - 4, y1: coordinate[1], x2: actualX + 4, y2: coordinate[1]}, style: {stroke: color, lineWidth: 1.4}})));
    return {type: 'group', children};
  };
}

function drawPerformance(id, summaries, chapter, axis, axisName, metric, bars = false) {
  const selectedProduct = byId('metadata-product').value;
  const groups = seriesGroups(app.model, summaries, chapter, axis, metric, chapter === 'metadata' && selectedProduct !== 'all' ? [selectedProduct] : Object.keys(PRODUCTS));
  const option = baseOption(axis, axisName, metric);
  let hasValue = false;
  groups.forEach((group, index) => {
    const name = groupName(group, chapter, groups);
    const color = groupColor(group, chapter);
    const data = group.points.map((point, x) => ({value: bars ? [x, point.value, point.stat?.min ?? null, point.stat?.max ?? null] : point.value, study: point, metric}));
    if (group.points.some(point => finite(point.value))) hasValue = true;
    if (bars) option.series.push({type: 'custom', name, renderItem: rangeRenderer(index, groups.length, true), data, encode: {x: 0, y: [1, 2, 3]}, itemStyle: {color}, clip: true});
    else {
      option.series.push({type: 'line', name, data, connectNulls: false, symbol: 'circle', symbolSize: 6, lineStyle: {width: 2, type: chapter === 'metadata' && group.reference?.master_cpus === 1 ? 'dotted' : chapter === 'metadata' && group.reference?.master_cpus === 4 ? 'dashed' : 'solid'}, itemStyle: {color}});
      option.series.push({type: 'custom', name, renderItem: rangeRenderer(index, groups.length, false), data: group.points.map((point, x) => [x, point.value, point.stat?.min ?? null, point.stat?.max ?? null]), encode: {x: 0, y: [1, 2, 3]}, itemStyle: {color}, silent: true, tooltip: {show: false}, clip: true});
    }
  });
  if (!hasValue) option.graphic = [{type: 'text', left: 'center', top: 'middle', style: {text: '尚无通过完整审计的数据\n缺失值不补零', fill: '#7b8fa1', fontSize: 12, lineHeight: 23, align: 'center'}}];
  draw(id, option);
}

function drawCenter(summaries) {
  const axis = [1, 2, 4, 8];
  const option = baseOption(axis, 'Workers 总数', 'master_cpu_quota_utilization_pct');
  option.grid.right = window.innerWidth <= 450 ? 42 : 57;
  option.yAxis = [option.yAxis, {type: 'value', name: '节流 / ms', min: 0, nameTextStyle: {fontSize: 10, color: '#61768c'}, axisLabel: {fontSize: 10, formatter: compact}, splitLine: {show: false}}];
  let hasValue = false;
  const selectedProduct = byId('metadata-product').value;
  for (const [metric, yAxisIndex, suffix] of [['master_cpu_quota_utilization_pct', 0, '利用率'], ['master_throttled_usec', 1, '节流 ms']]) {
    const groups = seriesGroups(app.model, summaries, 'metadata', axis, metric, selectedProduct === 'all' ? Object.keys(PRODUCTS) : [selectedProduct]);
    for (const group of groups) {
      if (group.points.some(point => finite(point.value))) hasValue = true;
      option.series.push({type: 'line', name: `${groupName(group, 'metadata', groups)} · ${suffix}`, yAxisIndex, connectNulls: false, symbol: yAxisIndex ? 'emptyCircle' : 'circle', symbolSize: 5, lineStyle: {width: yAxisIndex ? 1.4 : 2, type: yAxisIndex ? 'dashed' : 'solid'}, itemStyle: {color: groupColor(group, 'metadata')}, data: group.points.map(point => ({value: point.value, study: point, metric}))});
    }
  }
  if (!hasValue) option.graphic = [{type: 'text', left: 'center', top: 'middle', style: {text: '尚无通过完整审计的中心资源观测', fill: '#7b8fa1', fontSize: 11}}];
  draw('metadata-resources', option);
}

function renderCharts() {
  if (!app.model) return;
  const size = Number(byId('value-size').value);
  const baseline = [];
  for (const [operation, id] of [['put', 'baseline-put'], ['get', 'baseline-get'], ['exist_hit', 'baseline-exist-hit'], ['exist_miss', 'baseline-exist-miss']]) {
    const summaries = selectSummaries(app.model, 'baseline', operation, size);
    baseline.push(...summaries);
    drawPerformance(id, summaries, 'baseline', [1, 16, 256, 4096], '每次调用 keys 数', byId('baseline-metric').value, true);
  }
  renderQuality('baseline-quality', baseline);
  for (const [chapter, axis, axisName, operationId] of [['worker', [1, 2, 4, 8], 'Workers 总数', 'worker-operation'], ['client', [1, 2, 4, 8, 16, 32], 'Clients 并发数', 'client-operation'], ['node', [1, 2, 4], '同机逻辑节点数', 'node-operation']]) {
    const summaries = selectSummaries(app.model, chapter, byId(operationId).value, size);
    drawPerformance(`${chapter}-throughput`, summaries, chapter, axis, axisName, 'keys_s');
    drawPerformance(`${chapter}-latency`, summaries, chapter, axis, axisName, 'p99_us');
    renderQuality(`${chapter}-quality`, summaries);
  }
  const metadata = selectSummaries(app.model, 'metadata', byId('metadata-operation').value, size, byId('metadata-product').value);
  drawPerformance('metadata-throughput', metadata, 'metadata', [1, 2, 4, 8], 'Workers 总数', 'keys_s');
  drawCenter(metadata);
  renderQuality('metadata-quality', metadata);
}

function statText(stat, divisor = 1) {
  return stat && finite(stat.median) && finite(stat.min) && finite(stat.max) ? `${number(stat.median / divisor)} [${number(stat.min / divisor)}, ${number(stat.max / divisor)}]` : '—';
}

function labelTables() {
  for (const table of document.querySelectorAll('.acceptance-table, .version-table, .summary-table')) {
    table.setAttribute('role', 'table');
    const headers = [...table.querySelectorAll('thead th')];
    const labels = headers.map(header => {
      const clone = header.cloneNode(true);
      for (const line of clone.querySelectorAll('br')) line.replaceWith(' · ');
      return clone.textContent.replace(/\s+/g, ' ').trim();
    });
    for (const [index, header] of headers.entries()) {
      header.id ||= `study-${table.classList[0]}-column-${index}`;
      header.scope = 'col';
      header.setAttribute('role', 'columnheader');
    }
    for (const group of table.querySelectorAll('thead, tbody')) group.setAttribute('role', 'rowgroup');
    for (const row of table.querySelectorAll('tr')) row.setAttribute('role', 'row');
    for (const row of table.querySelectorAll('tbody tr')) {
      for (const [index, cell] of [...row.children].entries()) {
        cell.dataset.label = cell.colSpan > 1 ? '数据状态' : labels[index] || '数据';
        cell.setAttribute('role', 'cell');
        if (headers[index]) cell.setAttribute('headers', headers[index].id);
      }
    }
  }
}

function renderRecords() {
  const model = app.model;
  byId('run-rows').innerHTML = RUNS.map(name => {
    const run = model.runs.get(name);
    const coverage = run?.coverage;
    const success = completeCoverage(run) && !model.duplicateRuns.has(name);
    const state = success ? 'passed' : run?.passed === false ? 'failed' : 'waiting';
    const rows = list(run?.rows);
    const seconds = rows.length && rows.every(row => finite(row.duration_s)) ? rows.reduce((sum, row) => sum + row.duration_s, 0) : null;
    const checks = run?.checks;
    const artifacts = [['results.json', '结果'], ['steps.json', '逐步回执'], ['events.jsonl', '事件'], ['reproduce.sh', '复现脚本'], ['source.tar.gz', '任务源码包']].map(([file, label]) => jobLink(run, file) ? `<a href="${esc(jobLink(run, file))}">${label}</a>` : '').join('');
    return `<tr><td><strong>${esc(RUN_LABELS[name])}</strong><small>${esc(run?.job_id || '尚未收录运行')}</small></td><td>${badge(state, success ? '通过' : run?.passed === false ? '失败' : '等待完整证据')}<small>原始状态 ${esc(run?.status || '未收录')}</small>${list(run?.issues).slice(0, 4).map(issue => `<small>${esc(issue?.code || issue)}</small>`).join('')}</td><td>${number(coverage?.expected, 0)} / ${number(coverage?.actual_remote_completion_rows, 0)}</td><td>${['missing', 'duplicates', 'unexpected'].map(key => Array.isArray(coverage?.[key]) ? number(coverage[key].length, 0) : '—').join(' / ')} / ${number(coverage?.invalid_count, 0)}</td><td>${number(checks?.passed, 0)} 通过<small>${number(checks?.failed, 0)} 失败${checks?.failed === undefined ? '（未提供计数）' : ''}</small></td><td>${finite(seconds) ? `${number(seconds, 2)} s` : '—'}<small>${rows.length ? `${rows.length} 条实际窗口累加` : '没有窗口数据'}；全程耗时见逐步回执</small></td><td><div class="artifact-links">${artifacts || '尚无原件链接'}</div>${jobLink(run) ? `<small><a href="${esc(jobLink(run))}">任务 JSON ↗</a></small>` : ''}</td></tr>`;
  }).join('');
  byId('step-records').replaceChildren();
  for (const name of RUNS) {
    const run = model.runs.get(name);
    if (!jobLink(run, 'steps.json')) continue;
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = `${RUN_LABELS[name]} · 读取各步骤实际耗时与验收`;
    const contents = document.createElement('div');
    contents.className = 'step-message';
    contents.textContent = '展开时通过 GET 读取本任务逐步回执；不会执行实验。';
    details.append(summary, contents);
    let loaded = false;
    details.addEventListener('toggle', async () => {
      if (!details.open || loaded || !app.model) return;
      loaded = true;
      contents.textContent = '正在读取逐步回执…';
      const generation = app.generation;
      try {
        const data = await getJson(jobLink(run, 'steps.json'));
        if (generation !== app.generation || !app.model) return;
        const steps = Array.isArray(data) ? data : list(data.steps);
        contents.innerHTML = steps.length ? steps.map(step => `<div class="step-row"><strong>${esc(step.name || step.step || '未命名步骤')}</strong>${badge(step.status, step.status || '未知')}<span class="duration">${finite(step.duration_s) ? `${number(step.duration_s, 3)} s` : '耗时未提供'}</span>${protectedLink(step.artifact_url) ? `<a href="${esc(protectedLink(step.artifact_url))}">验收原件 ↗</a>` : ''}</div>`).join('') : '回执尚无步骤；不以零秒补齐。';
      } catch (error) {
        if (!error.auth && generation === app.generation) contents.textContent = `逐步回执读取失败：${error.message}`;
      }
    });
    byId('step-records').append(details);
  }
  const versions = new Map();
  for (const run of model.runs.values()) for (const row of list(run.rows)) {
    if (row.product && row.build_sha && row.source_sha256) versions.set(JSON.stringify([row.product, row.build_sha, row.source_sha256]), row);
  }
  byId('version-rows').innerHTML = [...versions.values()].map(row => {
    const matched = app.versions.find(version => version.product === row.product && version.sha === row.build_sha);
    return `<tr><td>${esc(PRODUCTS[row.product] || row.product)}</td><td>${esc(matched?.tag || '未获取到匹配 SHA 的 Tag')}</td><td><code>${esc(row.build_sha)}</code></td><td><code>${esc(row.source_sha256)}</code></td></tr>`;
  }).join('') || '<tr><td colspan="4" class="empty">正式运行未提供版本身份；不从探针或旧报告补入。</td></tr>';
  byId('summary-rows').innerHTML = model.summaries.map(summary => {
    const gate = summaryEligibility(model, summary);
    return `<tr><td>${esc(list(summary.runs).join(' / '))}<br><strong>${esc(PRODUCTS[summary.product] || summary.product)}</strong> · ${esc(OPERATIONS[summary.operation] || summary.operation)}<small>${esc(summary.profile)}<br>Build ${esc(short(summary.build_sha))}<br>Source ${esc(short(summary.source_sha256))}</small></td><td>B${esc(summary.batch)} · ${esc(sizeLabel(summary.size))}<br>N${esc(summary.nodes)} / W${esc(summary.workers)} / C${esc(summary.clients)}</td><td>S${esc(summary.server_cpus)} / C${esc(summary.client_cpus)} / 中心配额 ${esc(summary.master_cpus)}<br>${esc(summary.memory_gib)} GiB</td><td>${esc(summary.observations)} 个观测<br>${badge(gate.passed ? 'passed' : 'incomplete', gate.passed ? '完整可画' : '不绘制数值')}${gate.reasons.map(reason => `<small>${esc(reason)}</small>`).join('')}</td><td>${gate.passed ? statText(summary.keys_s) : '—'}</td><td>${gate.passed ? statText(summary.p99_us) : '—'}</td><td>${['server', 'client', 'master'].map(role => finite(summary[`${role}_cpu_cores`]?.median) ? number(summary[`${role}_cpu_cores`].median, 3) : '—').join(' / ')}</td><td>${statText(summary.master_throttled_usec, 1000)}</td><td>${statText(summary.peak_charged_memory_gib)}</td><td>${quality(summary).map(note => `<span class="quality-label">${esc(note)}</span>`).join('') || '没有短窗口 / 低样本标记'}<small>资源窗口覆盖率 ${statText(summary.resource_window_coverage)}</small></td></tr>`;
  }).join('') || '<tr><td colspan="10" class="empty">没有正式统计组。缺失状态保留在上方验收表。</td></tr>';
  labelTables();
}

async function load() {
  clearProtected();
  const generation = app.generation;
  byId('access-panel').hidden = false;
  byId('access-title').textContent = '正在读取验收证据';
  byId('access-message').textContent = '读取同源独立审计文件；未加载的数据不会显示为零。';
  byId('login-link').hidden = true;
  byId('retry-load').hidden = true;
  try {
    const data = await getJson('data/study.json');
    if (generation !== app.generation) return;
    app.model = normalizeStudy(data);
    const sizes = [...new Set(app.model.summaries.map(summary => summary.size).filter(value => finite(value) && value > 0))].sort((a, b) => a - b);
    byId('value-size').innerHTML = (sizes.length ? sizes : [1024]).map(value => `<option value="${value}">${esc(sizeLabel(value))}</option>`).join('');
    byId('access-panel').hidden = true;
    byId('study-app').hidden = false;
    renderStatus();
    renderRecords();
    renderCharts();
    if (window.ResizeObserver) {
      app.observer?.disconnect();
      app.observer = new ResizeObserver(() => {for (const chart of app.charts.values()) chart.resize();});
      for (const id of chartIds) app.observer.observe(byId(id));
    }
    try {
      const catalog = await getJson(`${API}/catalog`);
      if (generation !== app.generation || !app.model) return;
      app.versions = list(catalog.versions).map(({product, tag, sha}) => ({product, tag, sha}));
      renderRecords();
    } catch (error) {
      if (!error.auth && generation === app.generation && app.model) byId('version-rows').insertAdjacentHTML('beforeend', '<tr><td colspan="4">受保护版本目录读取失败；保留审计文件中的 SHA，Tag 未补写。</td></tr>');
    }
  } catch (error) {
    if (!error.auth && generation === app.generation) showAccess('报告证据尚不可用', error.message);
  }
}

function closeNavigation() {
  byId('study-navigation').classList.remove('open');
  byId('nav-backdrop').hidden = true;
  byId('menu-toggle').setAttribute('aria-expanded', 'false');
}

function initialize() {
  byId('menu-toggle').addEventListener('click', () => {
    const open = byId('study-navigation').classList.toggle('open');
    byId('nav-backdrop').hidden = !open;
    byId('menu-toggle').setAttribute('aria-expanded', String(open));
  });
  byId('nav-backdrop').addEventListener('click', closeNavigation);
  document.addEventListener('keydown', event => {if (event.key === 'Escape') closeNavigation();});
  const links = [...document.querySelectorAll('.navigation nav a')];
  for (const link of links) link.addEventListener('click', closeNavigation);
  if (window.IntersectionObserver) {
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible.length) for (const link of links) link.classList.toggle('active', link.hash === `#${visible[0].target.id}`);
    }, {rootMargin: '-70px 0px -62% 0px'});
    for (const section of document.querySelectorAll('main section')) observer.observe(section);
  }
  for (const id of ['value-size', 'baseline-metric', 'worker-operation', 'client-operation', 'metadata-operation', 'metadata-product', 'node-operation']) byId(id).addEventListener('change', renderCharts);
  for (const id of ['refresh-data', 'retry-load']) byId(id).addEventListener('click', load);
  window.addEventListener('resize', () => {for (const chart of app.charts.values()) chart.resize();});
  window.addEventListener('pageshow', event => {if (event.persisted) load();});
  window.addEventListener('pagehide', clearProtected);
  document.addEventListener('visibilitychange', () => {if (document.visibilityState === 'visible' && app.model) load();});
  load();
}

if (typeof document !== 'undefined') initialize();
