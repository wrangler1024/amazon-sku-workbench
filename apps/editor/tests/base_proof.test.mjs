import assert from 'node:assert/strict';
import test from 'node:test';
import {createProofWhenReady} from '../src/baseProof.ts';

test('waits for two stable permission reads before the one proof write',async()=>{
 const responses=[false,false,true,false,true,true],waits=[];let writes=0;
 const value=await createProofWhenReady(async()=>responses.shift(),async()=>{writes++;return 'proof'},async ms=>waits.push(ms));
 assert.equal(value,'proof');assert.equal(writes,1);assert.equal(responses.length,0);assert.deepEqual(waits,[250,250,250,250,250]);
});
test('denied or unloaded context never attempts a write and remains bounded',async()=>{
 for(const pending of [async()=>false,async()=>{throw new Error('table not loaded error')}]){
  let waits=0;await assert.rejects(createProofWhenReady(pending,async()=>assert.fail('must not write'),async()=>waits++),/检查身份校验表权限/);assert.equal(waits,95);
 }
});
test('does not repeat failed writes or bypass errors in permission queries',async()=>{
 for(const message of ['Network timeout','Failed to fetch','record permission denied error']){
  let writes=0;await assert.rejects(createProofWhenReady(async()=>true,async()=>{writes++;throw new Error(message)},async()=>{}),new RegExp(message));assert.equal(writes,1);
 }
 await assert.rejects(createProofWhenReady(async()=>{throw new Error('unknown permission API error')},async()=>assert.fail('must not write')),/unknown permission/);
});

import {initializeProof} from '../src/baseProof.ts';
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
function proofOptions(overrides={}){
 return {baseId:'base1',origin:'https://plugin.test',selection:async()=>({baseId:'base1'}),table:async()=> 'table1',permission:async()=>true,challenge:async()=>({parent_origin:'https://plugin.test',proof:'digest',challenge_id:'challenge',secret:'private'}),write:async()=> 'rec123',exchange:async()=>({access_token:'x'.repeat(48)}),...overrides};
}
test('initialization overlaps setup but never writes before context and permission finish',async()=>{
 const gates=Object.fromEntries(['selection','table','permission','challenge'].map(k=>[k,deferred()])),started=[];let writes=0;
 const p=initializeProof(proofOptions({...Object.fromEntries(Object.keys(gates).map(k=>[k,()=>{started.push(k);return gates[k].promise}])),write:async(table,proof)=>{assert.equal(table,'table1');assert.equal(proof,'digest');writes++;return 'rec123'}}));
 assert.deepEqual(started,['selection','table','permission','challenge']);gates.table.resolve('table1');gates.challenge.resolve({parent_origin:'https://plugin.test',proof:'digest'});gates.selection.resolve({baseId:'base1'});await Promise.resolve();assert.equal(writes,0);
 gates.permission.resolve(true);assert.equal(await p,'x'.repeat(48));assert.equal(writes,1);
});
test('wrong Base, wrong plugin origin and denied permission never write or exchange',async()=>{
 for(const override of [{selection:async()=>({baseId:'other'})},{challenge:async()=>({parent_origin:'https://evil.test'})},{permission:async()=>{throw Error('denied')}}]){
  await assert.rejects(initializeProof(proofOptions({...override,write:()=>assert.fail('no SDK write'),exchange:()=>assert.fail('no exchange')})));
 }
});
test('new identity proof is always created and exchanged; no stale session reuse',async()=>{
 let writes=0,exchanges=0;const stages=[];
 const options=proofOptions({write:async()=>{writes++;return 'rec'+writes},exchange:async body=>{assert.equal(body.record_id,'rec'+(++exchanges));assert.equal(body.secret,'private');return {access_token:'x'.repeat(48)}},stage:name=>stages.push(name)});
 await initializeProof(options);await initializeProof(options);assert.equal(writes,2);assert.equal(exchanges,2);assert.equal(stages.filter(s=>s==='exchange').length,2);
});
test('exchange failure or invalid session cannot complete initialization',async()=>{
 await assert.rejects(initializeProof(proofOptions({exchange:async()=>{throw Error('inactive account')}})),/inactive/);
 await assert.rejects(initializeProof(proofOptions({exchange:async()=>({access_token:''})})),/身份识别未完成/);
});
test('one explicit temporary proof-read failure retries the same record without another write',async()=>{
 let writes=0,reads=0;const payloads=[],waits=[];
 await initializeProof(proofOptions({write:async()=>{writes++;return 'record'},pause:async ms=>waits.push(ms),exchange:async body=>{payloads.push(body);if(!reads++)throw Object.assign(Error('飞书身份核验暂未完成，请重试识别'),{status:503});return {access_token:'x'.repeat(48)}}}));
 assert.equal(writes,1);assert.equal(reads,2);assert.equal(payloads[0],payloads[1]);assert.deepEqual(waits,[300]);
});
test('denial, ambiguous errors and a second transient failure never keep retrying',async()=>{
 for(const [status,message,expected] of [[403,'权限不足',1],[401,'身份校验已失效',1],[undefined,'Failed to fetch',1],[503,'other service error',1],[503,'飞书身份核验暂未完成，请重试识别',2]]){
  let reads=0,writes=0;await assert.rejects(initializeProof(proofOptions({pause:async()=>{},write:async()=>{writes++;return 'record'},exchange:async()=>{reads++;throw Object.assign(Error(message),{status})}})));
  assert.equal(writes,1);assert.equal(reads,expected);
 }
});
