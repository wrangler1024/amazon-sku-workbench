import React,{useEffect,useRef,useState} from 'react';
import {apiFetch} from './api';
import {apiUrl,nativePlugin,sessionToken} from './connection';
import {createImageCache} from './imageCache';

const images=createImageCache(async path=>(await apiFetch(path,{signal:AbortSignal.timeout(20000)})).blob());
const placeholder='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64"><rect width="64" height="64" rx="8" fill="#eef2ef"/><path d="M18 44V20h28v24H18zm1-4 9-10 7 7 5-5 6 8" fill="none" stroke="#94a69c" stroke-width="2"/><circle cx="39" cy="26" r="3" fill="#94a69c"/></svg>');

export function SecureImage({src,...props}:React.ImgHTMLAttributes<HTMLImageElement>){
 const ref=useRef<HTMLImageElement>(null),[loaded,setLoaded]=useState({source:'',url:''}),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 const protectedImage=!!src?.startsWith('/api/');
 useEffect(()=>{
  if(!protectedImage||!nativePlugin)return;
  setError('');let objectUrl='',started=false,cancelled=false;
  const load=async()=>{if(started)return;started=true;try{const blob=await images.load(src!,sessionToken());if(cancelled)return;objectUrl=URL.createObjectURL(blob);setLoaded({source:src!,url:objectUrl})}catch(e:any){if(!cancelled)setError(e.message||'图片暂未加载')}};
  const dialog=ref.current?.closest('dialog');
  const observer=new IntersectionObserver(entries=>{if(entries.some(x=>x.isIntersecting)){observer.disconnect();void load()}},{root:dialog?.querySelector('.modal-body')||null,rootMargin:'240px'});
  // A top-layer dialog may not intersect its original scrolled ancestor.
  // Full-size previews load immediately; list thumbnails remain lazy.
  if(props.className?.split(' ').includes('live-full-image')||props.loading==='eager')void load();
  else if(ref.current)observer.observe(ref.current);
  return()=>{cancelled=true;observer.disconnect();if(objectUrl)URL.revokeObjectURL(objectUrl)};
 },[src,protectedImage,retry]);
 return <img {...props} ref={ref} loading={protectedImage&&nativePlugin?'eager':props.loading||'lazy'} src={error?placeholder:protectedImage&&nativePlugin?(loaded.source===src&&loaded.url?loaded.url:placeholder):src} title={error?error+'；点击重试':props.title} onError={()=>setError('图片加载失败')} onClick={e=>{if(error){e.preventDefault();e.stopPropagation();setLoaded({source:'',url:''});setError('');setRetry(v=>v+1)}else props.onClick?.(e)}}/>;
}
export async function downloadFile(path:string,filename?:string){
 const r=await apiFetch(path);const blob=await r.blob();const url=URL.createObjectURL(blob);const disposition=r.headers.get('Content-Disposition')||'';const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];const name=encoded?decodeURIComponent(encoded):disposition.match(/filename="?([^";]+)/i)?.[1];const a=document.createElement('a');a.href=url;a.download=(filename||name||'图片下载').replace(/[\\/]/g,'_');document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
export function DownloadLink({path,children,filename,...props}:React.AnchorHTMLAttributes<HTMLAnchorElement>&{path:string;filename?:string}){
 const [busy,setBusy]=useState(false);
 async function download(e:React.MouseEvent){
  if(!nativePlugin)return;e.preventDefault();if(busy)return;setBusy(true);
  try{await downloadFile(path,filename)}catch(e:any){window.dispatchEvent(new CustomEvent('workbench-error',{detail:e.message||'下载失败，请重试'}))}finally{setBusy(false)}
 }
 return <a {...props} href={apiUrl(path)} onClick={download} aria-busy={busy}>{busy?'正在下载…':children}</a>;
}
