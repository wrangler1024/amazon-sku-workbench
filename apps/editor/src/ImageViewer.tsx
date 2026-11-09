import React,{useEffect,useState} from 'react';
import {Modal} from './prototype/ui';
import {SecureImage} from './Media';
import './image-viewer.css';

export type ViewerImage={src:string;name:string};
export type ViewerSelection={images:ViewerImage[];index:number};

export default function ImageViewer({images,index=0,title='原图查看',onClose}:{images:ViewerImage[];index?:number;title?:string;onClose:()=>void}){
 const [position,setPosition]=useState(()=>Math.max(0,Math.min(index,images.length-1)));
 const current=images[position];
 useEffect(()=>{
  const keydown=(e:KeyboardEvent)=>{
   if(e.altKey||e.ctrlKey||e.metaKey||!['ArrowLeft','ArrowRight'].includes(e.key))return;
   e.preventDefault();e.stopPropagation();
   setPosition(p=>Math.max(0,Math.min(images.length-1,p+(e.key==='ArrowLeft'?-1:1))));
  };
  window.addEventListener('keydown',keydown);
  return()=>window.removeEventListener('keydown',keydown);
 },[images.length]);
 if(!current)return null;
 return <Modal title={title} className="image-viewer" onClose={onClose} footer={<><span className="image-viewer-name" title={current.name}>{current.name}</span><span className="image-viewer-counter" role="status" aria-live="polite">{position+1} / {images.length}</span>{images.length>1&&<span className="image-viewer-hint">← → 切换图片</span>}</>}>
  <div className="image-viewer-stage">
   <SecureImage key={current.src} loading="eager" className="live-full-image" src={current.src} alt={current.name}/>
   {images.length>1&&<><button type="button" className="image-viewer-arrow previous" aria-label="上一张" title="上一张（←）" disabled={position===0} onClick={()=>setPosition(p=>Math.max(0,p-1))}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 5-7 7 7 7"/></svg></button><button type="button" className="image-viewer-arrow next" aria-label="下一张" title="下一张（→）" disabled={position===images.length-1} onClick={()=>setPosition(p=>Math.min(images.length-1,p+1))}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></button></>}
  </div>
 </Modal>
}
