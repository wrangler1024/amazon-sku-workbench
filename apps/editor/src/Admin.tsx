import React,{useEffect,useState} from 'react';
import {collectorName} from './CollectionPanel';
export default function Admin({api,onUpdated}:any){
 const [notifications,setNotifications]=useState<any>(null);
 const [collector,setCollector]=useState<any>(null),[capacity,setCapacity]=useState<any>(null);
 const [providers,setProviders]=useState<any[]>([]),[jobs,setJobs]=useState<any[]>([]),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 useEffect(()=>{Promise.all([api('/api/admin/providers'),api('/api/admin/jobs'),api('/api/admin/collector'),api('/api/admin/capacity'),api('/api/admin/notifications')]).then(([p,j,c,b,n])=>{setProviders(p);setJobs(j);setCollector(c);setCapacity(b);setNotifications(n)}).catch(e=>setMessage(e.message))},[]);
 function change(i:number,key:string,value:any){setProviders(providers.map((p,j)=>i===j?{...p,[key]:value}:p))}
 async function check(p:any){setBusy(true);setMessage('');try{const result=await api('/api/admin/providers/'+p.provider+'/check','POST',{});setMessage(result.message);setProviders(await api('/api/admin/providers'))}catch(e:any){setMessage(e.message)}finally{setBusy(false)}}
 async function save(p:any){setBusy(true);setMessage('');try{setProviders(await api('/api/admin/providers/'+p.provider,'PUT',{api_key:p.api_key||'',model:p.model,enabled:p.enabled,daily_limit:p.daily_limit}));await onUpdated();setMessage('配置已保存，密钥已加密')}catch(e:any){setMessage(e.message)}finally{setBusy(false)}}
 async function saveCollector(){setBusy(true);setMessage('');try{setCollector(await api('/api/admin/collector','PUT',{provider:collector.config_provider,api_key:collector.api_key||'',enabled:collector.enabled,daily_limit:collector.daily_limit}));setMessage('采集配置已保存，密钥已加密')}catch(e:any){setMessage(e.message)}finally{setBusy(false)}}
 async function checkCollector(){setBusy(true);setMessage('');try{const r=await api('/api/admin/collector/check?provider='+collector.config_provider,'POST',{});setMessage(r.message+(r.remaining_credits!=null?' · 剩余 '+r.remaining_credits+' credits':''));setCollector(await api('/api/admin/collector?provider='+collector.config_provider))}catch(e:any){setMessage(e.message)}finally{setBusy(false)}}
 async function notificationAction(action:string){setBusy(true);setMessage('');try{if(action==='toggle')setNotifications(await api('/api/admin/notifications','PUT',{enabled:!notifications.enabled}));else if(action==='test'){const r=await api('/api/admin/notifications/test','POST',{});setMessage('测试通知已进入队列，仅发送给 '+r.recipient)}else setNotifications(await api('/api/admin/notifications'))}catch(e:any){setMessage(e.message)}finally{setBusy(false)}}
 return <main className="team">
  <div className="section-head"><h2>amazon-aigc-workbench 管理后台</h2><span className="badge">仅管理员</span></div>
  <p className="muted">配置模型服务。密钥仅由服务端加密保存，不在页面回显；留空保留已有密钥。OpenAI 经受支持地区网关接入时，此处填写网关令牌。</p>
  {message&&<p role="status" className="notice">{message}</p>}
  {notifications&&<section className="inset" aria-label="协作通知"><div className="section-head"><h3>飞书协作通知 · {notifications.enabled?'已启用':'已暂停'}</h3><button disabled={busy} onClick={()=>notificationAction('toggle')}>{notifications.enabled?'暂停通知':'启用通知'}</button></div><p>新图需通知美工负责人；认领、指派、交付通知相关运营；返修与验收结果通知执行美工。本人操作不重复通知本人。</p><p>已发送 {notifications.counts.sent||0} · 待发送 {notifications.counts.queued||0} · 重试 / 失败 {notifications.counts.failed||0} · 已跳过 {notifications.counts.skipped||0}</p><button disabled={busy||!notifications.enabled} onClick={()=>notificationAction('test')}>发送测试通知给我</button> <button disabled={busy} onClick={()=>notificationAction('refresh')}>刷新通知记录</button><details><summary>最近通知</summary><div className="table-scroll"><table><thead><tr><th>商品</th><th>事件</th><th>收件人</th><th>状态</th><th>说明</th></tr></thead><tbody>{notifications.events.map((e:any)=><tr key={e.id}><td>{e.code||'通道测试'}</td><td>{e.action}</td><td>{e.recipient_name}</td><td>{e.state}</td><td>{e.error||''}</td></tr>)}</tbody></table></div></details></section>}
  {capacity&&<section className="inset" aria-label="团队容量"><div className="section-head"><h3>{capacity.team_size}人容量档</h3><button onClick={()=>api('/api/admin/capacity').then(setCapacity)}>刷新用量</button></div><p>生成并发 {capacity.model_concurrency} · 采集并发 {capacity.source_concurrency} · 每位运营同时生成最多 {capacity.per_operator_concurrency} 项</p><p>{capacity.model_daily_total===0?'团队每日调用次数不设上限':`全部模型共享每日 ${capacity.model_daily_total} 个任务`}，今日已提交 {capacity.used_today.total} 个（北京时间）。此处为服务容量，不代表已添加相应数量的成员。</p></section>}
  {providers.map((p,i)=><section className="inset" key={p.provider}>
   <div className="section-head"><h3>{p.provider==='openai'?'OpenAI':'DeepSeek'}</h3><span className="badge">{p.configured?'已配置密钥':'未配置'}</span></div>
   <label className="field"><span>{p.provider==='openai'?'API Key / 网关令牌':'API Key'}</span><input type="password" autoComplete="new-password" value={p.api_key||''} placeholder={p.configured?'留空保留已有密钥':p.provider==='openai'?'填写 OpenAI 密钥或模型网关令牌':'填写服务端API密钥'} onChange={e=>change(i,'api_key',e.target.value)}/></label>
   <label className="field"><span>模型名称</span><input value={p.model} onChange={e=>change(i,'model',e.target.value)}/></label>
   <div className="field"><span>此模型每日调用次数</span><label><input type="checkbox" checked={p.daily_limit===0} onChange={e=>change(i,'daily_limit',e.target.checked?0:1500)}/> 不限制每日调用次数</label>{p.daily_limit!==0&&<label><span>每日上限</span><input type="number" min={1} max={10000} value={p.daily_limit} onChange={e=>change(i,'daily_limit',Number(e.target.value))}/></label>}</div>
   <label><input type="checkbox" checked={p.enabled} onChange={e=>change(i,'enabled',e.target.checked)}/> 启用此供应商</label>
   <div className="actions"><button className="primary" disabled={busy} onClick={()=>save(p)}>保存配置</button><button disabled={busy||!p.configured} onClick={()=>check(p)}>验证连接</button></div>
   {p.last_check&&<p className="muted">上次验证（{p.last_check.model}）：{p.last_check.message}</p>}
  </section>)}
  {collector&&<section className="inset"><div className="section-head"><h3>来源采集服务</h3><span className="badge">{collector.configured?'已配置密钥':'未配置'}</span></div>
  <p className="muted">TMAPI用于1688货源及Amazon竞品。生成前自动读取，失败可手动补充。</p>
  <label className="field"><span>采集服务</span><select aria-label="采集服务" disabled={busy} value={collector.config_provider||'tmapi'} onChange={async e=>{try{setCollector(await api('/api/admin/collector?provider='+e.target.value))}catch(err:any){setMessage(err.message)}}}><option value="tmapi">TMAPI</option><option value="firecrawl">Firecrawl（保留配置）</option></select></label>
  <label className="field"><span>{collectorName(collector.config_provider)} API Key</span><input aria-label="采集服务 API Key" type="password" autoComplete="new-password" value={collector.api_key||''} placeholder={collector.configured?'留空保留已有密钥':'填写所选服务的API Key'} onChange={e=>setCollector({...collector,api_key:e.target.value})}/></label>
  <label className="field"><span>每日接口请求上限</span><input type="number" min={1} max={10000} value={collector.daily_limit||100} onChange={e=>setCollector({...collector,daily_limit:Number(e.target.value)})}/></label>
  <label><input type="checkbox" checked={collector.enabled} onChange={e=>setCollector({...collector,enabled:e.target.checked})}/> 启用所选采集服务</label>
  <div className="actions"><button className="primary" disabled={busy} onClick={saveCollector}>保存采集配置</button><button disabled={busy||!collector.configured} onClick={checkCollector}>验证采集连接</button></div>
  <p className="muted">TMAPI验证连接会读取一条测试货源，计入接口用量。</p>
  {collector.last_check&&<p className="muted">{collector.last_check.message}{collector.last_check.remaining_credits!=null?' · 剩余 '+collector.last_check.remaining_credits+' credits':''}</p>}</section>}
  <div className="section-head"><h3>采集与生成任务</h3><button onClick={()=>api('/api/admin/jobs').then(setJobs).catch((e:any)=>setMessage(e.message))}>刷新日志</button></div>
  {jobs.length===0&&<p className="muted">暂无任务</p>}
  {jobs.map(j=><details className="inset" key={j.id}>
   <summary>{j.code||'—'} · {j.kind==='source'?(j.source_kind==='supplier'?'货源采集':'竞品采集'):j.task==='brief'?'图需生成':'文案生成'} · {({queued:'排队中',waiting_sources:'采集资料中',waiting_input:'待补充资料',running:'执行中',succeeded:'成功',failed:'失败'} as any)[j.state]}</summary>
   <p>{j.error||(j.state==='succeeded'?'任务已完成':j.state==='waiting_input'?'尚未调用模型，等待补充资料或选择继续':'任务正在处理')}</p>{j.source_url&&<p className="subtext">{j.source_url}</p>}
   <p className="muted">提交：{new Date(j.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} · 完成：{j.finished_at?new Date(j.finished_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'尚未完成'}</p>
   {j.kind==='source'&&<p className="muted">{collectorName(j.collector||'native')} · 网页 {j.page_ms? (Number(j.page_ms)/1000).toFixed(1)+'秒':'—'} · 含素材入库 {j.total_ms?(Number(j.total_ms)/1000).toFixed(1)+'秒':'—'}</p>}
   {j.kind==='model'&&<p className="muted">模型：{j.model||'未记录'} · 请求ID：{j.request_id||'未记录'}</p>}
   {j.usage&&<p className="muted">输入 {j.usage.input_tokens??'—'} / 输出 {j.usage.output_tokens??'—'} tokens；其中推理 {j.usage.output_tokens_details?.reasoning_tokens??'—'}</p>}
   {j.incomplete_reason&&<p className="muted">中断原因：{j.incomplete_reason}</p>}
  </details>)}
 </main>
}
