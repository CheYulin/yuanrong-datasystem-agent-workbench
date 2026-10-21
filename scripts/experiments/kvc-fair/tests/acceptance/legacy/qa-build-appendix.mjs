import fs from 'node:fs/promises';
import path from 'node:path';
import {chromium} from '/mnt/c/Users/T14S/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
const out=process.argv[2],started=process.hrtime.bigint(),result={checks:[],errors:[],views:[]};
await fs.mkdir(out,{recursive:true});
const check=(name,passed,detail={})=>result.checks.push({name,passed:!!passed,...detail});
const browser=await chromium.launch({executablePath:'/home/t14s/.cache/puppeteer/chrome/linux-148.0.7778.97/chrome-linux64/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try {
 for(const report of ['kvc-tcp-20261020','kvc-batch-tcp-20261020']) {
  for(const width of [1440,390]) {
   const context=await browser.newContext({viewport:{width,height:1000}}),page=await context.newPage(),errors=[];
   page.on('pageerror',e=>errors.push(String(e)));
   const url=`https://yche.me/perf/${report}/index.html`,response=await page.goto(url,{waitUntil:'networkidle',timeout:45000});
   const key=`${report}-${width}`;check(key+' HTTP',response.status()===200);
   await page.waitForFunction(()=>window.echarts&&[...document.querySelectorAll('.chart')].every(x=>!!echarts.getInstanceByDom(x)),{timeout:20000});
   const evidence=await page.evaluate(()=>{
    const a=document.querySelector('#build-appendix'),body=a.innerText;
    return {tags:['v0.9.2.rc44','v0.3.13.post1','v0.5.6'].every(x=>body.includes(x)),shas:['2ff37e2f7c5fb45f3feea7c3fb969b8f84b920d6','719735896c86b56fabec6cf3e825fb2ea640597a','3e11e10812b448142c6cdb177a3058652bd826fa'].every(x=>body.includes(x)),tables:a.querySelectorAll('table').length,commands:a.querySelectorAll('pre').length,profile:body.includes('旧单 key TCP')&&body.includes('rocksdb_write_mode=none')&&body.includes('--disable-observability'),overflow:document.documentElement.scrollWidth>innerWidth+1,charts:[...document.querySelectorAll('.chart')].map(x=>({id:x.id,series:echarts.getInstanceByDom(x)?.getOption()?.series?.length||0,graphic:echarts.getInstanceByDom(x)?.getOption()?.graphic?.length||0})),links:[...a.querySelectorAll('a[href]')].map(a=>a.href),navs:[...document.querySelectorAll('nav a')].filter(a=>a.textContent.includes('附录')).map(a=>({text:a.textContent,href:a.getAttribute('href')}))};
   });
   check(key+' exact version identities',evidence.tags&&evidence.shas);
   check(key+' build and configuration content',evidence.tables===3&&evidence.commands===3&&evidence.profile);
   check(key+' charts retained',evidence.charts.length>0&&evidence.charts.every(x=>x.series>0||x.id==='topology'&&x.graphic>0),{charts:evidence.charts});
   check(key+' no document overflow',!evidence.overflow);
   if(width===390)check(key+' mobile tags remain intact and tables scroll',await page.locator('#appendix-version-table').evaluate(t=>getComputedStyle(t.rows[1].cells[1]).whiteSpace==='nowrap'&&t.parentElement.scrollWidth>t.parentElement.clientWidth));
   check(key+' appendix navigation exists',evidence.navs.length>0,{navs:evidence.navs});
   // The shared reader has a visible replacement mobile menu.
   if(width===390){
    const toggle=page.locator('.yche-toc-button');
    if(await toggle.isVisible()){
     await toggle.click();const panel=page.locator('#'+await toggle.getAttribute('aria-controls'));
     const link=panel.locator('a').filter({hasText:'附录 A'}).first();
     await link.click();check(key+' mobile directory navigates',await toggle.getAttribute('aria-expanded')==='false'&&await page.locator('#build-appendix').evaluate(x=>x.getBoundingClientRect().top<innerHeight));
    }else{await page.locator('a[href="#build-appendix"]').first().click();check(key+' mobile shortcut navigates',page.url().endsWith('#build-appendix'));}
   } else {
    const nav=page.locator('nav a').filter({hasText:'附录 A'});let clicked=false;
    for(let i=0;i<await nav.count();i++)if(await nav.nth(i).isVisible()){await nav.nth(i).click();clicked=true;break;}
    check(key+' desktop directory navigates',clicked&&await page.locator('#build-appendix').evaluate(x=>x.getBoundingClientRect().top<innerHeight));
   }
   await page.screenshot({path:path.join(out,key+'-versions.png')});
   for(const summary of ['Mooncake：Release','LMCache：CPU'])await page.locator('#build-appendix summary').filter({hasText:summary}).click();
   check(key+' build commands expand',await page.locator('#build-appendix details[open]').count()===3);
   await page.locator('#appendix-runtime').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,key+'-runtime.png')});
   check(key+' no runtime errors',errors.length===0,{errors});
   if(width===1440){for(const asset of [...new Set(evidence.links)]){const r=await context.request.head(asset,{timeout:30000});check(key+' asset '+asset,r.status()===200,{status:r.status()});await r.dispose();}}
   result.views.push({report,width,evidence});await context.close();
  }
 }
}catch(e){result.errors.push(String(e.stack||e));}finally{await browser.close();result.duration_s=Number(process.hrtime.bigint()-started)/1e9;result.passed=result.errors.length===0&&result.checks.every(x=>x.passed)&&result.views.length===4;await fs.writeFile(path.join(out,'qa.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({passed:result.passed,checks:result.checks.length,failures:result.checks.filter(x=>!x.passed),errors:result.errors,duration_s:result.duration_s}));process.exitCode=result.passed?0:1;}
