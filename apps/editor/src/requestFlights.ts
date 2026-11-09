// One network request per identity/query. A caller leaving a screen must not
// cancel another caller's shared read; the owner cancels on query/account change.
export function createRequestFlights(timeoutMs=15000){
 const flights=new Map<string,{controller:AbortController;promise:Promise<any>}>();
 function run<T>(key:string,read:(signal:AbortSignal)=>Promise<T>,fresh=false):Promise<T>{
  const previous=flights.get(key);
  if(previous&&!fresh)return previous.promise;
  if(previous)previous.controller.abort();
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  const entry={controller,promise:Promise.resolve().then(()=>read(controller.signal)).finally(()=>{
   clearTimeout(timer);if(flights.get(key)===entry)flights.delete(key);
  })};
  flights.set(key,entry);return entry.promise;
 }
 function cancelExcept(key?:string){for(const [k,f] of flights)if(k!==key){f.controller.abort();flights.delete(k)}}
 return {run,cancelExcept};
}

export function waitForRead<T>(promise:Promise<T>,signal?:AbortSignal):Promise<T>{
 if(!signal)return promise;
 if(signal.aborted)return Promise.reject(new DOMException('Read cancelled','AbortError'));
 return new Promise((resolve,reject)=>{
  const cancel=()=>reject(new DOMException('Read cancelled','AbortError'));
  signal.addEventListener('abort',cancel,{once:true});
  promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',cancel));
 });
}
