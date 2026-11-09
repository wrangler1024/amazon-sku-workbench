// Poll a read-only permission check before any SDK write. Calling addRecord to
// probe readiness causes Feishu itself to display an error toast on each retry.
export async function createProofWhenReady<T>(ready:()=>Promise<boolean>,create:()=>Promise<T>,wait=(ms:number)=>new Promise<void>(resolve=>setTimeout(resolve,ms))):Promise<T>{
 let stable=0;
 // Permissions are host-side read-only checks. Poll at 250 ms so a ready host
 // is not left waiting through the old one-second backoff; retain a ~24 s budget.
 for(let attempt=0;attempt<96;attempt++){
  let allowed=false;
  try{allowed=await ready()}catch(error:any){
   if(!/^(record permission denied error|table not loaded error)$/i.test(String(error?.message||error)))throw error;
  }
  stable=allowed?stable+1:0;
  if(stable>=2)return create(); // No repeated writes, including ambiguous failures.
  if(attempt<95)await wait(250);
 }
 throw new Error('飞书身份校验暂不可用，请重新连接；仍失败请联系管理员检查身份校验表权限');
}

// All setup calls are read-only except issuing a short-lived server challenge.
// The SDK write still waits for the correct Base AND stable Addable permission.
export async function initializeProof<T>({baseId,origin,selection,table,permission,challenge,write,exchange,stage=()=>{},pause=(ms:number)=>new Promise<void>(r=>setTimeout(r,ms))}:{
 baseId:string;origin:string;selection:()=>Promise<{baseId?:string}>;table:()=>Promise<T>;
 permission:()=>Promise<unknown>;challenge:()=>Promise<any>;
 write:(table:T,proof:string)=>Promise<string>;exchange:(body:any)=>Promise<any>;stage?:(name:string)=>void;pause?:(ms:number)=>Promise<void>;
}){
 stage('start');
 const timed=async<V>(name:string,read:()=>Promise<V>)=>{const value=await read();stage(name);return value};
 const [selected,target,_,issued]=await Promise.all([
  timed('selection',selection),timed('table',table),timed('permission',permission),timed('challenge',challenge),
 ]);
 if(selected.baseId!==baseId)throw new Error('工作台与当前数据表不匹配');
 if(issued.parent_origin!==origin)throw new Error('工作台来源配置不匹配');
 const recordId=await write(target,String(issued.proof));stage('write');
 const payload={challenge_id:issued.challenge_id,secret:issued.secret,record_id:recordId};
 let result:any;
 for(let attempt=0;attempt<2;attempt++){
  try{result=await exchange(payload);break}catch(error:any){
   // This exact backend response precedes identity/session creation. Never
   // retry permission failures, ambiguous transport errors, or the SDK write.
   if(attempt||error.status!==503||error.message!=='飞书身份核验暂未完成，请重试识别')throw error;
   stage('read-retry');await pause(300);
  }
 }
 stage('exchange');
 if(typeof result.access_token!=='string'||result.access_token.length<32)throw new Error('身份识别未完成，请重新打开工作台');
 return result.access_token as string;
}
