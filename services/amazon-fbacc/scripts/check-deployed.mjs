import assert from 'node:assert/strict';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname} from 'node:path';
import {calculateFbaFee,VERSION,RATE_VERSION} from '../src/index.mjs';
import {MARKETS,EXAMPLE_PRICES,makeRequest} from '../public/model.mjs';

const base=new URL(process.argv[2]??'https://fbacc.samforo.icu');
assert.equal(base.protocol,'https:');assert.equal(base.username,'');assert.equal(base.password,'');
const evidence={checkedAt:new Date().toISOString(),url:base.origin,checks:[]};
async function fetchPath(path,init={}){
  return fetch(new URL(path,base),{...init,signal:AbortSignal.timeout(20000)});
}
for(const path of ['/healthz','/readyz']){
  const response=await fetchPath(path);assert.equal(response.status,200);
  const data=await response.json();assert.equal(data.version,VERSION);assert.equal(data.rateVersion,RATE_VERSION);assert.equal(data.marketplaces,17);
  evidence.checks.push({path,status:response.status,data});
}
for(const [path,file] of [['/','index.html'],['/assets/app.js','app.js'],['/assets/model.mjs','model.mjs'],['/assets/app.css','app.css'],['/assets/logo.svg','logo.svg']]){
  // Match the versioned URLs used by the page, avoiding an older cached asset.
  const assetPath=path.startsWith('/assets/') ? `${path}?v=${VERSION}` : path;
  const response=await fetchPath(assetPath);assert.equal(response.status,200);
  const bytes=Buffer.from(await response.arrayBuffer());
  const remote=createHash('sha256').update(bytes).digest('hex');
  const local=createHash('sha256').update(readFileSync(new URL('../public/'+file,import.meta.url))).digest('hex');
  assert.equal(remote,local,file);
  assert.ok(response.headers.get('strict-transport-security'));
  assert.ok(response.headers.get('content-security-policy'));
  evidence.checks.push({path,status:response.status,sha256:remote,matchesLocal:true});
}
const specResponse=await fetchPath('/openapi.json');const spec=await specResponse.json();
assert.deepEqual(spec.servers,[{url:base.origin}]);assert.ok(spec.paths['/readyz']);
evidence.checks.push({path:'/openapi.json',status:specResponse.status,server:spec.servers[0].url});
const values={length:30,width:20,height:5,dimensionUnit:'cm',weight:200,weightUnit:'g',saleDate:'2026-10-31',lowPriceEligibility:'conservative',cep:false};
const items=MARKETS.map(([code])=>makeRequest(values,code,EXAMPLE_PRICES[code]));
const batch=await fetchPath('/v1/fba/calculate-batch',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({items})});
assert.equal(batch.status,200);const result=await batch.json();assert.equal(result.results.length,17);
result.results.forEach((item,i)=>{assert.equal(item.ok,true);assert.deepEqual(item.result,calculateFbaFee(items[i]));});
evidence.checks.push({path:'/v1/fba/calculate-batch',status:batch.status,marketplaces:result.results.map(x=>({marketplace:x.result.marketplace,currency:x.result.currency,fee:x.result.fee.total})),requestId:batch.headers.get('x-request-id')});
const ca=items.find(x=>x.marketplace==='CA');
const single=await fetchPath('/v1/fba/calculate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(ca)});
assert.equal(single.status,200);assert.equal((await single.json()).fee.total,7.29);
const invalid=await fetchPath('/v1/fba/calculate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({...ca,price:{amount:25,currency:'USD'}})});
assert.equal(invalid.status,422);evidence.checks.push({path:'currency-validation',status:422});
for(const path of ['/.env','/src/server.mjs','/evidence/verification.json'])assert.equal((await fetchPath(path)).status,404);
const redirect=await fetch('http://'+base.host+'/',{redirect:'manual',signal:AbortSignal.timeout(10000)});
assert.equal(redirect.status,301);assert.equal(redirect.headers.get('location'),base.origin+'/');
evidence.checks.push({path:'HTTP redirect',status:301,location:redirect.headers.get('location')});
evidence.status='passed';
const output=process.argv[3]??new URL('../evidence/public-checks.json',import.meta.url).pathname;
mkdirSync(dirname(output),{recursive:true});writeFileSync(output,JSON.stringify(evidence,null,2)+'\n');
console.log(JSON.stringify({url:base.origin,status:'passed',marketplaces:17,assetsVerified:5,canadaExample:7.29}));
