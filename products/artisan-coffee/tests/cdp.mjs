// Bounded, standard-library Chrome DevTools client. Each run owns its own tab.
export const siteURL = process.env.SITE_URL || 'http://127.0.0.1:8766';
export const debugURL = process.env.CDP_URL || 'http://127.0.0.1:9226';
export const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function connect(endpoint = debugURL) {
  const response = await fetch(`${endpoint}/json/new?about:blank`, {method:'PUT',signal:AbortSignal.timeout(5000)});
  if (!response.ok) throw new Error(`Chrome target creation failed: HTTP ${response.status}`);
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {
    const timer = setTimeout(() => { socket.close(); reject(new Error('Chrome connection timed out')); },5000);
    socket.onopen = () => { clearTimeout(timer); resolve(); };
    socket.onerror = () => { clearTimeout(timer); reject(new Error('Chrome connection failed')); };
  });
  let nextId = 0;
  const pending = new Map(), events = [];
  const rejectPending = () => {
    for (const task of pending.values()) { clearTimeout(task.timer); task.reject(new Error('Chrome connection closed')); }
    pending.clear();
  };
  socket.onclose = rejectPending;
  socket.onerror = rejectPending;
  socket.onmessage = ({data}) => {
    const message = JSON.parse(data);
    if (!message.id) { events.push(message); return; }
    const task = pending.get(message.id);
    if (task) {
      pending.delete(message.id); clearTimeout(task.timer);
      message.error ? task.reject(new Error(JSON.stringify(message.error))) : task.resolve(message.result);
    }
  };
  const send = (method, params = {}) => new Promise((resolve,reject) => {
    if (socket.readyState !== WebSocket.OPEN) { reject(new Error('Chrome connection is not open')); return; }
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Chrome command timed out: ${method}`)); },10000);
    pending.set(id,{resolve,reject,timer});
    socket.send(JSON.stringify({id,method,params}));
  });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await send('Page.enable'); await send('Runtime.enable');
  const close = async () => {
    socket.close(); rejectPending();
    await fetch(`${endpoint}/json/close/${target.id}`,{signal:AbortSignal.timeout(2000)}).catch(() => {});
  };
  return {send,evaluate,events,close};
}

export async function navigate(client, url = `${siteURL}/index.html`) {
  const result = await client.send('Page.navigate',{url});
  if (result.errorText) throw new Error(`Navigation failed: ${result.errorText}`);
  for (let i=0; i<80; i++) {
    await pause(50);
    if (await client.evaluate(`location.href === ${JSON.stringify(new URL(url).href)} && document.readyState === 'complete' && !!document.querySelector('#plan-items')`)) return;
  }
  throw new Error(`Product did not finish loading: ${url}`);
}
