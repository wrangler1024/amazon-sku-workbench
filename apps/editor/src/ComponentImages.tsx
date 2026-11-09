import {SecureImage} from './Media';
import ImageViewer from './ImageViewer';
import React,{useState} from 'react';
import {api} from './api';
import {Button,Help} from './prototype/ui';

export default function ComponentImages({itemId,ids=[],onChange,disabled,onBusy}:any){
 const [uploading,setUploading]=useState(false),[error,setError]=useState(''),[zoom,setZoom]=useState('');
 async function upload(file?:File){
  if(!file)return;
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>20_000_000){setError('请选择20MB以内的JPEG、PNG或WebP图片');return}
  setUploading(true);onBusy?.(true);setError('');
  try{const form=new FormData();form.append('file',file);const asset=await api('/api/items/'+itemId+'/assets/upload','POST',form);onChange([...ids,asset.id])}
  catch(e:any){setError(e.message)}finally{setUploading(false);onBusy?.(false)}
 }
 return <div className="component-images"><div className="field-label"><strong>组成产品图片 <span className="subtext">（选填）</span></strong><Help text="图片用于生成补充参考；没有图片也可继续。美工可查看货源链接核对。支持JPEG、PNG、WebP，单张不超过20MB，最多5张。"/></div><div className="component-image-list">{ids.map((id:string)=><div key={id}><button type="button" className="component-image-preview" onClick={()=>setZoom(id)} aria-label="放大组成产品图片"><SecureImage src={'/api/assets/'+id} alt="组成产品图片"/></button><Button kind="text small" disabled={disabled||uploading} onClick={()=>onChange(ids.filter((x:string)=>x!==id))}>移除</Button></div>)}{ids.length<5&&<label className="component-image-upload">{uploading?'上传中…':'＋ 上传图片'}<input aria-label="上传组成产品图片" type="file" accept="image/jpeg,image/png,image/webp" disabled={disabled||uploading} onChange={e=>{upload(e.target.files?.[0]);e.target.value=''}}/></label>}</div>{error&&<p className="collection-error" role="alert">{error}</p>}{!ids.length&&!uploading&&<small className="subtext">图片选填，无图也可保存并生成</small>}{zoom&&<ImageViewer title="组成产品图片" images={ids.map((id:string,i:number)=>({src:'/api/assets/'+id,name:'组成产品图片 '+(i+1)}))} index={Math.max(0,ids.indexOf(zoom))} onClose={()=>setZoom('')}/>}</div>
}
