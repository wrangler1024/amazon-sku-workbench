import test from 'node:test';
import assert from 'node:assert/strict';
import {createListRefresher,LIST_REFRESH_INTERVAL,reviewSucceeded} from '../src/listRefresh.ts';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
test('polls every ten seconds without overlapping focus and timer requests',async()=>{
 assert.equal(LIST_REFRESH_INTERVAL,10000);
 let clock=10000,reads=0;const pending=deferred(),applied=[];
 const p=createListRefresher({scope:()=> 'tasks:0',now:()=>clock,read:()=>{reads++;return pending.promise},apply:v=>applied.push(v)});
 const first=p.refresh();await p.refresh();clock+=10;await p.refresh();assert.equal(reads,1);
 pending.resolve(['new task']);await first;assert.deepEqual(applied,[['new task']]);
 await p.refresh();assert.equal(reads,1);clock+=10000;await p.refresh();assert.equal(reads,2);p.stop();
});
test('details, hidden pages, uploads and dialogs pause reads; scope changes reject old responses',async()=>{
 let scope=null,reads=0,applied=0;const pending=deferred();
 const p=createListRefresher({scope:()=>scope,read:()=>{reads++;return pending.promise},apply:()=>applied++});
 await p.refresh();assert.equal(reads,0);scope='tasks:0';const active=p.refresh();
 // Opening a detail while the list request is outstanding must not update detail state.
 scope=null;pending.resolve([]);await active;assert.equal(applied,0);p.stop();
 const other=deferred();scope='tasks:1';const p2=createListRefresher({scope:()=>scope,read:()=>other.promise,apply:()=>applied++});
 const r=p2.refresh();scope='tasks:2';other.resolve([]);await r;assert.equal(applied,0);p2.stop();
});
test('failed reads retain existing rows and recover on next interval',async()=>{
 let rows=['existing'],clock=0,failed=0,attempt=0;
 const p=createListRefresher({scope:()=> 'tasks',now:()=>clock,read:async()=>{if(!attempt++)throw Error('offline');return ['new']},apply:v=>rows=v,failed:()=>failed++});
 await p.refresh();assert.deepEqual(rows,['existing']);assert.equal(failed,1);
 clock+=10000;await p.refresh();assert.deepEqual(rows,['new']);p.stop();
});
test('unmount cancels request and prevents late application',async()=>{
 let signal,applied=false;const pending=deferred();
 const p=createListRefresher({scope:()=> 'tasks',read:s=>{signal=s;return pending.promise},apply:()=>applied=true});
 const r=p.refresh();p.stop();assert.equal(signal.aborted,true);pending.resolve([]);await r;assert.equal(applied,false);
});
test('only acknowledged acceptance or rework returns to list',()=>{
 assert.equal(reviewSucceeded('review',{document:{revision:7}}),true);
 assert.equal(reviewSucceeded('review',false),false);
 assert.equal(reviewSucceeded('review',true),false);
 assert.equal(reviewSucceeded('save-review',{document:{}}),false);
 assert.equal(reviewSucceeded('cancel-review',{document:{}}),false);
});
