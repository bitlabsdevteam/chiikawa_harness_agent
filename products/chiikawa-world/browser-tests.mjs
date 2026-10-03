import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Real Chromium tests using Node's built-in WebSocket and the DevTools protocol.
// No package installation, external service, or access to a personal browser profile.
const root = dirname(fileURLToPath(import.meta.url));
const browserPath = [process.env.BROWSER,
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
  '/usr/bin/chromium', '/usr/bin/chromium-browser', '/usr/bin/google-chrome'
].filter(Boolean).find(existsSync);
assert.ok(browserPath, 'Set BROWSER to an installed Chromium-family browser.');
const files = new Map([
  ['/', ['index.html', 'text/html']], ['/index.html', ['index.html', 'text/html']],
  ['/style.css', ['style.css', 'text/css']], ['/app.js', ['app.js', 'text/javascript']],
  ['/favicon.svg', ['favicon.svg', 'image/svg+xml']]
]);
const server = createServer((request, response) => {
  const file = files.get(new URL(request.url, 'http://localhost').pathname);
  if (!file) { response.writeHead(404); response.end('Not found'); return; }
  response.writeHead(200, { 'Content-Type': `${file[1]}; charset=utf-8` });
  response.end(readFileSync(join(root, file[0])));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const profile = mkdtempSync(join(root, '.browser-test-'));
const browser = spawn(browserPath, ['--headless=new', '--no-first-run', '--no-default-browser-check',
  '--disable-extensions', '--disable-background-networking', '--remote-debugging-port=0',
  `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, description) {
  for (let i = 0; i < 100; i++) { if (await fn()) return; await pause(50); }
  throw new Error(`Timed out: ${description}`);
}
let socket, sequence = 0;
const pending = new Map();
const errors = [], requests = [], results = [];
function send(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
  return result.result.value;
}
async function test(name, run) { await run(); results.push(name); console.log(`PASS ${name}`); }
async function navigate() {
  await send('Page.navigate', { url: origin });
  await until(() => evaluate(`document.readyState === 'complete' && location.href === '${origin}/' && !document.querySelector('#new-wish').hidden`), 'site ready');
}
async function key(value) {
  const codes = { Escape: 27, Tab: 9, Enter: 13, ' ': 32 };
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: value, windowsVirtualKeyCode: codes[value] });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: value, windowsVirtualKeyCode: codes[value] });
}
async function screenshot(name) {
  const image = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  writeFileSync(join(root, 'verification', name), Buffer.from(image.data, 'base64'));
}
try {
  await until(() => existsSync(join(profile, 'DevToolsActivePort')), 'browser start');
  const port = readFileSync(join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0];
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`, { signal: AbortSignal.timeout(5000) })).json();
  socket = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  socket.onmessage = ({ data }) => {
    const message = JSON.parse(data);
    if (message.id) {
      const task = pending.get(message.id);
      if (!task) return;
      pending.delete(message.id); clearTimeout(task.timer);
      if (message.error) task.reject(new Error(JSON.stringify(message.error))); else task.resolve(message.result);
    } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails);
    else if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
  };
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable');
  await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await navigate();
  mkdirSync(join(root, 'verification'), { recursive: true });
  await test('All navigation and illustration references resolve; assets stay local', async () => {
    assert.equal(await evaluate(`document.title`), 'Chiikawa — A little world of happy');
    assert.deepEqual(await evaluate(`[...document.querySelectorAll('a[href^="#"], use')].filter(el => !document.getElementById(el.getAttribute('href').slice(1))).map(el => el.outerHTML)`), []);
    assert.equal(await evaluate(`document.querySelectorAll('h1').length`), 1);
    assert.ok(requests.every(url => url.startsWith(origin)), 'No external asset requests');
  });
  for (const width of [320, 360, 760, 768, 1024, 1440]) {
    await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false });
    await test(`${width}px responsive layout has no horizontal overflow or clipped primary content`, async () => {
      assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth`));
      assert.deepEqual(await evaluate(`[...document.querySelectorAll('h1,h2,.friend-card,.joy-task,.moment,.button')].filter(el=>{const r=el.getBoundingClientRect();return r.width && (r.left < -1 || r.right > innerWidth + 1);}).map(el=>el.className || el.tagName)`), []);
    });
    if (width === 360 || width === 1440) await screenshot(`home-${width}.png`);
  }
  await test('All three character profiles open; Escape closes and restores focus', async () => {
    for (const [id, name] of [['chiikawa', 'Chiikawa'], ['hachiware', 'Hachiware'], ['usagi', 'Usagi']]) {
      await evaluate(`document.querySelector('[data-profile="${id}"]').focus(); document.activeElement.click()`);
      assert.equal(await evaluate(`document.querySelector('#profile-dialog').open`), true);
      assert.equal(await evaluate(`document.querySelector('#profile-title').textContent`), name);
      assert.equal(await evaluate(`document.querySelector('#profile-art use').getAttribute('href')`), `#${id}`);
      await key('Escape');
      assert.equal(await evaluate(`document.querySelector('#profile-dialog').open`), false);
      assert.equal(await evaluate(`document.activeElement.dataset.profile`), id);
    }
  });
  await test('Modal makes background controls inert; Tab and both close buttons work', async () => {
    await evaluate(`document.querySelector('[data-profile]').click()`);
    await evaluate(`document.querySelector('.brand').focus()`);
    assert.equal(await evaluate(`!!document.activeElement.closest('dialog')`), true);
    for (let i = 0; i < 4; i++) {
      await key('Tab');
      // Native dialogs allow focus into browser chrome (reported as body),
      // but must never let Tab focus an underlying page control.
      assert.equal(await evaluate(`document.activeElement === document.body || !!document.activeElement.closest('dialog')`), true);
    }
    await evaluate(`document.querySelector('.dialog-done').click()`);
    assert.equal(await evaluate(`document.querySelector('dialog').open`), false);
    await evaluate(`document.querySelector('[data-profile]').click(); document.querySelector('.dialog-close').click()`);
    assert.equal(await evaluate(`document.querySelector('dialog').open`), false);
  });
  await test('Daily joys toggle by keyboard, persist on reload, and can be undone', async () => {
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 0);
    await evaluate(`document.querySelector('input[name="joy"]').focus()`);
    await key(' ');
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 1);
    await navigate();
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 1);
    await evaluate(`document.querySelectorAll('input[name="joy"]:not(:checked)').forEach(el=>el.click())`);
    assert.match(await evaluate(`document.querySelector('#joy-progress').textContent`), /^3 of 3/);
    assert.equal(await evaluate(`document.querySelectorAll('.progress-dots .complete').length`), 3);
    await evaluate(`document.querySelector('input[name="joy"]').click()`);
    assert.equal(await evaluate(`JSON.parse(localStorage.getItem('chiikawa-world:little-joys:v1')).completed.length`), 2);
  });
  await test('Old-day progress resets and malformed storage does not break controls', async () => {
    await evaluate(`localStorage.setItem('chiikawa-world:little-joys:v1',JSON.stringify({day:'2000-1-1',completed:['pause','treat','kindness']}))`);
    await navigate();
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 0);
    await evaluate(`localStorage.setItem('chiikawa-world:little-joys:v1','not json')`);
    await navigate();
    await evaluate(`document.querySelector('input[name="joy"]').click()`);
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 1);
  });
  await test('Unknown and duplicate saved task IDs are filtered', async () => {
    await evaluate(`(()=>{const d=new Date();localStorage.setItem('chiikawa-world:little-joys:v1',JSON.stringify({day:d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate(),completed:['pause','pause','unknown']}))})()`);
    await navigate();
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 1);
    assert.match(await evaluate(`document.querySelector('#joy-progress').textContent`), /^1 of 3/);
  });
  await test('Blocked browser storage leaves checklist usable with honest feedback', async () => {
    const script = await send('Page.addScriptToEvaluateOnNewDocument', { source: `Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Storage unavailable','SecurityError')}});` });
    await navigate();
    await evaluate(`document.querySelector('input[name="joy"]').click()`);
    assert.equal(await evaluate(`document.querySelectorAll('input:checked').length`), 1);
    assert.match(await evaluate(`document.querySelector('#storage-note').textContent`), /couldn’t save/);
    await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: script.identifier });
    await navigate();
  });
  await test('Reminder button changes the message without consecutive repeats', async () => {
    let last = await evaluate(`document.querySelector('#wish-title').textContent`);
    for (let i = 0; i < 15; i++) {
      await evaluate(`document.querySelector('#new-wish').click()`);
      const current = await evaluate(`document.querySelector('#wish-title').textContent`);
      assert.notEqual(current, last); last = current;
    }
  });
  await test('Mobile menu opens, closes after navigation, and supports Escape', async () => {
    await send('Emulation.setDeviceMetricsOverride', { width: 360, height: 800, deviceScaleFactor: 1, mobile: false });
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('#navigation')).display`), 'none');
    await evaluate(`document.querySelector('.menu-toggle').click()`);
    assert.equal(await evaluate(`document.querySelector('.menu-toggle').getAttribute('aria-expanded')`), 'true');
    assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth`));
    await evaluate(`document.querySelector('#navigation a').click()`);
    assert.equal(await evaluate(`location.hash`), '#friends');
    assert.equal(await evaluate(`document.querySelector('.menu-toggle').getAttribute('aria-expanded')`), 'false');
    await evaluate(`document.querySelector('.menu-toggle').focus();document.activeElement.click()`);
    await key('Escape');
    assert.equal(await evaluate(`document.querySelector('.menu-toggle').getAttribute('aria-expanded')`), 'false');
    assert.equal(await evaluate(`document.activeElement.className`), 'menu-toggle');
  });
  await test('Reduced-motion preference disables smooth scrolling', async () => {
    assert.equal(await evaluate(`getComputedStyle(document.documentElement).scrollBehavior`), 'auto');
  });
  await test('Without JavaScript, content and navigation remain available', async () => {
    await send('Emulation.setScriptExecutionDisabled', { value: true });
    await send('Page.navigate', { url: origin });
    await pause(400);
    assert.equal(await evaluate(`document.querySelectorAll('.friend-card').length`), 3);
    assert.notEqual(await evaluate(`getComputedStyle(document.querySelector('#navigation')).display`), 'none');
    assert.equal(await evaluate(`document.querySelector('#new-wish').hidden`), true);
    assert.equal(await evaluate(`document.querySelector('.noscript-note').textContent.includes('Enable JavaScript')`), true);
    await send('Emulation.setScriptExecutionDisabled', { value: false });
  });
  await test('No uncaught browser JavaScript errors', async () => { assert.deepEqual(errors, []); });
  writeFileSync(join(root, 'verification', 'results.json'), JSON.stringify({ passed: results.length, tests: results, viewports: [320, 360, 760, 768, 1024, 1440] }, null, 2) + '\n');
  console.log(`\n${results.length} browser checks passed.`);
} finally {
  if (socket) socket.close();
  for (const task of pending.values()) clearTimeout(task.timer);
  if (browser.exitCode === null) {
    const exited = new Promise(resolve => browser.once('exit', resolve));
    browser.kill('SIGTERM');
    await Promise.race([exited, pause(3000)]);
    if (browser.exitCode === null && browser.signalCode === null) { browser.kill('SIGKILL'); await exited; }
  }
  await new Promise(resolve => server.close(resolve));
  rmSync(profile, { recursive: true, force: true });
}
