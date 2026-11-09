export const LIST_REFRESH_INTERVAL = 10_000;

// Read-only polling: a changed scope invalidates an in-flight response.
// Returning null pauses polling without touching a detail draft or upload.
export function createListRefresher<T>({scope,read,apply,failed=()=>{},now=Date.now}:{
 scope:()=>string|null;
 read:(signal:AbortSignal)=>Promise<T>;
 apply:(value:T)=>void;
 failed?:()=>void;
 now?:()=>number;
}){
 let stopped=false,inflight=false,lastStarted=-Infinity,controller:AbortController|undefined;
 async function refresh(){
  const key=scope();
  if(stopped||inflight||key===null||now()-lastStarted<1000)return;
  inflight=true;lastStarted=now();controller=new AbortController();
  const timeout=setTimeout(()=>controller?.abort(),8000);
  try{
   const value=await read(controller.signal);
   if(!stopped&&scope()===key)apply(value);
  }catch{
   if(!stopped&&scope()===key)failed();
  }finally{clearTimeout(timeout);inflight=false;controller=undefined}
 }
 return {refresh,stop(){stopped=true;controller?.abort()}};
}

export function reviewSucceeded(action:string,result:any){return action==='review'&&!!result?.document}
