import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequestFlights,waitForRead} from '../src/requestFlights.ts';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
test('initial load, focus and timer join one request; next refresh reads again',async()=>{
 const f=createRequestFlights(),d=deferred();let reads=0;const read=()=>{reads++;return d.promise};
 const a=f.run('operator|tasks?page=1',read),b=f.run('operator|tasks?page=1',read),c=f.run('operator|tasks?page=1',read);
 assert.equal(a,b);assert.equal(b,c);await Promise.resolve();assert.equal(reads,1);d.resolve('page');assert.deepEqual(await Promise.all([a,b,c]),['page','page','page']);
 await f.run('operator|tasks?page=1',read);assert.equal(reads,2);
});
test('accounts, artist previews, filters and pages never share responses',async()=>{
 const f=createRequestFlights();let reads=0;
 const keys=['a|all|p1','b|all|p1','a|artist1|p1','a|all|p2','a|all|p1&q=x'];
 assert.deepEqual(await Promise.all(keys.map(key=>f.run(key,async()=>{reads++;return key}))),keys);assert.equal(reads,5);
});
test('canceling a polling subscriber leaves the foreground read alive',async()=>{
 const f=createRequestFlights(),d=deferred(),poll=new AbortController();let signal;
 const page=f.run('tasks',s=>{signal=s;return d.promise}),subscriber=waitForRead(page,poll.signal);
 await Promise.resolve();poll.abort();await assert.rejects(subscriber,{name:'AbortError'});assert.equal(signal.aborted,false);
 d.resolve('foreground');assert.equal(await page,'foreground');
});
test('fresh reads after mutations supersede earlier pre-mutation reads',async()=>{
 const f=createRequestFlights(),old=deferred(),fresh=deferred();let signal;
 const a=f.run('tasks',s=>{signal=s;return old.promise});await Promise.resolve();
 const b=f.run('tasks',()=>fresh.promise,true);assert.equal(signal.aborted,true);assert.notEqual(a,b);
 old.resolve('old');await a;assert.equal(f.run('tasks',()=>assert.fail('must join fresh')),b);
 fresh.resolve('fresh');assert.equal(await b,'fresh');
});
test('failed request does not poison retries and query changes cancel obsolete requests',async()=>{
 const f=createRequestFlights();await assert.rejects(f.run('tasks',async()=>{throw Error('offline')}),/offline/);
 assert.equal(await f.run('tasks',async()=> 'online'),'online');
 const a=deferred();let signal;const p=f.run('old',s=>{signal=s;return a.promise});await Promise.resolve();f.cancelExcept('new');assert.equal(signal.aborted,true);a.resolve(null);await p;
});
test('requests have a bounded timeout',async()=>{
 const f=createRequestFlights(10);await assert.rejects(f.run('hung',signal=>new Promise((_,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('timeout','AbortError'))))),{name:'AbortError'});
});
