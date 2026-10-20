#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

const {chromium}=await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE||'/mnt/c/Users/T14S/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs').href);
const base=process.env.QA_URL||'http://127.0.0.1:18768/index.html';
const output=process.env.QA_OUT||'/tmp/kvc-batch-qa';
await fs.mkdir(output,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/home/t14s/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
const startedMono=process.hrtime.bigint();
const report={url:base,started:new Date().toISOString(),checks:[],viewports:[],errors:[]};
const check=(name,pass,details={})=>{report.checks.push({name,pass,...details});};
const near=(a,b)=>a===b||Number.isFinite(a)&&Number.isFinite(b)&&Math.abs(a-b)<=Math.max(1,Math.abs(b))*1e-10;
const median=values=>{const data=values.toSorted((a,b)=>a-b),n=data.length;return n%2?data[(n-1)/2]:(data[n/2-1]+data[n/2])/2;};
const products=['datasystem','mooncake','lmcache'],batches=[1,16,256,4096],operations=['put','upsert','get','exist_hit','exist_miss'];
try {
  for(const width of [1440,900,390]){
    const context=await browser.newContext({viewport:{width,height:width===390?844:1000}});
    const page=await context.newPage(),state={width,pageErrors:[],networkErrors:[],httpErrors:[]};report.viewports.push(state);
    page.on('pageerror',e=>state.pageErrors.push(String(e)));
    page.on('requestfailed',r=>state.networkErrors.push({url:r.url(),error:r.failure()?.errorText}));
    page.on('response',r=>{if(r.status()>=400)state.httpErrors.push({url:r.url(),status:r.status()});});
    await page.goto(base,{waitUntil:'networkidle'});
    await page.waitForFunction(()=>window.KVC_BATCH_DATA&&Object.keys(window.KVC_BATCH_CHARTS||{}).length===9);
    const data=await page.evaluate(()=>window.KVC_BATCH_DATA);
    state.rows=data.rows.length;
    const failedRows=data.rows.filter(r=>r.status==='failed'),validRows=data.rows.filter(r=>r.valid);
    check(`${width}: full observed matrix`,data.rows.length===180&&validRows.length+failedRows.length===150&&data.rows.filter(r=>r.status==='not_supported').length===30&&data.evidence_verified===true&&data.passed===(failedRows.length===0)&&data.rows.every(r=>['valid','failed','not_supported'].includes(r.status)),{counts:data.counts,passed:data.passed,evidence_verified:data.evidence_verified});
    check(`${width}: native failures remain explicit`,failedRows.every(r=>r.valid===false&&r.evidence_verified===true&&r.failures>0&&r.native_errors.length>0&&['get','exist_hit','exist_miss'].includes(r.operation))&&JSON.stringify(data.summary.measurement_failures||[])===JSON.stringify(failedRows.map(r=>r.identity)));
    const identities=data.rows.map(r=>r.identity);
    check(`${width}: unique identities`,new Set(identities).size===180);
    const epochs=data.epochs||[],epochIds=epochs.map(e=>e.identity);
    check(`${width}: all 36 isolated service lifetimes completed`,data.workload_protocol==='separate-read-and-fresh-put-v1'&&epochs.length===36&&new Set(epochIds).size===36&&new Set(epochs.map(e=>e.run_dir)).size===36&&epochs.every(e=>{const failed=failedRows.filter(r=>r.identity.startsWith(e.identity.split('/')[0]+'/')&&r.epoch===e.identity.split('/')[1]).map(r=>r.identity);return e.valid===(failed.length===0)&&(e.execution_complete===true||e.valid===true)&&JSON.stringify(e.measurement_failures||[])===JSON.stringify(failed);})&&JSON.stringify(data.summary.epochs_completed)===JSON.stringify(epochIds));
    const phaseOption=await page.evaluate(()=>window.KVC_BATCH_CHARTS.phases.getOption()),phaseEpochs=phaseOption.series[0].data.filter(p=>p.value[2]===4),phaseErrors=[];
    if(phaseEpochs.length!==epochs.length)phaseErrors.push('service lifetime point count');
    for(const point of phaseEpochs){const epoch=epochs.find(e=>point.row.name==='服务 epoch · '+e.identity);if(!epoch||!near(point.value[0],epoch.start_wall_ns/1e6)||!near(point.value[1],epoch.end_wall_ns/1e6)||!near(point.row.duration,epoch.duration_s))phaseErrors.push('service lifetime point');}
    check(`${width}: service lifetime timeline equals original epoch boundaries`,phaseErrors.length===0,{errors:phaseErrors});
    const dimensions=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth,headings:[...document.querySelectorAll('main h2')].map(e=>e.textContent),captions:[...document.querySelectorAll('figure')].map(f=>{const a=f.querySelector('figcaption'),b=f.querySelector('.chart'),x=a.getBoundingClientRect(),y=b.getBoundingClientRect();return{align:getComputedStyle(a).textAlign,below:x.top>=y.bottom-1,center:Math.abs(x.left+x.width/2-y.left-y.width/2),text:a.textContent};}),charts:Object.entries(window.KVC_BATCH_CHARTS).map(([id,c])=>({id,width:c.getWidth(),dom:c.getDom().getBoundingClientRect().width}))}));
    state.dimensions=dimensions;
    check(`${width}: no horizontal page overflow`,dimensions.scroll<=width+1,dimensions);
    check(`${width}: numbered sections and centered captions`,dimensions.headings.length===7&&dimensions.headings.every((h,i)=>h.startsWith(`${i+1}.`))&&dimensions.captions.length===9&&dimensions.captions.every(c=>c.align==='center'&&c.below&&c.center<2));
    check(`${width}: all charts sized`,dimensions.charts.every(c=>c.width>100&&Math.abs(c.width-c.dom)<2));
    await page.screenshot({path:path.join(output,`${width}-initial.png`),fullPage:false});
    for(const operation of operations){
      await page.selectOption('#operation',operation);
      for(const metric of operation.startsWith('exist')?['keys_s','batch_qps']:['keys_s','batch_qps','throughput_mib_s']){
        await page.selectOption('#metric',metric);
        const option=await page.evaluate(()=>window.KVC_BATCH_CHARTS.throughput.getOption());
        const errors=[];
        for(let p=0;p<products.length;p++){
          const product=products[p],bar=option.series[p*2],scatter=option.series[p*2+1];
          if(bar.type!=='bar'||scatter.type!=='scatter')errors.push('series types');
          for(let j=0;j<batches.length;j++){
            const rows=data.rows.filter(r=>r.product===product&&r.operation===operation&&r.batch===batches[j]&&r.valid&&Number.isFinite(r[metric]));
            const expected=rows.length===3?median(rows.map(r=>r[metric])):null;
            if(!near(bar.data[j],expected))errors.push({product,batch:batches[j],actual:bar.data[j],expected});
          }
          const rows=data.rows.filter(r=>r.product===product&&r.operation===operation&&r.valid&&Number.isFinite(r[metric]));
          if(scatter.data.length!==rows.length)errors.push('scatter count');
          for(const point of scatter.data){const row=rows.find(r=>r.identity===point.row.identity);if(!row||!near(point.value[0],batches.indexOf(row.batch))||!near(point.value[1],row[metric]))errors.push('scatter point');}
        }
        check(`${width}: ${operation}/${metric} chart equals raw observations`,errors.length===0,{errors});
      }
      for(const quantile of ['p50_us','p95_us','p99_us']){
        await page.selectOption('#quantile',quantile);
        const option=await page.evaluate(()=>window.KVC_BATCH_CHARTS.latency.getOption());
        const errors=[];
        for(let p=0;p<products.length;p++)for(let j=0;j<batches.length;j++){
          const rows=data.rows.filter(r=>r.product===products[p]&&r.operation===operation&&r.batch===batches[j]&&r.valid&&Number.isFinite(r[quantile]));
          const expected=rows.length===3?median(rows.map(r=>r[quantile])):null;
          if(!near(option.series[p*2].data[j],expected))errors.push({product:products[p],batch:batches[j]});
        }
        check(`${width}: ${operation}/${quantile} whole-batch latency`,errors.length===0,{errors});
      }
      for(const metric of ['total_cpu_cores','peak_charged_memory_gib','peak_anon_shmem_gib','total_write_mib_s','total_read_mib_s']){
        await page.selectOption('#resource-metric',metric);
        const options=await page.evaluate(()=>({bars:window.KVC_BATCH_CHARTS['resource-summary'].getOption(),cost:window.KVC_BATCH_CHARTS.cost.getOption()})),errors=[];
        for(let p=0;p<products.length;p++){
          const product=products[p],rows=data.rows.filter(r=>r.product===product&&r.operation===operation&&r.valid&&Number.isFinite(r[metric]));
          for(let j=0;j<batches.length;j++){
            const values=rows.filter(r=>r.batch===batches[j]).map(r=>r[metric]),expected=values.length===3?median(values):null;
            if(!near(options.bars.series[p*2].data[j],expected))errors.push({product,batch:batches[j],expected});
          }
          const scatter=options.cost.series[p];
          if(scatter.type!=='scatter'||scatter.data.length!==rows.length)errors.push('resource scatter count/type');
          for(const point of scatter.data){const row=rows.find(r=>r.identity===point.row.identity);if(!row||!near(point.value[0],row.keys_s)||!near(point.value[1],row[metric])||!near(point.symbolSize,8+Math.log2(row.batch)*1.2))errors.push('resource scatter value');}
        }
        check(`${width}: ${operation}/${metric} preserves observed resources and nulls`,errors.length===0,{errors});
      }
    }
    await page.selectOption('#operation','exist_hit');
    const row=data.rows.find(r=>r.valid&&r.batch===4096&&r.product==='lmcache'&&r.operation==='exist_hit');
    if(row){
      await page.selectOption('#timeline-run',row.identity);
      for(const metric of ['cpu','memory','io']){
        await page.selectOption('#timeline-metric',metric);
        const option=await page.evaluate(()=>window.KVC_BATCH_CHARTS.timeline.getOption()),errors=[];
        const samples=data.samples.filter(s=>s.mono_ns>=row.start_mono_ns&&s.mono_ns<=row.end_mono_ns);
        const definitions=metric==='cpu'?[['server','cpu_usec_per_s',1e6],['client','cpu_usec_per_s',1e6]]:metric==='io'?[['server','read_bytes_per_s',2**20],['client','read_bytes_per_s',2**20],['server','write_bytes_per_s',2**20],['client','write_bytes_per_s',2**20]]:[[null,'memory',2**30],[null,'anon',2**30]];
        if(option.series.length!==definitions.length||option.xAxis[0].type!=='value'||!near(option.xAxis[0].max,row.duration_s))errors.push('axes/series');
        definitions.forEach(([role,key,scale],i)=>{
          const points=samples.map(s=>{let value;if(role)value=Number.isFinite(s.prev_mono_ns)&&s.prev_mono_ns>=row.start_mono_ns&&Number.isFinite(s[role]?.[key])?s[role][key]/scale:null;else{const values=['server','client'].flatMap(r=>key==='anon'?[s[r]?.anon,s[r]?.shmem]:[s[r]?.memory]);value=values.every(Number.isFinite)?values.reduce((a,b)=>a+b,0)/scale:null;}return[(s.mono_ns-row.start_mono_ns)/1e9,value];});
          if(option.series[i].data.length!==points.length)errors.push('point count');
          points.forEach((p,j)=>{const a=option.series[i].data[j];if(!a||!near(a[0],p[0])||!near(a[1],p[1])){if(errors.length<10)errors.push({series:i,point:j,actual:a,expected:p});}});
        });
        check(`${width}: timeline ${metric} every point matches window counters`,errors.length===0,{points:samples.length,errors});
      }
    }
    await page.locator('#resources').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,`${width}-resources.png`),fullPage:false});
    await page.locator('#performance').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(output,`${width}-performance.png`),fullPage:false});
    if(width===390){const toggle=page.locator('#toc-toggle'),before=await toggle.getAttribute('aria-expanded');await toggle.click();const after=await toggle.getAttribute('aria-expanded');await toggle.click();check('mobile: navigation button changes and restores expanded state',before!==after&&await toggle.getAttribute('aria-expanded')===before);}
    check(`${width}: no JS exceptions`,state.pageErrors.length===0,{errors:state.pageErrors});
    if(width===1440&&!base.startsWith('file:')){
      const response=await page.request.get(new URL('data.json',base).href),downloaded=await response.json();
      check('downloaded data matches rendered payload',response.ok()&&JSON.stringify(downloaded)===JSON.stringify(data));
      const href=await page.locator('a[download][href^="reproduce.zip"]').getAttribute('href'),url=new URL(href,base),expected=url.searchParams.get('sha256');
      const zip=await page.request.get(url.href),bytes=await zip.body(),observed=createHash('sha256').update(bytes).digest('hex');
      const manifest=await (await page.request.get(new URL('SHA256SUMS',base).href)).text();
      const matches=manifest.split('\n').filter(line=>/  reproduce\.zip$/.test(line));
      check('versioned ZIP body SHA equals URL and unique manifest entry',zip.ok()&&/^[a-f0-9]{64}$/.test(expected||'')&&expected===observed&&matches.length===1&&matches[0].startsWith(observed+'  '),{expected,observed,bytes:bytes.length});
      const links=await page.locator('a[href]').evaluateAll(links=>links.map(a=>a.getAttribute('href')).filter(h=>h&&!h.startsWith('#')&&!h.startsWith('http')&&!h.startsWith('../')));
      for(const href of new Set(links)){const url=new URL(href,base),r=await page.request.head(url.href);check('relative artifact '+href,r.ok(),{status:r.status()});}
    }
    const optionalFailure=item=>{const url=new URL(item.url);return url.pathname==='/favicon.ico'||url.pathname==='/site-api/admin/pages'&&item.status===401;};
    check(`${width}: required requests succeeded`,state.networkErrors.every(optionalFailure)&&state.httpErrors.every(optionalFailure),{networkErrors:state.networkErrors,httpErrors:state.httpErrors});
    await context.close();
  }
}catch(error){report.errors.push(String(error.stack||error));}
finally{await browser.close();report.ended=new Date().toISOString();report.duration_s=Number(process.hrtime.bigint()-startedMono)/1e9;report.passed=report.errors.length===0&&report.checks.every(c=>c.pass);await fs.writeFile(path.join(output,'qa.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({passed:report.passed,checks:report.checks.length,failed:report.checks.filter(c=>!c.pass),errors:report.errors}));}
process.exitCode=report.passed?0:1;
