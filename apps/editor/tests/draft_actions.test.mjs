import assert from 'node:assert/strict';
import test from 'node:test';
import {persistDraft} from '../src/draftActions.ts';

const detail=(revision,body={})=>({document:{revision,body}});
test('save all edits before a dependent operation and pass the latest revision without confirming',async()=>{
 const calls=[];let acknowledged;
 const result=await persistDraft({data:detail(7),draft:{facts:{uses:'用途'},listing:{title:'文案'}},dirty:['listing','facts'],write:async body=>{calls.push(body);return detail(body.revision+1)},onSaved:d=>acknowledged=d});
 calls.push({action:'parse',revision:result.document.revision});
 assert.deepEqual(calls.map(x=>x.section||x.action),['facts','listing','parse']);
 assert.deepEqual(calls.map(x=>x.revision),[7,8,9]);
 assert(calls.slice(0,2).every(x=>x.confirm===false));assert.equal(acknowledged,result);
});
test('a later save failure retains the remaining draft and retries from the acknowledged revision',async()=>{
 let data=detail(3),dirty=['facts','sources'];const draft={facts:{uses:'原用途'},sources:[{url:'https://supplier'}]};const calls=[];
 const saved=(d,k)=>{data=d;dirty=dirty.filter(x=>x!==k)};
 await assert.rejects(persistDraft({data,draft,dirty,onSaved:saved,write:async body=>{calls.push(body);if(body.section==='sources')throw new Error('网络异常');return detail(body.revision+1)}}),/网络异常/);
 assert.equal(data.document.revision,4);assert.deepEqual(dirty,['sources']);assert.equal(draft.sources[0].url,'https://supplier');
 await persistDraft({data,draft,dirty,onSaved:saved,write:async body=>{calls.push(body);return detail(body.revision+1)}});
 assert.equal(calls.at(-1).revision,4);assert.equal(calls.at(-1).section,'sources');assert.deepEqual(dirty,[]);
});
test('confirmation is explicit and occurs after other dirty sections have saved',async()=>{
 const calls=[];
 await persistDraft({data:detail(1),draft:{facts:{},brief:{},selling_points:'卖点'},dirty:['brief','facts','selling_points'],section:'brief',confirm:true,onSaved:()=>{},write:async body=>{calls.push(body);return detail(body.revision+1)}});
 assert.deepEqual(calls.map(x=>x.section),['facts','selling_points','brief']);assert.deepEqual(calls.map(x=>x.confirm),[false,false,true]);
});
test('clean actions do not cause redundant saves',async()=>{
 const data=detail(6);assert.equal(await persistDraft({data,draft:{},dirty:[],write:()=>{throw new Error('unexpected write')},onSaved:()=>{}}),data);
});
