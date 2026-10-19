#!/usr/bin/env node
// Validate actual partial or complete report data; no benchmark values are fabricated.
// QA_URL=http://127.0.0.1:18766/perf/kvc-fair-20261020/index.html QA_OUT=/tmp/kvc-qa node qa_report.mjs
// PLAYWRIGHT_MODULE and CHROME_BIN override the local browser dependencies. No benchmark services are modified.
import {pathToFileURL} from 'node:url';
const playwrightModule=process.env.PLAYWRIGHT_MODULE || '/mnt/c/Users/T14S/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const {chromium}=await import(pathToFileURL(playwrightModule).href);
import fs from 'node:fs/promises';
import path from 'node:path';

// QA_REQUIRE_PROFILE=1 requires all nine real supplemental observations; QA_EXPECT_ROWS optionally pins primary row count.
const base = process.env.QA_URL || 'http://127.0.0.1:18766/perf/kvc-fair-20261020/index.html';
const requireProfile=process.env.QA_REQUIRE_PROFILE==='1';
const expectedRows=process.env.QA_EXPECT_ROWS?Number(process.env.QA_EXPECT_ROWS):null;
const out = process.env.QA_OUT || '/home/t14s/workspace/scratch/2026-10-20-kvc-compare-report-qa/evidence';
await fs.mkdir(out, {recursive:true});
const browser = await chromium.launch({executablePath:process.env.CHROME_BIN || '/home/t14s/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const result={url:base,generated_utc:new Date().toISOString(),browserVersion:browser.version(),fixture:'Actual report input only; partial and complete primary matrices accepted. Supplemental observations remain independent.',requireProfile,expectedRows,viewports:[],links:[],errors:[]};
const check=(state,name,pass,details={})=>{state.checks.push({name,pass,...details});};
function csvRecords(text){let quoted=false,count=0;for(let i=0;i<text.length;i++){if(text[i]==='"'){if(quoted&&text[i+1]==='"')i++;else quoted=!quoted;}else if(text[i]==='\n'&&!quoted)count++;}return count+(text.length&&!text.endsWith('\n')?1:0);}
function inspectResourceSeries({profile=false,metric='memory'}={}){
  const d=window.KVC_DATA,p=profile?d.put_profile:d,index=Number(document.getElementById(profile?'put-profile-run':'timeline-run').value),row=p.rows[index];
  const samples=p.samples.filter(s=>s.mono_ns>=row.start_mono_ns&&s.mono_ns<=row.end_mono_ns),chart=window.KVC_CHARTS[profile?'put-profile':'timeline'],option=chart.getOption(),errors=[];
  const memory=s=>['server','client'].every(role=>s[role].anon!=null&&s[role].shmem!=null)?(s.server.anon+s.server.shmem+s.client.anon+s.client.shmem)/(2**30):null;
  const charged=s=>(s.server.memory+s.client.memory)/(2**30),rate=(s,role,key,scale)=>samples.length<2||s.prev_mono_ns<row.start_mono_ns||s[role][key]==null?null:s[role][key]/scale;
  const definitions=profile&&metric==='cpu'?[['服务端 CPU',s=>rate(s,'server','cpu_usec_per_s',1e6),0],['客户端 CPU',s=>rate(s,'client','cpu_usec_per_s',1e6),0]]:profile&&metric==='io'?[['服务端写',s=>rate(s,'server','write_bytes_per_s',2**20),0],['客户端写',s=>rate(s,'client','write_bytes_per_s',2**20),0],['服务端读',s=>rate(s,'server','read_bytes_per_s',2**20),0],['客户端读',s=>rate(s,'client','read_bytes_per_s',2**20),0]]:[['匿名+SHM',memory,0],['总计费内存',charged,0],...(!profile?[['服务端 CPU',s=>rate(s,'server','cpu_usec_per_s',1e6),1],['客户端 CPU',s=>rate(s,'client','cpu_usec_per_s',1e6),1]]:[])];
  const axes=profile?[metric==='cpu'?'CPU 核':metric==='memory'?'GiB':'MiB/s']:['GiB','CPU 核'];
  if(option.xAxis?.[0]?.type!=='value'||option.xAxis?.[0]?.name!=='窗口秒')errors.push('x axis');
  if(option.yAxis?.length!==axes.length||option.yAxis?.some((a,i)=>a.type!=='value'||a.name!==axes[i]))errors.push('y axes');
  if(option.series?.length!==definitions.length)errors.push('series count');
  const summaries=[];const equal=(a,b)=>a===b||Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=Math.max(1,Math.abs(b))*1e-12;
  definitions.forEach(([name,value,axis],i)=>{const actual=option.series?.[i],expected=samples.map(s=>[(s.mono_ns-row.start_mono_ns)/1e9,value(s)]);if(!actual||actual.name!==name||actual.type!=='line'||(actual.yAxisIndex||0)!==axis||actual.data?.length!==expected.length)errors.push(name+' metadata/count');else expected.forEach((point,j)=>{const a=actual.data[j];if(!Array.isArray(a)||!equal(a[0],point[0])||!equal(a[1],point[1])){if(errors.length<15)errors.push({series:name,point:j,expected:point,actual:a});}});summaries.push({name,points:expected.length,first:expected[0],last:expected.at(-1),finite:expected.filter(p=>Number.isFinite(p[1])).length});});
  return {pass:errors.length===0,errors,profile,metric,index,product:row.product,size:row.size,samples:samples.length,axes,series:summaries,fileNonzeroSamples:samples.filter(s=>(s.server.file||0)+(s.client.file||0)>0).length};
}
try {
  for (const width of [1440,900,390]) {
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000},deviceScaleFactor:1});
    const page=await context.newPage();
    const state={width,checks:[],pageErrors:[],console:[],httpErrors:[],failedRequests:[],screenshots:[]};
    result.viewports.push(state);
    page.on('pageerror',e=>state.pageErrors.push(String(e)));
    page.on('console',m=>{if(['warning','error'].includes(m.type()))state.console.push({type:m.type(),text:m.text(),location:m.location()});});
    page.on('response',r=>{if(r.status()>=400)state.httpErrors.push({status:r.status(),url:r.url(),expectedLocalApi:r.url().includes('/site-api/')});});
    page.on('requestfailed',r=>state.failedRequests.push({url:r.url(),failure:r.failure()}));
    await page.goto(base,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>window.KVC_CHARTS&&document.querySelector('.yche-toc-button'));
    await page.waitForTimeout(700);
    const screenshot=async name=>{const file=path.join(out,`${width}-${name}.png`);await page.screenshot({path:file,fullPage:false});state.screenshots.push(file);};
    await screenshot('initial');
    const initial=await page.evaluate(()=>({
      viewport:innerWidth,scrollWidth:document.documentElement.scrollWidth,bodyWidth:document.body.scrollWidth,
      tocButtons:[...document.querySelectorAll('.yche-toc-button')].filter(e=>e.getBoundingClientRect().width>0).length,
      headings:[...document.querySelectorAll('main h2')].map(e=>e.textContent.trim()),
      captions:[...document.querySelectorAll('figure')].map(f=>{const c=f.querySelector('figcaption'),chart=f.querySelector('.chart');const a=c.getBoundingClientRect(),b=chart.getBoundingClientRect();return {text:c.textContent.trim(),count:f.querySelectorAll('figcaption').length,align:getComputedStyle(c).textAlign,below:a.top>=b.bottom,centerDelta:Math.abs(a.x+a.width/2-b.x-b.width/2)};}),
      chartWidths:Object.entries(window.KVC_CHARTS).map(([id,c])=>({id,width:c.getWidth(),dom:c.getDom().getBoundingClientRect().width})),
      chartContainerCount:document.querySelectorAll('.chart').length,
      topologyBounds:(()=>{const chart=window.KVC_CHARTS.topology,series=chart.getModel().getSeriesByIndex(0);let elements;if(series?.subType==='graph'){const data=series.getData();elements=Array.from({length:data.count()},(_,i)=>({element:data.getItemGraphicEl(i),name:data.getName(i)}));}else elements=chart.getZr().storage.getDisplayList().filter(e=>['rect','tspan'].includes(e.type)).map(element=>({element,name:element.type==='rect'?'node rectangle':element.style.text}));return elements.map(({element,name})=>{const rect=element.getBoundingRect().clone(),transform=element.getComputedTransform();if(transform)rect.applyTransform(transform);return {name,left:rect.x,right:rect.x+rect.width,top:rect.y,bottom:rect.y+rect.height,chartWidth:chart.getWidth(),chartHeight:chart.getHeight()};});})(),
      rows:window.KVC_DATA.rows.length,samples:window.KVC_DATA.samples.length,identities:window.KVC_DATA.rows.map(r=>[r.operation,r.product,r.repeat,r.size,r.concurrency].join(':')),profile:!!window.KVC_DATA.put_profile,
      buildTable:{rows:document.querySelector('#build').closest('section').querySelectorAll('tbody tr').length,headerHeight:document.querySelector('#build').closest('section').querySelector('thead').getBoundingClientRect().height,tableWidth:document.querySelector('#build').closest('section').querySelector('table').getBoundingClientRect().width,wrapperWidth:document.querySelector('#build').closest('section').querySelector('.table-wrap').getBoundingClientRect().width},
      duplicateChrome:{headers:document.querySelectorAll('.yche-fixed-header').length,siteNav:document.querySelectorAll('#site-nav').length,readerBars:document.querySelectorAll('#yche-readerbar').length},
      fonts:{body:getComputedStyle(document.body).fontFamily,captions:[...document.querySelectorAll('figcaption')].map(e=>getComputedStyle(e).fontFamily),charts:Object.entries(window.KVC_CHARTS).map(([id,c])=>({id,font:c.getOption().textStyle?.fontFamily||null}))},
      phaseTicks:window.KVC_CHARTS.phases.getZr().storage.getDisplayList().filter(e=>e.type==='tspan'&&!e.ignore&&/^\d{2}:\d{2}:\d{2}$/.test(String(e.style.text))).map(e=>{const r=e.getBoundingRect().clone(),t=e.getComputedTransform();if(t)r.applyTransform(t);return {text:e.style.text,left:r.x,right:r.x+r.width,top:r.y,bottom:r.y+r.height};}).sort((a,b)=>a.left-b.left),
      phases:(()=>{const expected=(window.KVC_DATA.timings?.steps||[]).filter(s=>['build','build_total','environment','suite_wall','local_operation'].includes(s.category)&&s.start_utc&&s.end_utc&&Number.isFinite(Date.parse(s.start_utc))&&Number.isFinite(Date.parse(s.end_utc))).sort((a,b)=>Date.parse(a.start_utc)-Date.parse(b.start_utc));const option=window.KVC_CHARTS.phases.getOption(),data=option.series?.[0]?.data||[];return {expected:expected.map(s=>({start:Date.parse(s.start_utc),end:Date.parse(s.end_utc),scope:s.scope})),actual:data.map(d=>({start:d.value[0],end:d.value[1],index:d.value[2],scope:d.row?.scope})),axisType:option.xAxis?.[0]?.type,seriesType:option.series?.[0]?.type};})(),
      panelHidden:document.getElementById('yche-reader-panel').hidden
    }));
    state.initial=initial;
    check(state,'No horizontal page overflow',initial.scrollWidth<=width+1,{scrollWidth:initial.scrollWidth});
    check(state,'Exactly one visible chapter toggle',initial.tocButtons===1,{count:initial.tocButtons});
    check(state,'One shared header and no duplicate site navigation',initial.duplicateChrome.headers===1&&initial.duplicateChrome.siteNav<=1&&initial.duplicateChrome.readerBars<=1,initial.duplicateChrome);
    check(state,'Shared fonts and Microsoft YaHei chart font',initial.fonts.captions.every(f=>f===initial.fonts.body)&&initial.fonts.charts.every(c=>c.id==='put-profile'&&!initial.profile||c.font?.includes('Microsoft YaHei')),initial.fonts);
    check(state,'Phase axis labels do not overlap',initial.phaseTicks.length>0&&initial.phaseTicks.every((t,i,a)=>i===0||t.left>=a[i-1].right-1),{ticks:initial.phaseTicks});
    check(state,'Eight ECharts figures including actual phase timings',initial.chartWidths.length===8&&initial.phases.expected.length>0&&initial.phases.expected.length===initial.phases.actual.length&&initial.phases.axisType==='time'&&initial.phases.seriesType==='custom'&&initial.phases.actual.every((s,i)=>s.start===initial.phases.expected[i].start&&s.end===initial.phases.expected[i].end&&s.end>=s.start&&s.index===i&&s.scope===initial.phases.expected[i].scope),initial.phases);
    check(state,'Numbered chapters without gaps',initial.headings.length===6&&initial.headings.every((h,i)=>h.startsWith(String(i+1)+'.')),{headings:initial.headings});
    check(state,'Captions below charts, centered, single',initial.captions.length===8&&initial.captions.length===initial.chartContainerCount&&initial.captions.every(c=>c.count===1&&c.align==='center'&&c.below&&c.centerDelta<2),{captions:initial.captions});
    check(state,'All ECharts widths match containers',initial.chartWidths.length===initial.chartContainerCount&&initial.chartWidths.every(c=>c.width>0&&Math.abs(c.width-c.dom)<2),{charts:initial.chartWidths});
    if(initial.topologyBounds)check(state,'Topology nodes stay fully inside canvas',initial.topologyBounds.every(n=>n.left>=-1&&n.right<=n.chartWidth+1&&n.top>=-1&&n.bottom<=n.chartHeight+1),{nodes:initial.topologyBounds});
    check(state,'Primary measurements have unique identities and expected actual row count',initial.rows>0&&(expectedRows==null||initial.rows===expectedRows)&&new Set(initial.identities).size===initial.rows,{rows:initial.rows,samples:initial.samples,expectedRows});
    check(state,'Required supplemental profile is present',!requireProfile||initial.profile,{required:requireProfile,present:initial.profile});
    check(state,'Three real build summaries remain readable',initial.buildTable.rows===3&&initial.buildTable.headerHeight<=90,{...initial.buildTable});
    const tableScroll=await page.locator('#build').evaluate(h=>{const w=h.closest('section').querySelector('.table-wrap'),t=w.querySelector('table');w.scrollLeft=w.scrollWidth;const a=w.getBoundingClientRect(),b=t.querySelector('th:last-child').getBoundingClientRect();const info={clientWidth:w.clientWidth,scrollWidth:w.scrollWidth,scrollLeft:w.scrollLeft,tableDisplay:getComputedStyle(t).display,tableMinWidth:getComputedStyle(t).minWidth,lastHeaderLeft:b.left,lastHeaderRight:b.right,wrapperLeft:a.left,wrapperRight:a.right,pageWidth:document.documentElement.scrollWidth,viewport:innerWidth};w.scrollLeft=0;return info;});
    check(state,'Wide build table scrolls inside its wrapper',tableScroll.pageWidth<=tableScroll.viewport+1&&tableScroll.tableDisplay==='table'&&parseFloat(tableScroll.tableMinWidth)>=980&&(tableScroll.scrollWidth<=tableScroll.clientWidth||tableScroll.scrollLeft>0)&&tableScroll.lastHeaderLeft>=tableScroll.wrapperLeft-1&&tableScroll.lastHeaderRight<=tableScroll.wrapperRight+1,tableScroll);
    await page.locator('.yche-toc-button').click();
    await page.waitForTimeout(300);
    const toggled=await page.evaluate(()=>({hidden:document.getElementById('yche-reader-panel').hidden,expanded:document.querySelector('.yche-toc-button').getAttribute('aria-expanded'),chart:window.KVC_CHARTS.topology.getWidth(),dom:document.getElementById('topology').getBoundingClientRect().width,allCharts:Object.entries(window.KVC_CHARTS).map(([id,c])=>({id,width:c.getWidth(),dom:c.getDom().getBoundingClientRect().width}))}));
    check(state,'Chapter toggle changes panel state',toggled.hidden!==initial.panelHidden&&toggled.expanded===String(!toggled.hidden),toggled);
    check(state,'Charts resize after chapter panel change',toggled.allCharts.length===8&&toggled.allCharts.every(c=>Math.abs(c.width-c.dom)<2),toggled);
    if(toggled.hidden)await page.locator('.yche-toc-button').click();
    await page.locator('.yche-chapters a[href="#resources"]').click();
    await page.waitForTimeout(700);
    const navigation=await page.evaluate(()=>({hash:location.hash,active:document.querySelector('.yche-chapters a[aria-current="location"]')?.getAttribute('href'),headingTop:document.getElementById('resources').getBoundingClientRect().top,headerBottom:document.querySelector('.yche-fixed-header').getBoundingClientRect().bottom,panelHidden:document.getElementById('yche-reader-panel').hidden}));
    check(state,'Chapter link scrolls and highlights current section',navigation.hash==='#resources'&&navigation.active==='#resources'&&navigation.headingTop>=navigation.headerBottom-2&&navigation.headingTop<navigation.headerBottom+100,navigation);
    check(state,'Mobile panel closes after chapter selection',width>800||navigation.panelHidden,navigation);
    await screenshot('resources');
    const profileState=await page.evaluate(()=>{const p=window.KVC_DATA.put_profile,c=window.KVC_CHARTS['put-profile'];return {present:!!p,rows:p?.rows||[],sampleCount:p?.samples?.length||0,summary:p?.summary||null,note:document.getElementById('put-profile-note').textContent,disabled:document.getElementById('put-profile-run').disabled,options:document.getElementById('put-profile-run').options.length,graphic:c.getZr().storage.getDisplayList().filter(e=>e.type==='tspan').map(e=>e.style.text).join(' '),series:(c.getOption().series||[]).length,primaryContaminated:window.KVC_DATA.rows.some(r=>r.evidence.startsWith('results/put-resource-profile/'))};});
    state.profile={...profileState,rows:profileState.rows.map(r=>({product:r.product,size:r.size,concurrency:r.concurrency,valid:r.valid,evidence:r.evidence}))};
    if(!profileState.present){
      check(state,'Missing PUT profile shows explicit waiting state without fabricated series',profileState.disabled&&profileState.options===0&&profileState.series===0&&profileState.note.includes('尚未提供')&&profileState.graphic.includes('等待补充'),state.profile);
    }else{
      const identities=profileState.rows.map(r=>r.product+':'+r.size),expected=['datasystem','mooncake','lmcache'].flatMap(p=>[1024,65536,1048576].map(s=>p+':'+s));
      check(state,'Nine actual PUT observations are independent of the primary repeats',profileState.rows.length===9&&new Set(identities).size===9&&expected.every(id=>identities.includes(id))&&profileState.rows.every(r=>r.valid&&r.operation==='put'&&r.concurrency===8&&r.repeat===0&&r.evidence.startsWith('results/put-resource-profile/'))&&profileState.sampleCount>0&&!profileState.primaryContaminated&&!profileState.disabled&&profileState.options===9&&profileState.summary?.ended&&!profileState.summary?.fatal&&!profileState.summary?.failed?.length,state.profile);
      for(let index=0;index<profileState.rows.length;index++){
        await page.locator('#put-profile-run').selectOption(String(index));
        for(const metric of ['cpu','memory','io']){
          await page.locator('#put-profile-metric').selectOption(metric);
          const audit=await page.evaluate(inspectResourceSeries,{profile:true,metric});
          check(state,`PUT observation ${index+1} ${metric} axes and points match actual source samples`,audit.pass&&audit.index===index&&audit.samples>=2,audit);
        }
        const row=profileState.rows[index],url=new URL(row.evidence,base).href,response=await page.request.get(url),raw=await response.json();
        check(state,'Supplemental raw PUT evidence is valid and separate',response.ok()&&raw.valid===true&&raw.parameters?.op==='put'&&raw.parameters?.concurrency===8,{url,status:response.status()});
      }
      const response=await page.request.get(new URL('put-profile.csv',base).href),csv=await response.text();check(state,'PUT resource CSV has nine actual observations',response.ok()&&csvRecords(csv)-1===9,{status:response.status(),rows:csvRecords(csv)-1});
    }
    await page.locator('#put-profile').scrollIntoViewIfNeeded();await page.waitForTimeout(250);await screenshot('put-profile');
    await page.locator('#put-profile').screenshot({path:path.join(out,`${width}-put-profile-chart.png`)});
    await page.locator('#phases').scrollIntoViewIfNeeded();await page.waitForTimeout(400);await screenshot('phases');
    await page.locator('#phases').screenshot({path:path.join(out,`${width}-phases-chart.png`)});
    await page.evaluate(()=>window.KVC_CHARTS.phases.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:0}));await page.waitForTimeout(150);
    const phaseTooltip=await page.locator('#phases').innerText();check(state,'Phase tooltip exposes measured duration and scope',phaseTooltip.includes(' 秒 · ')&&phaseTooltip.includes(initial.phases.expected[0].scope),{text:phaseTooltip});
    const phaseTooltipBounds=await page.locator('#phases').evaluate(e=>{const tip=[...e.children].find(c=>c.style.position==='absolute'&&c.style.zIndex==='9999999');const r=tip?.getBoundingClientRect();return {left:r?.left,right:r?.right,docWidth:document.documentElement.scrollWidth,viewport:innerWidth,whiteSpace:tip?getComputedStyle(tip).whiteSpace:null};});check(state,'Phase tooltip remains readable inside viewport',phaseTooltipBounds.left>=-1&&phaseTooltipBounds.right<=width+1&&phaseTooltipBounds.docWidth<=width+1,phaseTooltipBounds);
    await page.evaluate(()=>window.KVC_CHARTS.phases.dispatchAction({type:'hideTip'}));
    await page.locator('#operation').selectOption('put');
    await page.locator('#size').selectOption('1024');
    await page.locator('#metric').selectOption('qps');
    const put=await page.evaluate(()=>{const selected=window.KVC_DATA.rows.filter(r=>r.operation==='put'&&r.size===1024),valid=selected.filter(r=>r.valid),groups=new Map(),label={datasystem:'DataSystem',mooncake:'Mooncake',lmcache:'LMCache'},conc=[2,8,16];for(const r of valid){const key=label[r.product]+':'+r.concurrency;const a=groups.get(key)||[];a.push(r);groups.set(key,a);}const expectedBars=[...groups].filter(([,a])=>a.length===3&&new Set(a.map(r=>r.repeat)).size===3&&a.every(r=>Number.isFinite(r.qps))).map(([key,a])=>({key,value:a.map(r=>r.qps).sort((a,b)=>a-b)[1]}));const series=window.KVC_CHARTS.throughput.getOption().series,actualBars=series.filter(s=>s.type==='bar').flatMap(s=>s.data.map((v,i)=>({key:s.name+':'+conc[i],value:v}))).filter(p=>p.value!=null);return {rows:document.querySelectorAll('#results tr').length,expected:selected.length,expectedValid:valid.length,scatter:series.filter(s=>s.type==='scatter').reduce((n,s)=>n+s.data.length,0),expectedBars,actualBars,note:document.getElementById('selection-note').textContent};});
    check(state,'PUT filters and medians use actual three distinct valid repeats',put.rows===put.expected&&put.scatter===put.expectedValid&&put.actualBars.length===put.expectedBars.length&&put.expectedBars.every(e=>put.actualBars.some(a=>a.key===e.key&&a.value===e.value)),put);
    await page.locator('#performance').scrollIntoViewIfNeeded();
    await page.waitForTimeout(500);
    await screenshot('performance');
    await page.locator('#operation').selectOption('exist_miss');
    const exist=await page.evaluate(()=>({size:document.getElementById('size').value,disabled:document.getElementById('size').disabled,metric:document.getElementById('metric').value,rows:document.querySelectorAll('#results tr').length,expected:window.KVC_DATA.rows.filter(r=>r.operation==='exist_miss'&&r.size===1024).length,options:document.getElementById('timeline-run').options.length}));
    check(state,'EXIST fixes object size and QPS',exist.size==='1024'&&exist.disabled&&exist.metric==='qps'&&exist.rows===exist.expected,exist);
    const before=await page.locator('#timeline-run').inputValue();
    const options=await page.locator('#timeline-run option').count();
    if(options>1)await page.locator('#timeline-run').selectOption({index:options-1});
    check(state,'Resource timeline selector changes real run',options>0&&(options===1||await page.locator('#timeline-run').inputValue()!==before),{options});
    const sampledRun=await page.evaluate(()=>[...document.getElementById('timeline-run').options].map(o=>o.value).find(i=>{const r=window.KVC_DATA.rows[Number(i)];return window.KVC_DATA.samples.filter(s=>s.mono_ns>=r.start_mono_ns&&s.mono_ns<=r.end_mono_ns).length>=2;}));
    if(sampledRun!=null)await page.locator('#timeline-run').selectOption(sampledRun);
    const resourceAudit=await page.evaluate(inspectResourceSeries,{});
    check(state,'Main timeline anonymous+SHM excludes file double counting and matches both CPU series',resourceAudit.pass&&resourceAudit.samples>=2,resourceAudit);
    await page.locator('details').evaluate(e=>e.open=true);
    const links=await page.evaluate(()=>{const selected=window.KVC_DATA.rows.filter(r=>r.operation===document.getElementById('operation').value&&r.size===Number(document.getElementById('size').value));return selected.map(r=>({url:new URL(r.evidence,location.href).href,valid:r.valid}));});
    for (const {url,valid} of links) {
      const response=await page.request.get(url);const raw=await response.json();
      result.links.push({viewport:width,kind:'raw-result',url,status:response.status(),valid:raw.valid});
      check(state,'Raw JSON link preserves the actual result validity',response.ok()&&raw.valid===valid,{url,status:response.status()});
    }
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('a[href="summary.csv"][download]').click()]);
    const csvFile=path.join(out,`${width}-summary.csv`);await download.saveAs(csvFile);
    const csv=await fs.readFile(csvFile,'utf8');check(state,'CSV download returns every actual primary row',csvRecords(csv)-1===initial.rows,{suggestedFilename:download.suggestedFilename(),rows:csvRecords(csv)-1,expected:initial.rows});
    for(const name of ['data.json','reproduce.zip','SHA256SUMS','docs/build.md','docs/test-design.md','docs/validation.md','docs/script-design.md','analysis.md','analysis.json','timings.json','timings.csv','docs/timing.md']) {
      const response=await page.request.get(new URL(name,base).href);result.links.push({viewport:width,kind:'artifact',name,status:response.status()});check(state,`Artifact link ${name}`,response.ok(),{status:response.status()});
    }
    await page.locator('#reset').click();
    const reset=await page.evaluate(()=>({operation:document.getElementById('operation').value,size:document.getElementById('size').value,metric:document.getElementById('metric').value,disabled:document.getElementById('size').disabled}));
    check(state,'Reset restores GET / 1MiB / MiB/s',reset.operation==='get'&&reset.size==='1048576'&&reset.metric==='throughput_mib_s'&&!reset.disabled,reset);
    check(state,'No JavaScript page exceptions',state.pageErrors.length===0,{pageErrors:state.pageErrors});
    check(state,'No failed core assets',state.httpErrors.filter(e=>!e.expectedLocalApi&&!e.url.endsWith('/favicon.ico')).length===0,{httpErrors:state.httpErrors});
    await context.close();
  }
} catch(error) {result.errors.push(error.stack||String(error));}
finally {await browser.close();}
result.checks=result.viewports.reduce((n,v)=>n+v.checks.length,0);
result.failed=result.viewports.flatMap(v=>v.checks.filter(c=>!c.pass).map(c=>({viewport:v.width,...c})));
result.pass=result.failed.length===0&&result.errors.length===0&&result.viewports.length===3;
await fs.writeFile(path.join(out,'browser-qa.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({pass:result.pass,checks:result.checks,failed:result.failed,errors:result.errors},null,2));
process.exitCode=result.pass?0:1;
