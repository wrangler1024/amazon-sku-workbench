import {initializeBaseAccess,isBaseEmbedded} from './baseAccess';
import {apiUrl,nativePlugin,sessionToken,previewContext} from './connection';
export async function apiFetch(path:string,options:RequestInit & {previewArtist?:string}={}){
 const {previewArtist=previewContext(),...requestOptions}=options;
 if(previewArtist&&!['GET','HEAD'].includes((options.method||'GET').toUpperCase()))throw new Error('管理员只读预览不允许执行写操作');
 const send=()=>fetch(apiUrl(path),{...requestOptions,credentials:nativePlugin?'omit':'same-origin',headers:{'X-Requested-With':'amazon-workbench',...options.headers,...(previewArtist?{'X-Workbench-Preview':previewArtist}:{}),...(nativePlugin?{'Authorization':'Bearer '+sessionToken(),'X-Workbench-Context':'feishu-base'}:{})}});
 let r=await send();if(r.status===401&&isBaseEmbedded){await initializeBaseAccess();r=await send()}
 if(r.status===304)return r;
 if(!r.ok){let e;try{e=await r.json()}catch{e={detail:'网络请求失败，请重试'}}throw new Error(typeof e.detail==='string'?e.detail:JSON.stringify(e.detail))}
 return r;
}
export async function api(path:string,method='GET',data?:any,previewArtist?:string){
 const options:any={method,headers:{},previewArtist};
 if(method!=='GET'){options.headers['Idempotency-Key']=crypto.randomUUID();if(data instanceof FormData)options.body=data;else{options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}}
 const r=await apiFetch(path,options);return r.status===204?null:r.json();
}
export const emptyListing={title:'',highlights:'',bullets:['','','','',''],description:'',terms:''};
export {emptyBrief,briefImageCount,briefFields} from './briefHelpers';

export function fullCopy(l:any){return l?['商品标题 Title',l.title,'商品亮点 Item Highlights',l.highlights,'五点描述 Bullet Points',...(l.bullets||[]),'长描述 Product Description',l.description,'Search Terms',l.terms].join('\n\n'):''}
export const dateText=(date:string)=>date?new Date(date).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';

export function apiRead(path:string,previewArtist=''){return api(path,'GET',undefined,previewArtist)}
