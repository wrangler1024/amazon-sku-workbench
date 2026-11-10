import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createServer} from '../src/server.mjs';
import {calculateFbaFee} from '../src/index.mjs';
import request from '../examples/request.json' with {type:'json'};

async function withServer(t,options={}) {
  const server=createServer(options);
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
  t.after(()=>new Promise(resolve=>server.close(resolve)));
  return {url:`http://127.0.0.1:${server.address().port}`,server};
}
const post=(url,path,body,headers={})=>fetch(url+path,{method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)});

test('HTTP single and batch endpoints preserve library results',async t=>{
  const {url}=await withServer(t);
  const single=await post(url,'/v1/fba/calculate',request);
  assert.equal(single.status,200);assert.deepEqual(await single.json(),calculateFbaFee(request));
  const batch=await post(url,'/v1/fba/calculate-batch',{items:[request,{}, {...request,marketplace:'BR'}]});
  const json=await batch.json();
  assert.equal(batch.status,200);assert.deepEqual(json.results.map(x=>x.ok),[true,false,true]);
  assert.equal(json.results[2].result.status,'quote_required');
  assert.equal(json.results[2].result.fee,null);
});

test('health, 17-market metadata and OpenAPI contract are served',async t=>{
  const {url}=await withServer(t);
  const health=await (await fetch(url+'/healthz')).json();assert.equal(health.marketplaces,17);
  const meta=await (await fetch(url+'/v1/fba/marketplaces')).json();assert.equal(meta.marketplaces.length,17);
  const spec=await (await fetch(url+'/openapi.json')).json();assert.equal(spec.openapi,'3.1.0');
  for(const path of ['/healthz','/v1/fba/marketplaces','/v1/fba/calculate','/v1/fba/calculate-batch','/openapi.json']) assert.ok(spec.paths[path]);
  function checkRefs(value) {
    if(!value || typeof value!=='object')return;
    if(value.$ref)assert.ok(spec.components.schemas[value.$ref.split('/').at(-1)],value.$ref);
    Object.values(value).forEach(checkRefs);
  }
  checkRefs(spec);
});

test('HTTP errors distinguish syntax, validation, method, media type and size',async t=>{
  const {url}=await withServer(t);
  assert.equal((await fetch(url+'/unknown')).status,404);
  const method=await fetch(url+'/v1/fba/calculate');assert.equal(method.status,405);assert.equal(method.headers.get('allow'),'POST');
  const malformed=await fetch(url+'/v1/fba/calculate',{method:'POST',headers:{'content-type':'application/json'},body:'{'});
  assert.equal(malformed.status,400);
  const missing=await post(url,'/v1/fba/calculate',{});assert.equal(missing.status,422);assert.ok((await missing.json()).error.path);
  const media=await fetch(url+'/v1/fba/calculate',{method:'POST',body:'text'});assert.equal(media.status,415);
  assert.equal((await post(url,'/v1/fba/calculate-batch',{items:[]})).status,422);
  assert.equal((await post(url,'/v1/fba/calculate-batch',{items:Array(101).fill(request)})).status,422);
  const big=await post(url,'/v1/fba/calculate',{padding:'x'.repeat(140000)});assert.equal(big.status,413);
});

test('streamed bodies also enforce the size limit',async t=>{
  const {url}=await withServer(t);
  const status=await new Promise((resolve,reject)=>{
    const req=http.request(url+'/v1/fba/calculate',{method:'POST',headers:{'content-type':'application/json'}},res=>{res.resume();resolve(res.statusCode);});
    req.on('error',reject);req.write('x'.repeat(70000));req.end('x'.repeat(70000));
  });
  assert.equal(status,413);
});

test('optional token protects calculations and metadata without logging it',async t=>{
  const {url}=await withServer(t,{apiKey:'test-only-token'});
  assert.equal((await fetch(url+'/healthz')).status,200);
  assert.equal((await fetch(url+'/v1/fba/marketplaces')).status,401);
  assert.equal((await post(url,'/v1/fba/calculate',request,{authorization:'Bearer wrong'})).status,401);
  const result=await post(url,'/v1/fba/calculate',request,{authorization:'Bearer test-only-token'});
  assert.equal(result.status,200);assert.equal((await result.json()).fee.total,7.29);
  const spec=await (await fetch(url+'/openapi.json',{headers:{authorization:'Bearer test-only-token'}})).json();
  assert.deepEqual(spec.security,[{bearerAuth:[]}]);
  assert.deepEqual(spec.paths['/healthz'].get.security,[]);
});
