import {useEffect,useRef,useState} from 'react';
import {apiFetch} from './api';
import {createRequestFlights,waitForRead} from './requestFlights';
import {mergeThumbnailRows} from './thumbnailRefresh';

export function listParameters(tab:string,editor:any,view:any,user:any,preview:string){
 const editing=['facts','listing','brief'].includes(tab),p=new URLSearchParams({tab,page:String(editing?editor.page||1:view.page||1)});
 if(editing){p.set('scope',editor.scope);p.set('q',editor.query||'');if(editor.range==='custom'){p.set('date_from',editor.from);p.set('date_to',editor.to)}else p.set('days',editor.range==='all'?'0':editor.range||'7')}
 else{const artist=user?.roles?.some((r:string)=>['artist','art_lead'].includes(r)),lead=user?.roles?.some((r:string)=>['admin','art_lead'].includes(r));
  const values={q:view.query||'',state:view.state??(tab==='review'?'pending':tab==='mine'?'unfinished':tab.endsWith('Publication')?'ready':''),task_range:view.range??(artist||lead?'pool':'assigned'),site:view.site||'',owner:view.owner||'',group:view.group||'',person:view.person||'',urgent:view.urgent?'true':'false'};
  for(const [k,v] of Object.entries(values))p.set(k,String(v));
 }
 return {path:'/api/workbench/items?'+p.toString(),preview:tab==='mine'?preview:''};
}

// Request identity includes account, role preview and every filter/page. A late
// response can never replace a newer query or a detail draft.
export function useWorkbenchPage(enabled:boolean,account:string,params:{path:string;preview:string}){
 const key=account+'|'+params.preview+'|'+params.path,current=useRef({key,account,params,enabled});current.current={key,account,params,enabled};
 const seq=useRef(0),cache=useRef(new Map<string,{page:any;etag:string}>());
 const pages=useRef(createRequestFlights()),counts=useRef(createRequestFlights());
 const [result,setResult]=useState<{key:string;page:any}|null>(null),[summaryResult,setSummary]=useState<{account:string;value:any}|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[summaryError,setSummaryError]=useState('');
 const alive=useRef(true);
 function refresh(silent=false,signal?:AbortSignal,fresh=false){
  const context=current.current;if(!context.enabled)return null;
  if(!silent)setLoading(true);
  // Counts are independent: slow/failed statistics must never hold up the list.
  void counts.current.run(context.account,async ownSignal=>{
   try{const r=await apiFetch('/api/workbench/summary',{signal:ownSignal,previewArtist:''});const value=await r.json();
    if(alive.current&&!ownSignal.aborted&&current.current.account===context.account){setSummary({account:context.account,value});setSummaryError('')}
   }catch{if(alive.current&&!ownSignal.aborted&&current.current.account===context.account)setSummaryError('待办统计暂未更新，列表可正常使用')}
  },fresh);
  const pending=pages.current.run(context.key,async ownSignal=>{
   const request=++seq.current,prior=cache.current.get(context.key);
   try{
    const r=await apiFetch(context.params.path,{signal:ownSignal,previewArtist:context.params.preview,headers:prior?{'If-None-Match':prior.etag}:{}});
    const page=r.status===304?prior?.page:await r.json();if(!page)throw new Error('列表读取失败，请重试');
    if(!alive.current||ownSignal.aborted||current.current.key!==context.key||request!==seq.current)return null;
    cache.current.set(context.key,{page,etag:r.headers.get('ETag')||prior?.etag||''});while(cache.current.size>24)cache.current.delete(cache.current.keys().next().value!);
    setResult(old=>old?.key===context.key&&old.page===page?old:{key:context.key,page});setError('');return page;
   }catch(e:any){if(alive.current&&current.current.key===context.key&&request===seq.current){setError(e.name==='AbortError'?'列表加载超时，请重试':e.message)}return null}
   finally{if(alive.current&&current.current.key===context.key&&request===seq.current)setLoading(false)}
  },fresh);
  return waitForRead(pending,signal).catch(()=>null);
 }
 useEffect(()=>{alive.current=true;return()=>{alive.current=false;pages.current.cancelExcept();counts.current.cancelExcept()}},[]);
 useEffect(()=>{counts.current.cancelExcept(account);setSummaryError('')},[account]);
 useEffect(()=>{pages.current.cancelExcept(enabled?key:undefined);if(!enabled)return;const cached=cache.current.get(key);if(cached)setResult({key,page:cached.page});setError('');void refresh(false)},[key,enabled]);
 // Newly approved items can arrive before their Base attachment is cached.
 // Poll only missing thumbnails; this never reloads detail, counts or drafts.
 const missing=result?.key===key?result.page.items.filter((r:any)=>r.thumbnail_pending&&!r.thumbnail_id).map((r:any)=>r.id).sort().join(','):'';
 useEffect(()=>{
  if(!enabled||!missing)return;
  let stopped=false,running=false;const controller=new AbortController();
  const wake=async()=>{
   if(stopped||running||document.hidden)return;running=true;const generation=seq.current;
   try{
    const query=new URLSearchParams();missing.split(',').forEach(id=>query.append('item_id',id));
    const r=await apiFetch('/api/workbench/thumbnails?'+query,{signal:AbortSignal.any([controller.signal,AbortSignal.timeout(20000)]),previewArtist:params.preview});
    const updates=await r.json();
    if(stopped||!alive.current||current.current.key!==key||seq.current!==generation)return;
    setResult(old=>{if(old?.key!==key)return old;const items=mergeThumbnailRows(old.page.items,updates);return items===old.page.items?old:{key,page:{...old.page,items}}});
   }catch{/* Optional image metadata retries on the next visible interval. */}finally{running=false}
  };
  const timer=setInterval(wake,10000);
  window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{stopped=true;controller.abort();clearInterval(timer);window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake)};
 },[key,enabled,missing]);
 return {page:result?.key===key?result.page:null,summary:summaryResult?.account===account?summaryResult.value:null,loading,error:error||summaryError,refresh,key};
}
