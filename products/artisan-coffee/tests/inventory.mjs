// Read back every delivered source, document and evidence artifact; bind evidence to source.
import assert from 'node:assert/strict';
import {readFile,readdir,writeFile,stat} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const report=JSON.parse(await readFile('.verification/results.json','utf8'));
const run=JSON.parse(await readFile('.verification/run-status.json','utf8'));
const html=await readFile('index.html','utf8');
assert.equal(report.sourceHash,hash(html),'Browser evidence does not match current index.html');
assert.equal(run.sourceHash,report.sourceHash);
assert.equal(run.status,'passed','Latest verification did not finish successfully');
assert.ok(report.passed>=132);
const entries=[...((await readFile('REVIEW.md','utf8')).matchAll(/^(\d+)\. /gm))].map(match=>+match[1]);
assert.deepEqual(entries,Array.from({length:12},(_,i)=>i+1));
assert.equal((html.match(/<svg /g)||[]).length,4);
assert.equal((html.match(/<section /g)||[]).length,11);
assert.ok(!/<(?:script|img)[^>]+src=|https?:\/\//i.test(html),'External product dependency found');
assert.ok(report.layouts.every(layout=>layout.paragraphWords>1400&&layout.visibleSVGs===4));
const quality=await readFile('QUALITY.md','utf8');
assert.ok(quality.includes(`${report.passed} browser checks`),'QUALITY test count is stale');
assert.ok(quality.includes(report.layouts[0].paragraphWords.toLocaleString('en-IN')),'QUALITY prose count is stale');
const paths=['index.html','README.md','DESIGN.md','REVIEW.md','QUALITY.md'];
async function collect(directory) {
 for(const name of (await readdir(directory)).sort()) {
  if(name==='inventory.json'||name.startsWith('chrome-profile-'))continue;
  const path=`${directory}/${name}`;
  if((await stat(path)).isDirectory())await collect(path); else paths.push(path);
 }
}
await collect('tests'); await collect('.verification');
const files=[];
for(const path of paths) {
 const bytes=await readFile(path); const entry={path,bytes:bytes.length,sha256:hash(bytes)};
 if(path.endsWith('.png')) {
  assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');
  entry.dimensions=[bytes.readUInt32BE(16),bytes.readUInt32BE(20)];
 } else if(path.endsWith('.pdf')) {
  assert.equal(bytes.subarray(0,4).toString(),'%PDF');
 } else if(path.endsWith('.json')) JSON.parse(bytes.toString());
 else new TextDecoder('utf-8',{fatal:true}).decode(bytes);
 files.push(entry);
}
const manifest={sourceHash:report.sourceHash,checks:report.passed,files,note:'All listed files read back. PNG/PDF headers and JSON/UTF-8 validated; source/docs/evidence inspected. Binary validation is not a human visual review. This manifest excludes itself to avoid a circular hash.'};
await writeFile('.verification/inventory.json',JSON.stringify(manifest,null,2));
console.log(`Inventory verified: ${files.length} files; 12 review entries; ${report.passed} browser checks tied to current source.`);
