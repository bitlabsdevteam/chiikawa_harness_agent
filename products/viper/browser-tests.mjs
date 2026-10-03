import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync, existsSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

// Standard-library CDP verification. No browser package, install, or external service.
const candidates = [process.env.BROWSER, '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].filter(Boolean);
const binary = candidates.find(existsSync);
if (!binary) throw new Error('Set BROWSER to an installed Chromium-family browser executable. No packages are installed by this test.');
const html = readFileSync(new URL('./index.html', import.meta.url));
const server = createServer((req,res) => {
  if (req.url === '/' || req.url === '/index.html') {res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});res.end(html);}
  else {res.writeHead(204);res.end();}
});
await new Promise(done => server.listen(0,'127.0.0.1',done));
const origin = `http://127.0.0.1:${server.address().port}`;
const profile = resolve(`.viper-browser-${process.pid}`);
mkdirSync(profile);mkdirSync('verification',{recursive:true});
const browser = spawn(binary,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-extensions','--disable-background-networking','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'],{stdio:'ignore'});
const sleep = ms => new Promise(done=>setTimeout(done,ms));
async function until(run,description) {
  for(let i=0;i<100;i++){try{const value=await run();if(value)return value;}catch(_){}await sleep(50);}
  throw new Error(`Timed out: ${description}`);
}
let ws, next=0, passed=0;
const pending=new Map(), errors=[], report=[], network=[];
function send(method,params={}) {
  const id=++next;
  return new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{pending.delete(id);reject(new Error(`CDP timeout: ${method}`));},10000);
    pending.set(id,{resolve:value=>{clearTimeout(timeout);resolve(value);},reject});
    ws.send(JSON.stringify({id,method,params}));
  });
}
async function evaluate(expression) {
  const result=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if(result.exceptionDetails)throw new Error(result.exceptionDetails.exception?.description||result.exceptionDetails.text);
  return result.result.value;
}
async function test(name,run) {await run();passed++;console.log(`PASS ${name}`);report.push(name);}
async function key(key,code=key) {
  const windowsVirtualKeyCode = ({Enter:13,Tab:9,' ':32,ArrowLeft:37,ArrowUp:38,ArrowRight:39,ArrowDown:40})[key] || (key.length===1 ? key.toUpperCase().charCodeAt(0) : 0);
  await send('Input.dispatchKeyEvent',{type:'keyDown',key,code,windowsVirtualKeyCode,...(key==='Enter'?{text:'\r',unmodifiedText:'\r'}:{})});
  await send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode});
}
async function screenshot(name) {
  const image=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  writeFileSync(`verification/${name}.png`,Buffer.from(image.data,'base64'));
}
try {
  const port=await until(()=>existsSync(`${profile}/DevToolsActivePort`)&&readFileSync(`${profile}/DevToolsActivePort`,'utf8').split('\n')[0],'browser debugging port');
  const targets=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws=new WebSocket(targets.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((done,fail)=>{ws.addEventListener('open',done,{once:true});ws.addEventListener('error',fail,{once:true});});
  ws.addEventListener('message',event=>{
    const message=JSON.parse(event.data);
    if(message.id){const handler=pending.get(message.id);pending.delete(message.id);if(handler)message.error?handler.reject(new Error(JSON.stringify(message.error))):handler.resolve(message.result);}
    else if(message.method==='Runtime.exceptionThrown')errors.push(message.params.exceptionDetails);
    else if(message.method==='Network.requestWillBeSent')network.push(message.params.request.url);
  });
  await send('Page.enable');await send('Runtime.enable');await send('Network.enable');
  await send('Page.navigate',{url:origin});
  await until(()=>evaluate(`document.title==='Viper — One more bite.' && typeof game !== 'undefined'`),'page load');
  await test('real requestAnimationFrame loop stays ready until Start',async()=>{
    const before=await evaluate('JSON.stringify(game.snake)');await sleep(400);
    assert.equal(await evaluate('game.state'),'ready');assert.equal(await evaluate('JSON.stringify(game.snake)'),before);
    assert.equal(await evaluate('document.querySelectorAll("[data-direction]:disabled").length'),4);
  });
  const layouts=[];
  for(const [width,height,dpr] of [[360,800,2],[768,1024,2],[1280,960,1]]) {
    await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:dpr,mobile:false});await sleep(150);
    await test(`${width}px layout, visible content, density and controls`,async()=>{
      const data=await evaluate(`(()=>{
        const visible=el=>{for(let p=el;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||s.visibility==='hidden')return false;}return el.getClientRects().length>0;};
        const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let words=[];while(walker.nextNode()){const n=walker.currentNode,p=n.parentElement;if(!p.closest('script,style,svg,noscript,.sr-only,[hidden]')&&visible(p))words.push(n.textContent);}
        const buttons=[...document.querySelectorAll('button')].filter(visible).map(b=>({name:b.getAttribute('aria-label')||b.textContent,width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}));
        const board=document.getElementById('board');const mascot=document.querySelector('.mascot').getBoundingClientRect(),intro=document.querySelector('.intro').getBoundingClientRect();
        return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,sections:document.querySelectorAll('main section').length,svgs:[...document.querySelectorAll('svg')].filter(visible).length,words:words.join(' ').match(/\\b[\\w’'-]+\\b/g).length,buttons,board:board.getBoundingClientRect().width,backing:board.width,headingColor:getComputedStyle(document.getElementById('overlay-title')).color,scoreSize:parseFloat(getComputedStyle(document.querySelector('.stat span')).fontSize),footSize:parseFloat(getComputedStyle(document.querySelector('.screen-foot')).fontSize),noteSize:parseFloat(getComputedStyle(document.querySelector('.storage-note')).fontSize),mascotOverlap:!(mascot.right<=intro.left||mascot.left>=intro.right||mascot.bottom<=intro.top||mascot.top>=intro.bottom)};
      })()`);
      assert.ok(data.scrollWidth<=width,`overflow: ${data.scrollWidth} > ${width}`);assert.ok(data.sections>=9);assert.equal(data.svgs,4);assert.ok(data.words>1400);
      data.proseWords=await evaluate(`(()=>{const nodes=[...document.querySelectorAll('main p,main li,main figcaption')].filter(p=>!p.closest('.sr-only,noscript,[hidden]')&&p.getClientRects().length&&getComputedStyle(p).display!=='none');return nodes.map(p=>p.textContent).join(' ').match(/\\b[\\w’'-]+\\b/g).length;})()`);
      assert.ok(data.proseWords>1400);
      assert.ok(data.buttons.every(b=>b.width>=44&&b.height>=44),JSON.stringify(data.buttons));
      assert.equal(data.backing,Math.round(data.board*dpr));assert.equal(data.headingColor,'rgb(255, 248, 232)');
      assert.ok(data.scoreSize>=11&&data.footSize>=10&&data.noteSize>=11);assert.equal(data.mascotOverlap,false);
      layouts.push(data);
    });
    await screenshot(`ready-${width}`);
  }
  await test('overlay heading contrast stays above 4.5:1, even over the lightest food pixel',async()=>{
    const colors=await evaluate(`({text:getComputedStyle(document.getElementById('overlay-title')).color,background:getComputedStyle(document.getElementById('overlay')).backgroundColor})`);
    const rgb=s=>s.match(/[0-9.]+/g).map(Number);
    const foreground=rgb(colors.text),bg=rgb(colors.background),alpha=bg[3]??1;
    const composite=bg.slice(0,3).map((c,i)=>c*alpha+foreground[i]*(1-alpha));
    const luminance=cs=>cs.slice(0,3).map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;}).reduce((a,c,i)=>a+c*[.2126,.7152,.0722][i],0);
    const contrast=(luminance(foreground)+.05)/(luminance(composite)+.05);
    assert.ok(contrast>=4.5,`contrast ${contrast}`);report.push(`Measured worst-case overlay heading contrast: ${contrast.toFixed(2)}:1`);
  });
  await test('real animation frames advance on Start, and pause freezes',async()=>{
    await evaluate(`document.getElementById('start').click()`);const head=await evaluate('game.snake[0].x');
    await sleep(230);assert.ok(await evaluate(`game.snake[0].x>${head}`));
    await key('p','KeyP');assert.equal(await evaluate('game.state'),'paused');assert.equal(await evaluate('document.activeElement.id'),'start');
    const before=await evaluate('JSON.stringify(game.snake)');await sleep(220);assert.equal(await evaluate('JSON.stringify(game.snake)'),before);
    await screenshot('paused-1280');
  });
  // Swap only the scheduling clock on a fresh load, keeping production frame() and engine intact.
  const clock=await send('Page.addScriptToEvaluateOnNewDocument',{source:`window.__callbacks=[];window.__time=0;window.requestAnimationFrame=callback=>{window.__callbacks.push(callback);return window.__callbacks.length;};window.__advance=ms=>{window.__time+=ms;const callbacks=window.__callbacks.splice(0);callbacks.forEach(cb=>cb(window.__time));};`});
  await send('Page.reload');await until(()=>evaluate(`typeof __advance==='function' && typeof game!=='undefined'`),'controlled clock load');
  await test('Start announces coordinates; a plain step is silent and does not rewrite score',async()=>{
    await evaluate(`document.getElementById('start').click();window.__scoreNode=document.getElementById('score').firstChild;__advance(0)`);
    assert.match(await evaluate(`document.getElementById('announcer').textContent`),/Head column.*Food column/);
    const message=await evaluate(`document.getElementById('announcer').textContent`);
    await evaluate('__advance(165)');assert.equal(await evaluate('game.snake[0].x'),7);
    assert.equal(await evaluate(`document.getElementById('announcer').textContent`),message);
    assert.equal(await evaluate(`document.getElementById('score').firstChild===__scoreNode`),true);
    assert.equal(await evaluate(`[...document.querySelectorAll('output')].every(o=>o.getAttribute('aria-live')==='off')`),true);
    assert.equal(await evaluate(`document.getElementById('state-label').textContent.includes('RUNNING')`),true);
  });
  await test('keyboard arrows/WASD, reverse rejection and double-turn lock',async()=>{
    await key('ArrowLeft');assert.equal(await evaluate('game.pending'),null);
    await key('w','KeyW');await key('a','KeyA');assert.equal(await evaluate('game.pending'),'up');
    await evaluate('__advance(165)');assert.equal(await evaluate('game.direction'),'up');
    await key('a','KeyA');await evaluate('__advance(165)');assert.equal(await evaluate('game.direction'),'left');
  });
  await test('keyboard controls are scoped to the cabinet, not guide navigation',async()=>{
    const outside=await evaluate(`(()=>{const a=document.querySelector('nav a');a.focus();const e=new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true});a.dispatchEvent(e);return e.defaultPrevented;})()`);
    assert.equal(outside,false);assert.equal(await evaluate('game.pending'),null);
    const inside=await evaluate(`(()=>{board.focus();const e=new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true,cancelable:true});board.dispatchEvent(e);return e.defaultPrevented;})()`);
    assert.equal(inside,true);assert.equal(await evaluate('game.pending'),'down');
  });
  await test('pause clears queued turns, disables direction pad and moves focus visibly',async()=>{
    await key(' ','Space');assert.equal(await evaluate('game.state'),'paused');assert.equal(await evaluate('game.pending'),null);
    assert.equal(await evaluate(`document.querySelectorAll('[data-direction]:disabled').length`),4);
    assert.equal(await evaluate('document.activeElement.id'),'start');
    assert.match(await evaluate(`document.getElementById('announcer').textContent`),/Paused.*Head column.*Food column/);
    await evaluate(`document.getElementById('start').click();__advance(0)`);assert.equal(await evaluate('document.activeElement.id'),'board');
    const before=await evaluate('JSON.stringify(game.snake)');await evaluate('__advance(164)');assert.equal(await evaluate('JSON.stringify(game.snake)'),before);
    await evaluate('__advance(1)');assert.notEqual(await evaluate('JSON.stringify(game.snake)'),before);
  });
  await test('five real foods change pace and persist the best before game over',async()=>{
    await evaluate(`game.reset();game.start();resetClock();syncUI();__advance(0);for(let i=0;i<5;i++){game.food={x:game.snake[0].x+1,y:game.snake[0].y};__advance(165);}`);
    assert.equal(await evaluate('game.score'),5);assert.equal(await evaluate('game.pace'),2);
    assert.equal(await evaluate(`localStorage.getItem('viper.best.v1')`),'5');assert.equal(await evaluate(`document.getElementById('best').textContent`),'005');
    assert.match(await evaluate(`document.getElementById('announcer').textContent`),/Score 5. Pace 2.*Food column/);
  });
  await test('short delays move once, long stalls pause without catch-up',async()=>{
    await evaluate('game.reset();game.start();resetClock();syncUI();__advance(0)');
    await evaluate('__advance(490)');assert.equal(await evaluate('game.snake[0].x'),7);
    await evaluate('__advance(501)');assert.equal(await evaluate('game.state'),'paused');assert.equal(await evaluate('game.reason'),'stall');assert.equal(await evaluate('game.snake[0].x'),7);
  });
  await test('visibility loss and window blur pause; returning does not resume',async()=>{
    await evaluate(`document.getElementById('start').click();game.turn('up');Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));`);
    assert.equal(await evaluate('game.state'),'paused');assert.equal(await evaluate('game.pending'),null);
    await evaluate(`Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));__advance(1000)`);
    assert.equal(await evaluate('game.state'),'paused');
    await evaluate(`document.getElementById('start').click();window.dispatchEvent(new Event('blur'))`);
    assert.equal(await evaluate('game.state'),'paused');assert.equal(await evaluate('document.activeElement.id'),'start');
    await evaluate(`delete document.hidden`);
  });
  await test('canvas resize at DPR 2 preserves a paused round',async()=>{
    const before=await evaluate('JSON.stringify([game.snake,game.food,game.score,game.state])');
    await send('Emulation.setDeviceMetricsOverride',{width:768,height:1024,deviceScaleFactor:2,mobile:false});await sleep(100);
    assert.equal(await evaluate('JSON.stringify([game.snake,game.food,game.score,game.state])'),before);
    assert.equal(await evaluate('canvas.width===Math.round(canvas.getBoundingClientRect().width*2)'),true);
  });
  await test('touch event steers exactly once; direction button retains keyboard focus',async()=>{
    await evaluate(`game.reset();syncUI();document.getElementById('start').click();__advance(0);document.querySelector('.up').scrollIntoView({block:'center',behavior:'instant'})`);
    await send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
    await evaluate(`document.documentElement.style.scrollBehavior='auto';window.scrollTo(0,scrollY+document.querySelector('.up').getBoundingClientRect().top-innerHeight/2)`);await sleep(150);
    const point=await evaluate(`(()=>{const r=document.querySelector('.up').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
    await send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{...point,radiusX:2,radiusY:2,force:1}]});
    await send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await sleep(80);
    assert.equal(await evaluate('game.pending'),'up',JSON.stringify(await evaluate(`({state:game.state,reason:game.reason,hidden:document.hidden,disabled:document.querySelector('.up').disabled,focus:document.activeElement.outerHTML.slice(0,150),point:document.elementFromPoint(${point.x},${point.y})?.outerHTML.slice(0,200)})`)));await evaluate('__advance(165)');assert.equal(await evaluate('game.direction'),'up');
    await evaluate(`document.querySelector('.left').focus()`);await key('Enter');
    assert.equal(await evaluate('game.pending'),'left',JSON.stringify(await evaluate(`({state:game.state,reason:game.reason,focus:document.activeElement.outerHTML.slice(0,200),disabled:document.querySelector('.left').disabled})`)));assert.equal(await evaluate(`document.activeElement.classList.contains('left')`),true);
    await send('Emulation.setTouchEmulationEnabled',{enabled:false});
  });
  await test('wall ending and Restart expose explicit actions without automatic play',async()=>{
    await evaluate(`game.pending=null;game.snake=[{x:0,y:0},{x:1,y:0},{x:2,y:0}];game.direction='left';__advance(165)`);
    assert.equal(await evaluate('game.state'),'lost');assert.equal(await evaluate(`document.querySelectorAll('[data-direction]:disabled').length`),4);
    assert.equal(await evaluate(`document.getElementById('overlay-title').textContent`),'Hello, wall.');
    await evaluate(`document.getElementById('start').click()`);assert.equal(await evaluate('game.state'),'running');assert.equal(await evaluate('game.score'),0);
    await evaluate(`document.getElementById('restart').click()`);assert.equal(await evaluate('game.state'),'ready');assert.equal(await evaluate('best'),5);assert.equal(await evaluate('document.activeElement.id'),'start');
  });
  await test('best survives reload; malformed storage is safely ignored',async()=>{
    await send('Page.reload');await until(()=>evaluate(`document.getElementById('best')?.textContent==='005'`),'persisted best');
    assert.equal(await evaluate('game.state'),'ready');
    await evaluate(`localStorage.setItem('viper.best.v1','NaN<script>')`);await send('Page.reload');await until(()=>evaluate(`typeof best!=='undefined'&&best===0`),'invalid stored score');
    assert.equal(await evaluate(`document.getElementById('best').textContent`),'000');
  });
  const failure=await send('Page.addScriptToEvaluateOnNewDocument',{source:`Storage.prototype.getItem=function(){throw new DOMException('Blocked','SecurityError');};Storage.prototype.setItem=function(){throw new DOMException('Full','QuotaExceededError');};`});
  await send('Page.reload');await until(()=>evaluate(`document.getElementById('storage-note')?.textContent.includes('UNAVAILABLE')`),'storage denied');
  await test('storage read/write failures keep play usable and retain the visit-only best',async()=>{
    await evaluate(`document.getElementById('start').click();__advance(0);game.food={x:7,y:10};__advance(165)`);
    assert.equal(await evaluate('game.state'),'running');assert.equal(await evaluate('game.score'),1);assert.equal(await evaluate('best'),1);
    assert.match(await evaluate(`document.getElementById('storage-note').textContent`),/THIS VISIT ONLY/);
    assert.equal(await evaluate(`document.getElementById('storage-note').getAttribute('role')`),'status');
    await evaluate(`document.getElementById('restart').click()`);assert.equal(await evaluate('best'),1);
  });
  await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:failure.identifier});
  await test('reduced motion, explicit focus indicator and accessible names',async()=>{
    await send('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
    assert.equal(await evaluate(`getComputedStyle(document.documentElement).scrollBehavior`),'auto');
    await key('Tab');
    assert.equal(await evaluate(`getComputedStyle(document.activeElement).outlineStyle`),'solid');
    const tree=await send('Accessibility.getFullAXTree');
    for(const name of ['Viper game board','Turn up','Turn down','Turn left','Turn right','Start game ↗'])assert.ok(tree.nodes.some(n=>n.name?.value===name),`missing AX name: ${name}`);
    assert.equal(await evaluate(`document.querySelectorAll('img:not([alt])').length`),0);
    assert.equal(await evaluate(`(()=>{const ids=[...document.querySelectorAll('[id]')].map(e=>e.id);return new Set(ids).size===ids.length;})()`),true);
    assert.equal(await evaluate(`document.querySelectorAll('h1').length`),1);
    assert.equal(await evaluate(`[...document.querySelectorAll('a[href^="#"]')].every(a=>a.hash===''||document.getElementById(a.hash.slice(1)))`),true);
    assert.equal(await evaluate(`[...document.querySelectorAll('[aria-labelledby],[aria-describedby]')].every(e=>[e.getAttribute('aria-labelledby'),e.getAttribute('aria-describedby')].filter(Boolean).join(' ').split(/\\s+/).every(id=>document.getElementById(id)))`),true);
  });
  await send('Page.removeScriptToEvaluateOnNewDocument',{identifier:clock.identifier});
  await send('Page.reload');await until(()=>evaluate(`typeof __advance==='undefined' && typeof game!=='undefined'`),'real frame final load');
  await send('Emulation.setDeviceMetricsOverride',{width:1280,height:960,deviceScaleFactor:1,mobile:false});
  await evaluate(`scrollTo({top:0,behavior:'instant'})`);await sleep(100);
  await screenshot('final-1280');
  const metrics=await send('Page.getLayoutMetrics');
  const full=await send('Page.captureScreenshot',{format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1280,height:metrics.cssContentSize.height,scale:1}});
  writeFileSync('verification/full-page-1280.png',Buffer.from(full.data,'base64'));
  await test('no JavaScript exceptions or external page dependencies',async()=>{
    assert.equal(errors.length,0,JSON.stringify(errors));
    assert.ok(network.every(url=>url.startsWith(origin)||url.startsWith('data:')),network.join('\n'));
    assert.ok(!/<(?:script|link)[^>]+(?:src|href)\s*=\s*["']https?:/i.test(html.toString()));
  });
  await test('direct self-contained file opens, plays and persists its own best',async()=>{
    await send('Page.navigate',{url:pathToFileURL(resolve('index.html')).href});
    await until(()=>evaluate(`location.protocol==='file:' && typeof game!=='undefined'`),'local file load');
    assert.equal(await evaluate('game.state'),'ready');
    await evaluate(`document.getElementById('start').click();game.food={x:7,y:10}`);
    await until(()=>evaluate('game.score===1'),'local file food');
    await evaluate(`pauseGame()`);assert.equal(await evaluate(`localStorage.getItem('viper.best.v1')`),'1');
    await send('Page.reload');await until(()=>evaluate(`typeof best!=='undefined' && best===1 && game.state==='ready'`),'local file best reload');
    assert.equal(errors.length,0);
  });
  const result={browser:binary,passed,tests:report,layouts,exceptions:errors.length,externalRequests:network.filter(url=>!url.startsWith(origin)&&!url.startsWith('file:')),screenshots:['ready-360.png','ready-768.png','ready-1280.png','paused-1280.png','final-1280.png','full-page-1280.png']};
  writeFileSync('verification/results.json',JSON.stringify(result,null,2)+'\n');
  console.log(`\n${passed} browser checks passed. Evidence: verification/results.json`);
} finally {
  if(ws)ws.close();browser.kill('SIGTERM');
  await new Promise(done=>server.close(done));
  await sleep(350);rmSync(profile,{recursive:true,force:true});
}
