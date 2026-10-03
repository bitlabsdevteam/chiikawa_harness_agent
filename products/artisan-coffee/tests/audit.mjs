import {connect,navigate,pause} from './cdp.mjs';
// Non-destructive diagnostics; never overwrite the archived initial audit.
import {writeFile,readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
console.log('[audit] Connecting for the non-destructive diagnostic pass…');
const client = await connect();
const report = [];
try {
 for (const width of [360,768,1280]) {
  console.log(`[audit] Measuring ${width}px…`);
  await client.send('Emulation.setDeviceMetricsOverride',{width,height:900,deviceScaleFactor:1,mobile:false});
  await navigate(client);
  await client.evaluate(`document.documentElement.dataset.theme='light'`);
  await pause(100);
  report.push(await client.evaluate(`(() => {
   const visible = el => el.checkVisibility() && !el.closest('.sr-only,svg,[hidden]');
   const words = text => (text.match(/[\\p{L}\\p{N}]+(?:[’'-][\\p{L}\\p{N}]+)*/gu)||[]).length;
   const text = [...document.querySelectorAll('p')].filter(visible).map(el=>el.innerText).join(' ');
   const controls = [...document.querySelectorAll('button,a,select,summary')].filter(visible);
   return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,sections:document.querySelectorAll('main > section').length,paragraphWords:words(text),svgs:document.querySelectorAll('svg').length,visibleSVGs:[...document.querySelectorAll('svg')].filter(el=>el.checkVisibility()).length,smallControls:controls.filter(el=>{let r=el.getBoundingClientRect();return el.tagName==='BUTTON' && (r.width<44 || r.height<44)}).map(el=>({name:el.getAttribute('aria-label')||el.textContent.trim(),width:el.offsetWidth,height:el.offsetHeight})),overflow:[...document.querySelectorAll('body *')].filter(el=>el.checkVisibility() && !el.closest('svg,.sr-only') && el.getBoundingClientRect().right > innerWidth+1).map(el=>el.tagName+'.'+el.className),heroTitle:document.querySelector('h1').getBoundingClientRect().toJSON()};
  })()`));
 }
 await writeFile('.verification/latest-audit.json',JSON.stringify({sourceHash:createHash('sha256').update(await readFile('index.html')).digest('hex'),layouts:report},null,2));
 console.log(JSON.stringify(report,null,2));
} finally { await client.close(); }
