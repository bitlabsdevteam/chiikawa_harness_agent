// One-command verification with an isolated Chrome profile and automatic cleanup.
import {createServer} from 'node:http';
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir,mkdtemp,rm} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve} from 'node:path';
import {once} from 'node:events';
const directory=resolve('.verification');
await mkdir(directory,{recursive:true});
const sourceHash=createHash('sha256').update(await readFile('index.html')).digest('hex');
const status={status:'running',sourceHash,started:new Date().toISOString()};
await writeFile(`${directory}/run-status.json`,JSON.stringify(status,null,2));
await rm(`${directory}/results.json`,{force:true});
const profile=await mkdtemp(`${directory}/chrome-profile-`);
const server=createServer(async (request,response) => {
 if (request.url==='/favicon.ico') { response.writeHead(204); response.end(); return; }
 if (request.url!=='/'&&request.url!=='/index.html') { response.writeHead(404); response.end('Not found'); return; }
 response.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'});
 response.end(await readFile('index.html'));
});
let chrome, child, chromeLog='';
let aborted=false;
const stop = () => { aborted=true; child?.kill('SIGTERM'); chrome?.kill('SIGTERM'); };
process.once('SIGINT',stop); process.once('SIGTERM',stop);
const execute = async (script,env) => {
 if(aborted)throw Error('Verification interrupted');
 child=spawn(process.execPath,[script],{stdio:'inherit',env:{...process.env,...env}});
 const timer=setTimeout(()=>child.kill('SIGTERM'),120000);
 try { const [code,signal]=await once(child,'exit'); if(code!==0)throw Error(`${script} failed (${signal||code})`); }
 finally { clearTimeout(timer); child=null; }
};
try {
 server.listen(0,'127.0.0.1'); await once(server,'listening');
 chrome=spawn(process.env.CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
  '--headless','--disable-gpu','--no-first-run','--no-default-browser-check','--disable-background-networking','--disable-component-update','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank'
 ],{stdio:['ignore','ignore','pipe']});
 let launchError;
 chrome.on('error',error=>{launchError=error;});
 chrome.stderr.on('data',data=>{chromeLog+=data.toString();});
 let port;
 for(let i=0;i<100;i++) {
  if(launchError)throw launchError;
  if(aborted||chrome.exitCode!==null)throw Error('Chrome exited before becoming ready');
  try { port=Number((await readFile(`${profile}/DevToolsActivePort`,'utf8')).split('\n')[0]); if(port)break; } catch {}
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 if(!port)throw Error('Chrome did not publish a debugging port within 10 seconds');
 const env={SITE_URL:`http://127.0.0.1:${server.address().port}`,CDP_URL:`http://127.0.0.1:${port}`};
 await execute('tests/verify.mjs',env);
 await execute('tests/audit.mjs',env);
 status.status='passed';
} catch(error) {
 status.status='failed'; status.error=error.message; process.exitCode=1; console.error(error.message);
} finally {
 child?.kill('SIGTERM'); chrome?.kill('SIGTERM');
 if(chrome && chrome.exitCode===null && !chrome.killed)chrome.kill('SIGTERM');
 if(chrome && chrome.exitCode===null)await Promise.race([once(chrome,'exit'),new Promise(resolve=>setTimeout(resolve,3000))]);
 if(chrome && chrome.exitCode===null)chrome.kill('SIGKILL');
 server.closeAllConnections(); await new Promise(resolve=>server.close(resolve));
 await rm(profile,{recursive:true,force:true,maxRetries:4,retryDelay:200});
 await writeFile(`${directory}/chrome.log`,chromeLog);
 await writeFile(`${directory}/server.log`,'Isolated loopback HTTP server stopped. Only index.html was served; favicon requests received 204.\n');
 status.finished=new Date().toISOString();
 await writeFile(`${directory}/run-status.json`,JSON.stringify(status,null,2));
}
if(status.status==='passed') {
 try { await execute('tests/inventory.mjs',{}); }
 catch(error) { status.status='failed'; status.error=error.message; await writeFile(`${directory}/run-status.json`,JSON.stringify(status,null,2)); process.exitCode=1; }
}
