import React from 'react';
import {Button,Help} from './prototype/ui';

export const collectorName=(name:string)=>({tmapi:'TMAPI',firecrawl:'Firecrawl',native:'自建采集'}[name]||name);
export const jobLabel=(s:string)=>({waiting_sources:'采集资料中',waiting_input:'待补充资料',queued:'等待生成',running:'生成中',failed:'生成失败',succeeded:'待核对候选'}[s]||s);
export function sourceLabel(s:any){
 if(s.state==='failed')return '读取失败';
 if(s.state==='succeeded')return s.status==='partial'?'部分读取':'读取成功';
 return s.state==='running'?'正在读取':'等待采集';
}
export default function CollectionPanel({rows=[],draft,updateManual,resume,busy,editable=false,waiting=false}:any){
 if(!rows.length)return <p className="subtext">未配置来源链接，将使用已确认资料生成。</p>;
 return <section className="collection-panel" aria-label="来源采集状态">
  <div className="section-divider"><h3>来源采集状态</h3><Help text="同一链接合并采集，24小时内成功结果可复用。网页和人工补充仅供参考；本品规格以确认资料为准。"/></div>
  {rows.map((s:any,i:number)=>{
   const failed=s.state==='failed'||(s.state==='succeeded'&&!s.has_text);
   const text=(draft?.sources||[]).find((x:any)=>x.url===s.url)?.manual_text||'';
   return <article className={'collection-source '+(failed?'collection-failed':'')} key={s.url}>
    <div className="collection-source-head"><strong>{s.kind==='supplier'?'1688 货源':'Amazon 竞品'} {i+1}</strong><span className={'pill '+(failed?'amber':'blue')}>{sourceLabel(s)}</span><span className="subtext">{collectorName(s.collector||'native')}</span>{s.total_ms!=null&&<span className="subtext">{(s.total_ms/1000).toFixed(1)}秒</span>}{s.reused&&<span className="subtext">复用近期结果</span>}{s.manual&&<span className="subtext">含人工补充</span>}</div>
    <a href={/^https:\/\//.test(s.url)?s.url:undefined} target="_blank" rel="noreferrer">{s.url}</a>
    {s.title&&<p>{s.title}</p>}{s.error&&<p className="collection-error" role="status">{s.error}</p>}
    {failed&&s.kind==='competitor'&&<p className="subtext">竞品参考选填，此链接读取失败不阻塞生成。</p>}
    {s.warning&&<p className="subtext">资料提示：{s.warning}</p>}
    {s.state==='succeeded'&&<p className="subtext">{s.has_text?'已取得文字资料':'未取得商品文字'} · 素材图片 {s.image_count||0} 张</p>}
    {waiting&&failed&&<label className="collection-manual">手动补充资料<textarea rows={4} maxLength={12000} disabled={!editable||busy} aria-label={'手动补充 '+s.url} value={text} onChange={e=>updateManual(s,e.target.value)} placeholder="可粘贴货源规格、商品说明或竞品参考文字；本品规格有变化时，请先回产品资料核对确认。"/></label>}
   </article>
  })}
  {waiting&&<><p className="subtext">尚未调用模型。补齐失败的货源资料后继续，也可选择仅使用已确认事实及已取得的参考资料；竞品参考选填。</p><div className="live-actions"><Button kind="primary" disabled={busy||!editable} onClick={()=>resume('manual')}>保存补充并继续生成</Button><Button disabled={busy||!editable} onClick={()=>resume('confirmed_facts')}>仅用已确认资料继续</Button><Button kind="text" disabled={busy||!editable} onClick={()=>resume('retry')}>重试未完整链接</Button></div></>}
 </section>
}
