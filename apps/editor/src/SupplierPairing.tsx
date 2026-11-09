import React,{useEffect,useMemo,useState} from 'react';
import {Button,Help} from './prototype/ui';
import ImageViewer,{type ViewerSelection} from './ImageViewer';
import {SecureImage} from './Media';
import {sourceImage} from './imageCache';
import {dateText} from './api';

export default function SupplierPairing({data,initialSourceId='',busy,disabled,onRead,onSave,onDirty,onManual}:any){
 const sources=(data.document.body.sources||[]).filter((s:any)=>s.kind==='supplier'&&s.url);
 const [sourceId,setSourceId]=useState(initialSourceId||sources[0]?.id||'');
 const [preview,setPreview]=useState<ViewerSelection|null>(null),[submitting,setSubmitting]=useState(false),[elapsed,setElapsed]=useState(0);
 const [url,setUrl]=useState(''),[query,setQuery]=useState(''),[onlySelected,setOnlySelected]=useState(false);
 const [chosen,setChosen]=useState<Record<string,number>>({});
 const source=sources.find((s:any)=>s.id===sourceId);
 const snapshot=data.sources.find((s:any)=>s.url===source?.url&&s.body?.collector==='tmapi'&&s.body?.variants?.length);
 const job=data.jobs.find((j:any)=>j.kind==='source'&&j.source_url===source?.url);
 const reading=submitting||!!(job&&['queued','running'].includes(job.state));
 useEffect(()=>{setElapsed(0);if(!reading)return;const start=Date.now();const timer=setInterval(()=>setElapsed(Math.floor((Date.now()-start)/1000)),1000);return()=>clearInterval(timer)},[reading,sourceId]);
 const variants=snapshot?.body?.variants||[];
 const saved=useMemo(()=>Object.fromEntries((source?.selected_skus||[]).map((s:any)=>[s.sku_id,s.quantity])),[source?.selected_skus]);
 const changed=JSON.stringify(chosen)!==JSON.stringify(saved);
 useEffect(()=>{setChosen(saved);setQuery('');setOnlySelected(false)},[sourceId,snapshot?.id,saved]);
 useEffect(()=>{onDirty(changed||!!url.trim());return()=>onDirty(false)},[changed,url,onDirty]);
 const picked=variants.filter((v:any)=>Object.hasOwn(chosen,v.sku_id));
 const missing=Object.keys(chosen).filter(k=>!variants.some((v:any)=>v.sku_id===k));
 const valid=picked.length>0&&picked.length<=100&&!missing.length&&picked.every((v:any)=>Number.isFinite(chosen[v.sku_id])&&chosen[v.sku_id]>0&&chosen[v.sku_id]<=100000);
 const visible=variants.filter((v:any)=>(!onlySelected||Object.hasOwn(chosen,v.sku_id))&&(!query||(v.specification+' '+v.sku_id).toLowerCase().includes(query.toLowerCase())));
 const previewImages=visible.flatMap((v:any)=>{const url=v.images?.find((u:string)=>/^https:\/\//.test(u));return url?[{src:sourceImage(data.sources,url,'preview'),name:v.specification,skuId:v.sku_id}]:[]});
 async function read(value:string){if(changed&&!confirm('重新读取会重置未保存的规格选择，继续吗？'))return;setSubmitting(true);try{const id=await onRead(value);if(id){setSourceId(id);setUrl('')}}finally{setSubmitting(false)}}
 async function save(apply:boolean){await onSave({source_id:sourceId,snapshot_id:snapshot.id,selected:picked.map((v:any)=>({sku_id:v.sku_id,quantity:chosen[v.sku_id]})),apply_to_facts:apply})}
 return <div className="supplier-pairing">
  <div className="pair-add"><label>基础商品的1688链接<input aria-label="添加配对的1688链接" value={url} disabled={disabled||busy} onChange={e=>setUrl(e.target.value)} placeholder="https://detail.1688.com/offer/…html"/></label><Button kind="primary" disabled={disabled||busy||!url.trim()} onClick={()=>read(url.trim())}>添加并读取规格</Button></div>
  {!!sources.length&&<div className="pair-source"><label>当前基础商品<select aria-label="配对货源" value={sourceId} disabled={busy} onChange={e=>{if(!changed||confirm('切换货源将放弃未保存的规格选择，继续吗？'))setSourceId(e.target.value)}}>{sources.map((s:any,i:number)=><option key={s.id} value={s.id}>基础商品 {i+1} · {s.supplier_title||s.url}</option>)}</select></label><Button disabled={disabled||busy||reading||!source} onClick={()=>read(source.url)}>{reading?<><span className="parse-spinner small" aria-hidden="true"/>读取规格中…</>:snapshot?'重新读取规格':'读取规格'}</Button>{source&&<a href={source.url} target="_blank" rel="noreferrer">打开货源</a>}</div>}
  {reading&&<section className="supplier-progress" role="status" aria-live="polite" aria-busy="true"><span className="parse-spinner" aria-hidden="true"/><div><strong>{submitting?'正在提交解析请求':job?.state==='queued'?'解析任务排队中':'正在解析商品'}</strong><p>正在读取规格、图片和参考报价，完成后自动显示。可先关闭窗口，后台会继续处理。</p><small aria-live="off">已等待 {elapsed} 秒{elapsed>=30?' · 当前耗时较长，请勿重复提交':''}</small></div></section>}
  {reading&&!snapshot&&<div className="supplier-skeleton" aria-hidden="true">{[1,2,3].map(i=><div key={i}><span/><span/><span/></div>)}</div>}
  {job?.state==='failed'&&<div role="alert" className="note-box"><strong>规格读取失败</strong><p>{job.error}</p><Button kind="small" onClick={onManual}>手动补充资料</Button></div>}
  {snapshot?<>
   <div className="section-divider"><div><h3>{snapshot.body.title}</h3><p className="subtext">{variants.length} 个可选规格 · 报价读取于 {dateText(snapshot.created_at)}</p></div><Help text="参考报价和库存为采集时快照，采购时以供应商实际报价为准；每份用量指客户购买一份商品所包含的该规格数量。包装尺寸不自动带入产品事实。"/></div>
   {job?.state==='failed'&&<p className="subtext">以下保留上一次成功读取的规格与报价。</p>}
   <div className="pair-filter"><input aria-label="搜索货源规格" placeholder="搜索颜色、型号、包装或 SKU" value={query} onChange={e=>setQuery(e.target.value)}/><label className="review-check"><input type="checkbox" checked={onlySelected} onChange={e=>setOnlySelected(e.target.checked)}/>只看已选（{Object.keys(chosen).length}）</label></div>
   <div className="pair-table-wrap"><table className="pair-table"><thead><tr><th>选择</th><th>供应商规格</th><th>参考单价</th><th>参考库存</th><th>每份用量</th></tr></thead><tbody>{visible.map((v:any)=>{const checked=Object.hasOwn(chosen,v.sku_id),img=v.images?.find((u:string)=>/^https:\/\//.test(u));return <tr key={v.sku_id} className={checked?'is-selected':''}><td><input type="checkbox" aria-label={'选择规格 '+v.specification} checked={checked} disabled={disabled||busy||reading} onChange={e=>setChosen(old=>{const next={...old};if(e.target.checked)next[v.sku_id]=1;else delete next[v.sku_id];return next})}/></td><td><div className="pair-spec">{img&&<button type="button" className="source-image-button" onClick={()=>setPreview({images:previewImages,index:previewImages.findIndex((image:any)=>image.skuId===v.sku_id)})} aria-label={'查看规格图片 '+v.specification}><SecureImage src={sourceImage(data.sources,img)} alt={v.specification}/></button>}<div><strong>{v.specification}</strong><small>SKU {v.sku_id}</small></div></div></td><td>{v.reference_price?`${v.currency||'CNY'} ${v.reference_price}`:'未返回'}</td><td>{v.stock??'未返回'}</td><td><div className="pair-quantity"><input aria-label={'每份用量 '+v.specification} type="number" min="0.001" max="100000" step="any" value={checked?chosen[v.sku_id]:''} disabled={!checked||disabled||busy||reading} onChange={e=>setChosen(old=>({...old,[v.sku_id]:e.target.value===''?0:Number(e.target.value)}))}/><span>{snapshot.body.offer_unit||'件'}</span></div></td></tr>})}</tbody></table>{!visible.length&&<p className="empty">没有匹配的规格</p>}</div>
   {missing.length>0&&<p className="collection-error">已保存的 {missing.length} 个规格未在本次读取中找到，请核对后重新选择。<Button kind="text small" onClick={()=>setChosen(old=>Object.fromEntries(Object.entries(old).filter(([k])=>!missing.includes(k))))}>移除失效规格</Button></p>}
   <div className="pair-footer"><div><strong>已选 {picked.length} 个规格</strong><p className="subtext">将当前基础商品的所选规格加入组成明细；已添加的其他基础商品保留。</p></div><div className="live-actions"><Button disabled={disabled||busy||reading||!valid} onClick={()=>save(false)}>仅保存配对</Button><Button kind="primary" disabled={disabled||busy||reading||!valid} onClick={()=>save(true)}>带入产品资料</Button></div></div>
  </>:!reading&&<div className="empty"><p>添加基础商品的1688链接并读取，即可选择其规格与每份用量。</p><Button kind="small" onClick={onManual}>读取失败时手动录入</Button></div>}
 {preview&&<ImageViewer title="供应商规格图片" {...preview} onClose={()=>setPreview(null)}/>}
 </div>
}
