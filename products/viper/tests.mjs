import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';

mkdirSync(new URL('./verification/',import.meta.url),{recursive:true});
const logPath = new URL('./verification/engine-tests.txt',import.meta.url);
const lines=[];
function log(text) { lines.push(text); writeFileSync(logPath,lines.join('\n')+'\n'); console.log(text); }
process.on('uncaughtExceptionMonitor',error=>{lines.push(`FAILED: ${error.stack}`);writeFileSync(logPath,lines.join('\n')+'\n');});
import vm from 'node:vm';

// Run the exact engine shipped in index.html; no copied model or dependencies.
const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
log(`Source SHA-256: ${createHash('sha256').update(html).digest('hex')}`);
const engine = html.match(/\/\* ENGINE_START[\s\S]*?\*\/([\s\S]*?)\/\* ENGINE_END \*\//)[1];
const context = vm.createContext({});
vm.runInContext(`${engine}\nglobalThis.Game = ViperGame;`, context);
const Game = context.Game;
let passed = 0;
function test(name, run) {
  try { run(); passed++; console.log(`PASS ${name}`); }
  catch (error) { console.error(`FAIL ${name}`); throw error; }
}
function equal(actual, expected) { assert.equal(JSON.stringify(actual), JSON.stringify(expected)); }
function foodAhead(g) {
  const d = {right:[1,0],left:[-1,0],up:[0,-1],down:[0,1]}[g.direction];
  g.food = {x:g.snake[0].x+d[0],y:g.snake[0].y+d[1]};
}

test('ready state never advances or accepts turns', () => {
  const g = new Game(() => 0), before = JSON.stringify(g.snake);
  assert.equal(g.state,'ready'); assert.equal(g.step(),null);
  assert.equal(g.turn('up'),false); assert.equal(JSON.stringify(g.snake),before);
});
test('start and one square of grid movement', () => {
  const g = new Game(() => 0);g.start();assert.equal(g.step(),'move');
  equal(g.snake,[{x:7,y:10},{x:6,y:10},{x:5,y:10}]);
});
test('food grows the tail and adds exactly one point', () => {
  const g = new Game(() => 0);g.start();foodAhead(g);
  assert.equal(g.step(),'food');assert.equal(g.score,1);assert.equal(g.snake.length,4);
  assert.ok(!g.snake.some(p => p.x===g.food.x && p.y===g.food.y));
});
test('all 79 reachable five-food milestones increase speed', () => {
  const g = new Game();let previous=166;
  for (let gear=0;gear<80;gear++) {
    g.score=gear*5;
    assert.equal(g.interval,165/(1+gear/8));assert.equal(g.pace,gear+1);
    assert.ok(g.interval<previous);assert.ok(g.interval>15);previous=g.interval;
    g.score=Math.min(397,gear*5+4);assert.equal(g.interval,previous);
  }
});
test('the fifth eaten food changes pace immediately', () => {
  const g = new Game();g.start();
  for(let i=0;i<5;i++){foodAhead(g);g.step();}
  assert.equal(g.score,5);assert.equal(g.interval,165/1.125);assert.equal(g.pace,2);
});
test('reverse is rejected for every heading', () => {
  const g = new Game();g.start();
  for(const [a,b] of [['right','left'],['left','right'],['up','down'],['down','up']]) {
    g.direction=a;assert.equal(g.turn(b),false);assert.equal(g.pending,null);
  }
});
test('reverse or straight inputs do not consume the valid turn slot', () => {
  const g = new Game();g.start();assert.equal(g.turn('left'),false);assert.equal(g.turn('right'),false);
  assert.equal(g.turn('up'),true);assert.equal(g.pending,'up');
});
test('double turn in a single step is dropped, not queued', () => {
  const g = new Game();g.start();assert.equal(g.turn('up'),true);assert.equal(g.turn('left'),false);
  g.step();assert.equal(g.direction,'up');assert.equal(g.pending,null);
  g.step();assert.equal(g.direction,'up');assert.equal(g.turn('left'),true);g.step();assert.equal(g.direction,'left');
});
test('invalid direction is a no-op', () => {const g=new Game();g.start();assert.equal(g.turn('diagonal'),false);});
test('pause freezes movement and clears pending input', () => {
  const g=new Game();g.start();g.turn('up');g.pause();const before=JSON.stringify(g.snake);
  assert.equal(g.pending,null);assert.equal(g.turn('down'),false);assert.equal(g.step(),null);
  assert.equal(JSON.stringify(g.snake),before);g.start();g.step();assert.equal(g.direction,'right');
});
test('pause does not turn a ready or ended round into a playable one', () => {
  const g=new Game();assert.equal(g.pause(),false);g.state='lost';assert.equal(g.pause(),false);assert.equal(g.state,'lost');
});
test('each wall ends the round without wrapping', () => {
  for(const [direction,x,y] of [['right',19,10],['left',0,10],['up',10,0],['down',10,19]]) {
    const g=new Game();g.start();g.snake=[{x,y}];g.direction=direction;
    assert.equal(g.step(),'lost');assert.equal(g.reason,'wall');equal(g.snake[0],{x,y});assert.equal(g.step(),null);
  }
});
test('body collision ends the round', () => {
  const g=new Game();g.start();g.snake=[{x:2,y:2},{x:2,y:3},{x:3,y:3},{x:3,y:2},{x:4,y:2}];g.direction='right';
  assert.equal(g.step(),'lost');assert.equal(g.reason,'tail');
});
test('entering the vacated last-tail square is legal', () => {
  const g=new Game();g.start();g.snake=[{x:2,y:2},{x:2,y:3},{x:3,y:3},{x:3,y:2}];g.direction='right';g.food={x:8,y:8};
  assert.equal(g.step(),'move');equal(g.snake[0],{x:3,y:2});assert.equal(g.snake.length,4);
});
test('food selection covers first and last free squares without overlap', () => {
  const g=new Game(() => 0);equal(g.food,{x:0,y:0});g.random=()=>.999999;equal(g.placeFood(),{x:19,y:19});
  for(let i=0;i<100;i++){g.random=()=>i/100;const f=g.placeFood();assert.ok(!g.snake.some(p=>p.x===f.x&&p.y===f.y));}
});
test('last empty square produces a win, not an infinite spawn loop', () => {
  const g=new Game(()=>0,3);g.start();g.snake=[{x:1,y:0},{x:0,y:0},{x:0,y:1},{x:1,y:1},{x:2,y:1},{x:2,y:2},{x:1,y:2},{x:0,y:2}];g.food={x:2,y:0};g.score=5;
  assert.equal(g.step(),'won');assert.equal(g.state,'won');assert.equal(g.food,null);assert.equal(g.score,6);assert.equal(g.snake.length,9);
});
test('restart is a ready screen, and a replay starts a fresh round', () => {
  const g=new Game();g.start();g.score=20;g.turn('up');g.reset();assert.equal(g.state,'ready');assert.equal(g.score,0);assert.equal(g.pending,null);assert.equal(g.snake.length,3);
  g.state='lost';g.score=20;g.start();assert.equal(g.state,'running');assert.equal(g.score,0);assert.equal(g.direction,'right');
});
test('seeded long sessions preserve grid and occupancy invariants', () => {
  let seed=9;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  const names=['up','right','down','left'];let moves=0;
  for(let run=0;run<100;run++) {
    const g=new Game(random);g.start();
    for(let i=0;i<500&&g.state==='running';i++) {
      if(random()<.3)g.turn(names[Math.floor(random()*4)]);g.step();moves++;
      assert.equal(g.snake.length,g.score+3);assert.equal(new Set(g.snake.map(p=>`${p.x},${p.y}`)).size,g.snake.length);
      assert.ok(g.snake.every(p=>Number.isInteger(p.x)&&Number.isInteger(p.y)&&p.x>=0&&p.x<20&&p.y>=0&&p.y<20));
      assert.ok(!g.food||!g.snake.some(p=>p.x===g.food.x&&p.y===g.food.y));
    }
  }
  assert.ok(moves>1000);
});
console.log(`\n${passed} engine tests passed. Source: index.html.`);
