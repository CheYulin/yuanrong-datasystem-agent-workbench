import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';

const [report, output, playwright, executable] = process.argv.slice(2);
if (!report || !output || !playwright || !executable) throw new Error('Require report URL/path, new output directory, Playwright module and Chrome executable');
await fs.mkdir(output);
const {chromium} = await import(pathToFileURL(path.resolve(playwright)).href);
const browser = await chromium.launch({executablePath:executable, headless:true, args:['--no-sandbox','--disable-dev-shm-usage']});
const base = /^https?:/.test(report) ? new URL(report) : pathToFileURL(path.join(path.resolve(report), 'index.html'));
const cases=[];
try {
  for (const viewport of [{width:1440,height:1000},{width:390,height:844},{width:320,height:800}]) {
    const page=await browser.newPage({viewport});
    const errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    page.on('requestfailed',request=>errors.push(request.url()+': '+request.failure()?.errorText));
    await page.goto(base.href);
    await page.waitForFunction(()=>window.KVC_REPORT_READY===true);
    const reportState=await page.evaluate(()=>({
      overflow:document.documentElement.scrollWidth>innerWidth,
      charts:[...document.querySelectorAll('.chart')].filter(node=>echarts.getInstanceByDom(node)).length,
      captions:[...document.querySelectorAll('figcaption')].every(node=>getComputedStyle(node).textAlign==='center'),
      failures:document.querySelectorAll('#failureRows tr').length,
      expectedFailures:window.KVC_DATA.rows.filter(row=>row.status!=='valid').length,
      documents:window.KVC_DATA.documents,
      stepLinks:[...document.querySelectorAll('#stepRows a')].map(node=>node.getAttribute('href')),
      expectedStepLinks:(window.KVC_DATA.workflow?.steps||[]).flatMap(row=>Object.values(row.evidence||{})),
      actualTags:window.KVC_DATA.builds.map(row=>({product:row.product,tag:row.tag,sha:row.sha})),
      headings:[...document.querySelectorAll('main section>h1,main section>h2')].every(node=>/^\d+\./.test(node.textContent))
    }));
    const sameStepLinks=JSON.stringify([...reportState.stepLinks].sort())===JSON.stringify([...reportState.expectedStepLinks].sort());
    let stepTargets=true;
    if(viewport.width===1440) {
      for(const href of new Set(reportState.stepLinks)) {
        const target=new URL(href,base);
        if(target.protocol==='file:') {
          stepTargets=stepTargets&&(await fs.stat(fileURLToPath(target))).isFile();
        } else {
          const response=await page.request.get(target.href);
          stepTargets=stepTargets&&response.ok();
          await response.dispose();
        }
      }
    }
    let interaction=sameStepLinks&&stepTargets;
    for(const operation of ['put','upsert','get','exist_hit','exist_miss']) {
      await page.selectOption('#operation',operation);
      const valid=await page.evaluate(op=>{
        const options=echarts.getInstanceByDom(document.querySelector('#keys')).getOption();
        const labels={datasystem:'DataSystem',mooncake:'Mooncake',lmcache:'LMCache'};
        return window.KVC_DATA.aggregates.filter(row=>row.operation===op&&row.status!=='complete').every(row=>{
          const series=options.series.find(s=>s.name===labels[row.product]);
          return !series || series.data[[1,16,256,4096].indexOf(row.batch)]==null;
        });
      },operation);
      interaction=interaction&&valid;
    }
    if(viewport.width<850) {
      await page.click('#menu');
      interaction=interaction&&await page.locator('#navigation').evaluate(node=>node.classList.contains('open'));
      await page.locator('#navigation a[href="#resources"]').click();
      interaction=interaction&&!await page.locator('#navigation').evaluate(node=>node.classList.contains('open'));
    }
    await page.screenshot({path:path.join(output,'report-'+viewport.width+'.png')});
    const documents=[];
    for(const item of reportState.documents) {
      await page.goto(new URL(item.href,base).href);
      await page.waitForFunction(()=>window.docReady!==undefined);
      await page.evaluate(()=>window.docReady);
      const state=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,
        mermaidError:document.documentElement.dataset.mermaidError||null,
        figures:document.querySelectorAll('figure').length,svgs:document.querySelectorAll('.mermaid svg').length}));
      documents.push({href:item.href,...state});
    }
    const passed=!reportState.overflow&&reportState.charts===9&&reportState.captions&&reportState.headings&&
      reportState.failures===reportState.expectedFailures&&interaction&&documents.length===4&&
      documents.every(doc=>!doc.overflow&&!doc.mermaidError&&doc.svgs===doc.figures)&&errors.length===0;
    cases.push({viewport,passed,report:reportState,interaction,documents,errors});
    await page.close();
  }
} finally { await browser.close(); }
const result={passed:cases.every(row=>row.passed),scope:'Actual rendered report and four linked documents: desktop/mobile layout, ECharts interaction, native failure/N/A gaps, bound intermediate step links, Mermaid and JavaScript/resource loading',cases};
await fs.writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result));
if(!result.passed)process.exitCode=1;
