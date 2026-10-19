#!/usr/bin/env node
// Run against a generated real orchestration-smoke fixture with 8 rows, 3 builds, and timings.
// QA_URL=http://127.0.0.1:18766/perf/kvc-fair-20261020/index.html QA_OUT=/tmp/kvc-qa node qa_report.mjs
// PLAYWRIGHT_MODULE and CHROME_BIN override the local browser dependencies. No benchmark services are modified.
import {pathToFileURL} from 'node:url';
const playwrightModule=process.env.PLAYWRIGHT_MODULE || '/mnt/c/Users/T14S/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const {chromium}=await import(pathToFileURL(playwrightModule).href);
import fs from 'node:fs/promises';
import path from 'node:path';

// This validator deliberately uses the real eight-row correctness fixture, never synthetic performance data.
const base = process.env.QA_URL || 'http://127.0.0.1:18766/perf/kvc-fair-20261020/index.html';
const out = process.env.QA_OUT || '/home/t14s/workspace/scratch/2026-10-20-kvc-compare-report-qa/evidence';
await fs.mkdir(out, {recursive:true});
const browser = await chromium.launch({executablePath:process.env.CHROME_BIN || '/home/t14s/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const result={url:base,generated_utc:new Date().toISOString(),browserVersion:browser.version(),fixture:'Only actual orchestration-smoke: 2 cells, 8 correctness measurements; no ranking or formal performance claim.',viewports:[],links:[],errors:[]};
const check=(state,name,pass,details={})=>{state.checks.push({name,pass,...details});};
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
      rows:window.KVC_DATA.rows.length,
      buildTable:{rows:document.querySelector('#build').closest('section').querySelectorAll('tbody tr').length,headerHeight:document.querySelector('#build').closest('section').querySelector('thead').getBoundingClientRect().height,tableWidth:document.querySelector('#build').closest('section').querySelector('table').getBoundingClientRect().width,wrapperWidth:document.querySelector('#build').closest('section').querySelector('.table-wrap').getBoundingClientRect().width},
      duplicateChrome:{headers:document.querySelectorAll('.yche-fixed-header').length,siteNav:document.querySelectorAll('#site-nav').length,readerBars:document.querySelectorAll('#yche-readerbar').length},
      fonts:{body:getComputedStyle(document.body).fontFamily,captions:[...document.querySelectorAll('figcaption')].map(e=>getComputedStyle(e).fontFamily),charts:Object.values(window.KVC_CHARTS).map(c=>c.getOption().textStyle.fontFamily)},
      phaseTicks:window.KVC_CHARTS.phases.getZr().storage.getDisplayList().filter(e=>e.type==='tspan'&&!e.ignore&&/^\d{2}:\d{2}:\d{2}$/.test(String(e.style.text))).map(e=>{const r=e.getBoundingRect().clone(),t=e.getComputedTransform();if(t)r.applyTransform(t);return {text:e.style.text,left:r.x,right:r.x+r.width,top:r.y,bottom:r.y+r.height};}).sort((a,b)=>a.left-b.left),
      phases:(()=>{const expected=(window.KVC_DATA.timings?.steps||[]).filter(s=>['build','build_total','environment','suite_wall','local_operation'].includes(s.category)&&s.start_utc&&s.end_utc&&Number.isFinite(Date.parse(s.start_utc))&&Number.isFinite(Date.parse(s.end_utc))).sort((a,b)=>Date.parse(a.start_utc)-Date.parse(b.start_utc));const option=window.KVC_CHARTS.phases.getOption(),data=option.series?.[0]?.data||[];return {expected:expected.map(s=>({start:Date.parse(s.start_utc),end:Date.parse(s.end_utc),scope:s.scope})),actual:data.map(d=>({start:d.value[0],end:d.value[1],index:d.value[2],scope:d.row?.scope})),axisType:option.xAxis?.[0]?.type,seriesType:option.series?.[0]?.type};})(),
      panelHidden:document.getElementById('yche-reader-panel').hidden
    }));
    state.initial=initial;
    check(state,'No horizontal page overflow',initial.scrollWidth<=width+1,{scrollWidth:initial.scrollWidth});
    check(state,'Exactly one visible chapter toggle',initial.tocButtons===1,{count:initial.tocButtons});
    check(state,'One shared header and no duplicate site navigation',initial.duplicateChrome.headers===1&&initial.duplicateChrome.siteNav<=1&&initial.duplicateChrome.readerBars<=1,initial.duplicateChrome);
    check(state,'Shared fonts and Microsoft YaHei chart font',initial.fonts.captions.every(f=>f===initial.fonts.body)&&initial.fonts.charts.every(f=>f.includes('Microsoft YaHei')),initial.fonts);
    check(state,'Phase axis labels do not overlap',initial.phaseTicks.length>0&&initial.phaseTicks.every((t,i,a)=>i===0||t.left>=a[i-1].right-1),{ticks:initial.phaseTicks});
    check(state,'Seven ECharts figures including actual phase timings',initial.chartWidths.length===7&&initial.phases.expected.length>0&&initial.phases.expected.length===initial.phases.actual.length&&initial.phases.axisType==='time'&&initial.phases.seriesType==='custom'&&initial.phases.actual.every((s,i)=>s.start===initial.phases.expected[i].start&&s.end===initial.phases.expected[i].end&&s.end>=s.start&&s.index===i&&s.scope===initial.phases.expected[i].scope),initial.phases);
    check(state,'Numbered chapters without gaps',initial.headings.length>=6&&initial.headings.every((h,i)=>h.startsWith(String(i+1)+'.')),{headings:initial.headings});
    check(state,'Captions below charts, centered, single',initial.captions.length>=6&&initial.captions.length===initial.chartContainerCount&&initial.captions.every(c=>c.count===1&&c.align==='center'&&c.below&&c.centerDelta<2),{captions:initial.captions});
    check(state,'All ECharts widths match containers',initial.chartWidths.length===initial.chartContainerCount&&initial.chartWidths.every(c=>c.width>0&&Math.abs(c.width-c.dom)<2),{charts:initial.chartWidths});
    if(initial.topologyBounds)check(state,'Topology nodes stay fully inside canvas',initial.topologyBounds.every(n=>n.left>=-1&&n.right<=n.chartWidth+1&&n.top>=-1&&n.bottom<=n.chartHeight+1),{nodes:initial.topologyBounds});
    check(state,'QA fixture has exactly eight real results',initial.rows===8);
    check(state,'Three real build summaries remain readable',initial.buildTable.rows===3&&initial.buildTable.headerHeight<=90,{...initial.buildTable});
    const tableScroll=await page.locator('#build').evaluate(h=>{const w=h.closest('section').querySelector('.table-wrap'),t=w.querySelector('table');w.scrollLeft=w.scrollWidth;const a=w.getBoundingClientRect(),b=t.querySelector('th:last-child').getBoundingClientRect();const info={clientWidth:w.clientWidth,scrollWidth:w.scrollWidth,scrollLeft:w.scrollLeft,tableDisplay:getComputedStyle(t).display,tableMinWidth:getComputedStyle(t).minWidth,lastHeaderLeft:b.left,lastHeaderRight:b.right,wrapperLeft:a.left,wrapperRight:a.right,pageWidth:document.documentElement.scrollWidth,viewport:innerWidth};w.scrollLeft=0;return info;});
    check(state,'Wide build table scrolls inside its wrapper',tableScroll.pageWidth<=tableScroll.viewport+1&&tableScroll.tableDisplay==='table'&&parseFloat(tableScroll.tableMinWidth)>=980&&(tableScroll.scrollWidth<=tableScroll.clientWidth||tableScroll.scrollLeft>0)&&tableScroll.lastHeaderLeft>=tableScroll.wrapperLeft-1&&tableScroll.lastHeaderRight<=tableScroll.wrapperRight+1,tableScroll);
    await page.locator('.yche-toc-button').click();
    await page.waitForTimeout(300);
    const toggled=await page.evaluate(()=>({hidden:document.getElementById('yche-reader-panel').hidden,expanded:document.querySelector('.yche-toc-button').getAttribute('aria-expanded'),chart:window.KVC_CHARTS.topology.getWidth(),dom:document.getElementById('topology').getBoundingClientRect().width,allCharts:Object.entries(window.KVC_CHARTS).map(([id,c])=>({id,width:c.getWidth(),dom:c.getDom().getBoundingClientRect().width}))}));
    check(state,'Chapter toggle changes panel state',toggled.hidden!==initial.panelHidden&&toggled.expanded===String(!toggled.hidden),toggled);
    check(state,'Charts resize after chapter panel change',toggled.allCharts.length===7&&toggled.allCharts.every(c=>Math.abs(c.width-c.dom)<2),toggled);
    if(toggled.hidden)await page.locator('.yche-toc-button').click();
    await page.locator('.yche-chapters a[href="#resources"]').click();
    await page.waitForTimeout(700);
    const navigation=await page.evaluate(()=>({hash:location.hash,active:document.querySelector('.yche-chapters a[aria-current="location"]')?.getAttribute('href'),headingTop:document.getElementById('resources').getBoundingClientRect().top,headerBottom:document.querySelector('.yche-fixed-header').getBoundingClientRect().bottom,panelHidden:document.getElementById('yche-reader-panel').hidden}));
    check(state,'Chapter link scrolls and highlights current section',navigation.hash==='#resources'&&navigation.active==='#resources'&&navigation.headingTop>=navigation.headerBottom-2&&navigation.headingTop<navigation.headerBottom+100,navigation);
    check(state,'Mobile panel closes after chapter selection',width>800||navigation.panelHidden,navigation);
    await screenshot('resources');
    await page.locator('#phases').scrollIntoViewIfNeeded();await page.waitForTimeout(400);await screenshot('phases');
    await page.locator('#phases').screenshot({path:path.join(out,`${width}-phases-chart.png`)});
    await page.evaluate(()=>window.KVC_CHARTS.phases.dispatchAction({type:'showTip',seriesIndex:0,dataIndex:0}));await page.waitForTimeout(150);
    const phaseTooltip=await page.locator('#phases').innerText();check(state,'Phase tooltip exposes measured duration and scope',phaseTooltip.includes(' 秒 · ')&&phaseTooltip.includes(initial.phases.expected[0].scope),{text:phaseTooltip});
    const phaseTooltipBounds=await page.locator('#phases').evaluate(e=>{const tip=[...e.children].find(c=>c.style.position==='absolute'&&c.style.zIndex==='9999999');const r=tip?.getBoundingClientRect();return {left:r?.left,right:r?.right,docWidth:document.documentElement.scrollWidth,viewport:innerWidth,whiteSpace:tip?getComputedStyle(tip).whiteSpace:null};});check(state,'Phase tooltip remains readable inside viewport',phaseTooltipBounds.left>=-1&&phaseTooltipBounds.right<=width+1&&phaseTooltipBounds.docWidth<=width+1,phaseTooltipBounds);
    await page.evaluate(()=>window.KVC_CHARTS.phases.dispatchAction({type:'hideTip'}));
    await page.locator('#operation').selectOption('put');
    await page.locator('#size').selectOption('1024');
    await page.locator('#metric').selectOption('qps');
    const put=await page.evaluate(()=>({rows:document.querySelectorAll('#results tr').length,expected:window.KVC_DATA.rows.filter(r=>r.operation==='put'&&r.size===1024).length,scatter:window.KVC_CHARTS.throughput.getOption().series.filter(s=>s.type==='scatter').reduce((n,s)=>n+s.data.length,0),bars:window.KVC_CHARTS.throughput.getOption().series.filter(s=>s.type==='bar').flatMap(s=>s.data).filter(v=>v!=null).length,note:document.getElementById('selection-note').textContent}));
    check(state,'PUT filters show only actual matching measurements',put.rows===put.expected&&put.scatter===put.expected&&put.bars===0,put);
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
    await page.locator('details').evaluate(e=>e.open=true);
    const links=await page.locator('#results a').evaluateAll(a=>a.map(e=>e.href));
    for (const url of links) {
      const response=await page.request.get(url);const raw=await response.json();
      result.links.push({viewport:width,kind:'raw-result',url,status:response.status(),valid:raw.valid});
      check(state,'Raw JSON link returns actual valid result',response.ok()&&raw.valid===true,{url,status:response.status()});
    }
    const [download]=await Promise.all([page.waitForEvent('download'),page.locator('a[href="summary.csv"][download]').click()]);
    const csvFile=path.join(out,`${width}-summary.csv`);await download.saveAs(csvFile);
    const csv=await fs.readFile(csvFile,'utf8');check(state,'CSV download returns all eight rows',csv.trim().split('\n').length===9,{suggestedFilename:download.suggestedFilename(),rows:csv.trim().split('\n').length-1});
    for(const name of ['data.json','reproduce.zip','SHA256SUMS','docs/build.md','docs/test-design.md','docs/validation.md','docs/script-design.md','timings.json','timings.csv','docs/timing.md']) {
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
