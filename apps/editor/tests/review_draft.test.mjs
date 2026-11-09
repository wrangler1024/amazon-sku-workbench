import test from 'node:test';
import assert from 'node:assert/strict';
import {createReviewDraft,REVIEW_SAVE_DELAY} from '../src/reviewDraft.ts';
const value=note=>({note,image_notes:{a:note}});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}};
test('late save acknowledges its snapshot without replacing continued typing',async()=>{
 const pending=deferred(),calls=[];
 const d=createReviewDraft({value:value(''),revision:3,write:async(r,v)=>{calls.push({r,v});return calls.length===1?pending.promise:{revision:r+1}}});
 assert.equal(REVIEW_SAVE_DELAY,3000);d.update(value('第一段'));const saving=d.save();d.update(value('第一段，继续输入'));
 pending.resolve({revision:4});await saving;assert.equal(d.value().note,'第一段，继续输入');assert.equal(d.state().dirty,true);
 assert.equal(await d.flush(),true);assert.equal(d.revision(),5);assert.deepEqual(calls.map(c=>[c.r,c.v.note]),[[3,'第一段'],[4,'第一段，继续输入']]);assert.equal(d.state().dirty,false);
});
test('leaving while a request is running waits and saves the latest note once',async()=>{
 const pending=deferred();let calls=0;
 const d=createReviewDraft({value:value(''),revision:0,write:async r=>++calls===1?pending.promise:{revision:r+1}});
 d.update(value('旧段'));const s=d.save();d.update(value('最新段'));const leave=d.flush();const secondLeave=d.flush();
 assert.equal(calls,1);pending.resolve({revision:1});assert.equal(await leave,true);assert.equal(await secondLeave,true);await s;
 assert.equal(calls,2);assert.equal(d.revision(),2);assert.equal(d.value().note,'最新段');
});
test('lost acknowledgment retries the original idempotency key before new text',async()=>{
 const calls=[];let fail=true;
 const d=createReviewDraft({value:value(''),revision:4,write:async(r,v,key)=>{calls.push({r,v,key});if(fail){fail=false;throw Error('网络中断')}return {revision:r+1}}});
 d.update(value('第一版'));assert.equal(await d.flush(),false);assert.equal(d.state().dirty,true);d.update(value('最新第二版'));
 assert.equal(await d.flush(),true);assert.equal(calls[0].key,calls[1].key);assert.deepEqual(calls[0].v,calls[1].v);assert.notEqual(calls[1].key,calls[2].key);assert.equal(calls[2].r,5);assert.equal(calls[2].v.note,'最新第二版');
});
test('failed flush blocks leaving, retains edits and cannot reset with server data',async()=>{
 const d=createReviewDraft({value:value(''),revision:1,write:async()=>{throw Error('任务已更新，请刷新')}});
 d.update(value('人工返修意见'));assert.equal(await d.flush(),false);assert.equal(d.state().error,'任务已更新，请刷新');assert.equal(d.reset(value('远端旧值'),2),false);assert.equal(d.value().note,'人工返修意见');
});
test('unmounted controller ignores a late response and does not emit updates',async()=>{
 const pending=deferred(),states=[];const d=createReviewDraft({value:value(''),revision:0,write:()=>pending.promise,changed:s=>states.push(s)});
 d.update(value('文字'));const s=d.save();const n=states.length;d.stop();pending.resolve({revision:1});assert.equal(await s,false);assert.equal(states.length,n);
});
