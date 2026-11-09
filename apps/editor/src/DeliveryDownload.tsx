import React,{useEffect,useRef,useState} from 'react';
import {api} from './api';
import {downloadFile} from './Media';
import {safeDownloadShortName} from './downloadNaming';
import {Button,Modal} from './prototype/ui';

export default function DeliveryDownload({id,children,className='button soft'}:{id:string;children:React.ReactNode;className?:string}){
 const [open,setOpen]=useState(false),[info,setInfo]=useState<any>(null),[name,setName]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false),[downloading,setDownloading]=useState(false);
 const token=useRef(0);useEffect(()=>()=>{token.current++},[]);
 const short=safeDownloadShortName(name),folder=info?info.prefix+short+info.suffix:'';
 const close=()=>{token.current++;setOpen(false)};
 async function prepare(){const current=++token.current;setOpen(true);setLoading(true);setError('');setInfo(null);try{const result=await api('/api/deliveries/'+id+'/download-info');if(token.current===current){setInfo(result);setName(result.suggested_short_name)}}catch(e:any){if(token.current===current)setError(e.message)}finally{if(token.current===current)setLoading(false)}}
 async function download(){if(!short||downloading)return;const current=token.current;setDownloading(true);setError('');try{await downloadFile('/api/deliveries/'+id+'/download?short_name='+encodeURIComponent(short));if(token.current===current)close()}catch(e:any){if(token.current===current)setError(e.message||'下载失败，请重试')}finally{setDownloading(false)}}
 return <><button type="button" className={className} onClick={prepare}>{children}</button>{open&&<Modal title="下载验收图稿" onClose={()=>{if(!downloading)close()}} footer={<><Button disabled={downloading} onClick={close}>取消</Button><Button kind="primary" disabled={loading||downloading||!info||!short} onClick={download}>{downloading?'正在下载…':'确认下载'}</Button></>}>
 {loading&&<p role="status">正在准备文件名称…</p>}{error&&<p className="note-box" role="alert">{error}</p>}{!loading&&!info&&<Button onClick={prepare}>重新读取</Button>}
 {info&&<><p className="subtext">按本批商品名称自动简化；需要时可调整，仅用于本次下载。</p><label className="field">产品简称<input aria-label="下载产品简称" value={name} maxLength={24} disabled={downloading} onChange={e=>setName(e.target.value)}/></label><p className="subtext">最多24字，建议使用便于辨认商品的简短名称。</p><div className="download-folder"><strong>{folder}.zip</strong><div>解压文件夹：{folder}</div></div><p className="subtext">任务编号、站点、交付美工、制作批次和交付版本自动带入，原图内容保持不变。</p></>}
 </Modal>}</>;
}
