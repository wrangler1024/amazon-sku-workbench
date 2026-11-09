// Session-keyed blobs are shared across list/header/menu mounts; object URLs
// belong to each mounted image and are released when it unmounts.
export function createImageCache(fetchBlob:(path:string)=>Promise<Blob>,options:{parallel?:number;entries?:number;bytes?:number;ttl?:number}={}){
 const cache=new Map<string,{pending:Promise<Blob>;bytes:number;at:number}>();
 const queue:Array<()=>void>=[];let active=0,total=0;
 const cap=options.parallel??6,limit=options.entries??120,maxBytes=options.bytes??24_000_000,ttl=options.ttl??300_000;
 const drop=(key:string)=>{const old=cache.get(key);if(old){total-=old.bytes;cache.delete(key)}};
 function drain(){while(active<cap&&queue.length){active++;queue.shift()!()}}
 function load(path:string,session:string){
  const key=session+'\n'+path,old=cache.get(key);
  if(old&&Date.now()-old.at<ttl){cache.delete(key);cache.set(key,old);return old.pending}
  if(old)drop(key);
  let resolve!:(blob:Blob)=>void,reject!:(error:unknown)=>void;
  const entry={pending:new Promise<Blob>((yes,no)=>{resolve=yes;reject=no}),bytes:0,at:Date.now()};cache.set(key,entry);
  queue.push(()=>{fetchBlob(path).then(blob=>{
   entry.bytes=blob.size;total+=blob.size;entry.at=Date.now();resolve(blob);
   for(const [k,value] of cache){if(cache.size<=limit&&total<=maxBytes)break;if(value.bytes)drop(k)}
  }).catch(error=>{drop(key);reject(error)}).finally(()=>{active--;drain()})});drain();return entry.pending;
 }
 return {load};
}

export function sourceImage(snapshots:any[],url:string,size:'thumbnail'|'preview'='thumbnail'){
 return snapshots.find(s=>s.image_previews?.[url])?.image_previews[url]?.[size]||url;
}
