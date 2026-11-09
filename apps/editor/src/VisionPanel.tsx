import React from 'react';
import {Button,Help} from './prototype/ui';

const states:any={pending:'待准备',ready:'已提交',recognized:'已识别',unclear:'无法确认',unverified:'未返回识别回执',failed:'读取失败',skipped:'未送入',duplicate:'已合并'};
export default function VisionPanel({job,busy,editable,resume}:any){
 const r=job?.result?.vision;
 if(!job)return null;
 if(!job.vision_enabled&&!r)return <p className="subtext">本次任务使用文字资料，未启用图片识别。</p>;
 if(!r)return <p className="subtext">图片仅作补充参考：有可用图片时一并带入，缺图或读取失败仍继续生成。</p>;
 const waiting=r.state==='waiting_images',done=r.state==='completed',textOnly=r.state==='text_only';
 const label=waiting?'历史任务待继续':r.state==='preparing'?'准备参考图片':done?'识别完成':textOnly?'使用文字生成':r.state==='unverified'?'识别回执待核对':job.state==='failed'?'生成失败':'图片识别中';
 return <section className="vision-panel" aria-label="图片识别状态"><div className="section-divider"><h3>图片参考<Help text="图片仅为文案与图需生成提供补充参考，不作为生成门槛。缺图或读取失败时仍依据文字资料生成。已识别数按模型逐图回执统计，不代表商品事实已全部核实；美工仍需人工核对货源与参考链接。"/></h3><span className={'pill '+(done?'green':waiting||job.state==='failed'?'amber':'blue')}>{label}</span></div><div className="vision-counts"><span>{done?'已送入':'本次提交'} <strong>{r.sent_count||0}</strong> 张</span><span>模型已识别 <strong>{done?r.recognized_count:textOnly?0:'—'}</strong> 张</span><span>无法确认 <strong>{done?r.unclear_count:'—'}</strong> 张</span><span>未送入 <strong>{(r.failed_count||0)+(r.skipped_count||0)}</strong> 张</span></div>{r.reason&&<p className="subtext">{r.reason}</p>}{r.duplicate_count>0&&<p className="subtext">相同图片已合并 {r.duplicate_count} 份。</p>}{r.images?.length>0&&<details className="vision-details"><summary>查看逐张识别结果与失败原因</summary>{r.images.map((image:any)=><article key={image.image_id}><div><strong>{image.label}</strong><span className={'pill '+(['failed','unclear','unverified'].includes(image.state)?'amber':'neutral')}>{image.state==='ready'&&!r.sent_count?'准备完成':states[image.state]||image.state}</span></div>{image.observation&&<p>{image.observation}</p>}{image.error&&<p className={image.state==='recognized'?'subtext':'collection-error'}>{image.error}</p>}{image.duplicate_of&&<small className="subtext">与本次另一张图片相同，已合并其组成对应信息。</small>}</article>)}</details>}{waiting&&<><p className="subtext">这是旧版暂停的任务。现在图片为选填，可直接继续生成，无需补图。</p><div className="live-actions"><Button kind="primary" disabled={busy||!editable} onClick={()=>resume('retry_images')}>继续生成</Button></div></>}</section>
}
