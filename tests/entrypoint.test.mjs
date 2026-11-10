import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,symlink,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {once} from 'node:events';

test('CLI starts through an atomic-release symlink and shuts down cleanly',async()=>{
  const directory=await mkdtemp(path.join(tmpdir(),'fba-entry-'));
  const root=fileURLToPath(new URL('..',import.meta.url));
  const alias=path.join(directory,'current');await symlink(root,alias,'dir');
  const probe=createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
  const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
  const child=spawn(process.execPath,[path.join(alias,'src/server.mjs')],{cwd:alias,env:{...process.env,FBA_CALCULATOR_PORT:String(port),FBA_CALCULATOR_HOST:'127.0.0.1',FBA_CALCULATOR_API_KEY:'',FBA_CALCULATOR_ACCESS_LOG:'0',FBA_CALCULATOR_PUBLIC_URL:''},stdio:['ignore','pipe','pipe']});
  const closed=once(child,'close');let timer;
  try{
    await new Promise((resolve,reject)=>{
      timer=setTimeout(()=>reject(new Error('Symlink entry did not start')),5000);
      child.once('exit',code=>reject(new Error(`Symlink entry exited before listening: ${code}`)));
      child.once('error',reject);
      child.stdout.on('data',data=>{if(data.toString().includes('listening on'))resolve();});
    });
    const result=await fetch(`http://127.0.0.1:${port}/readyz`);assert.equal(result.status,200);
    assert.equal((await result.json()).marketplaces,17);
  }finally{
    clearTimeout(timer);if(child.exitCode==null)child.kill('SIGTERM');await closed;
    await rm(directory,{recursive:true,force:true});
  }
  assert.equal(child.exitCode,0);
});
