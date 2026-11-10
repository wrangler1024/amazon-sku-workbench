import assert from 'node:assert/strict';
import {createServer} from 'node:net';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import request from '../examples/request.json' with {type:'json'};

const root=fileURLToPath(new URL('..',import.meta.url));
const reserve=createServer();
await new Promise(resolve=>reserve.listen(0,'127.0.0.1',resolve));
const port=reserve.address().port;
await new Promise(resolve=>reserve.close(resolve));
const child=spawn(process.execPath,['src/server.mjs'],{cwd:root,env:{...process.env,FBA_CALCULATOR_HOST:'127.0.0.1',FBA_CALCULATOR_PORT:String(port),FBA_CALCULATOR_API_KEY:''},stdio:['ignore','pipe','pipe']});
const closed=once(child,'close');
let timer;
try {
  await new Promise((resolve,reject)=>{
    timer=setTimeout(()=>reject(new Error('Server startup timed out')),10000);
    child.once('error',reject);
    child.once('exit',code=>reject(new Error(`Server exited before ready: ${code}`)));
    child.stdout.on('data',data=>{if(data.toString().includes('listening on'))resolve();});
  });
  clearTimeout(timer);
  const base=`http://127.0.0.1:${port}`;
  const get=async path=>{
    const res=await fetch(base+path,{signal:AbortSignal.timeout(5000)});
    assert.equal(res.status,200);return res.json();
  };
  const health=await get('/healthz');assert.equal(health.marketplaces,17);
  const meta=await get('/v1/fba/marketplaces');assert.equal(meta.marketplaces.length,17);
  const response=await fetch(base+'/v1/fba/calculate',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request),signal:AbortSignal.timeout(5000)});
  assert.equal(response.status,200);
  const result=await response.json();assert.equal(result.fee.total,7.29);
  const schema=await get('/openapi.json');assert.equal(schema.openapi,'3.1.0');
  const proof={checkedAt:new Date().toISOString(),transport:'real Node server process on loopback',
    health,marketplaces:meta.marketplaces.map(m=>m.marketplace),example:result,openapi:schema.openapi,status:'passed'};
  writeFileSync(new URL('../evidence/http-smoke.json',import.meta.url),JSON.stringify(proof,null,2)+'\n');
  console.log(JSON.stringify({status:'passed',marketplaces:17,exampleFee:result.fee.total,currency:result.currency,serverStoppedAfterCheck:true}));
} finally {
  clearTimeout(timer);
  if(child.exitCode==null)child.kill('SIGTERM');
  await closed;
}
