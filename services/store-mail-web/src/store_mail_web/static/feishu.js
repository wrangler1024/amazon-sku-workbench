import { roleOptions } from "./roles.js";
import { $, $$, api, esc, fmtTime, state, toast, openModal, closeModal, personName, storeName } from './core.js';

export async function startFeishu(bind=false) {
  try {
    const destination=location.hash && location.hash!=='#/join' ? '/'+location.hash : '/#/inbox';
    sessionStorage.setItem('xynigo_login_destination',destination);
    const result=await api(bind?'/api/auth/feishu/bind':'/api/auth/feishu/start',{
      method:'POST',body:{return_to:destination}
    });
    location.assign(result.url);
  } catch(e) { toast(e.message,true); }
}

export async function setupFeishu() {
  try {
    const config=await api('/api/auth/config');
    state.feishuEnabled=config.feishu_enabled;
    $('#feishu-login').classList.toggle('hidden',!state.feishuEnabled);
    $('#local-access').open=!state.feishuEnabled;
    $('#login-lead').textContent=state.feishuEnabled?'使用公司飞书账号登录。首次加入需云邮箱管理员审核。':'使用团队账号进入邮件工作台。';
    $('#feishu-login').addEventListener('click',()=>startFeishu());
    $('#my-feishu-bind').addEventListener('click',()=>startFeishu(true));
    if(new URLSearchParams(location.search).has('login_error')) {
      $('#login-error').textContent='飞书登录未完成，请使用公司账号重试；绑定已有账号时请保持原账号登录。';
      $('#login-error').classList.remove('hidden');
      history.replaceState(null,'',location.pathname+location.hash);
    }
  } catch(e) { toast('登录配置暂时无法加载',true); }
}

export async function renderAdmission() {
  $('#app').classList.add('hidden');
  $('#login-layer').classList.add('hidden');
  const root=$('#admission-layer');
  root.classList.remove('hidden');
  root.innerHTML='<div class="login-card"><h1>加入 Xynigo 云邮箱</h1><p>正在核验申请状态…</p></div>';
  try {
    const a=await api('/api/auth/admission');
    const labels={new:'申请加入',pending:'等待管理员审核',approved:'申请已批准',rejected:'申请未通过',revoked:'账号已停用'};
    root.innerHTML=`<div class="login-card"><span class="eyebrow">XYNIGO 云邮箱</span><h1>${labels[a.status]||'申请状态'}</h1>
      <p>你好，${esc(a.name)}</p><p>云邮箱前期向主管、总监和管理员开放，由主管关注店铺邮件并转告本组运营。</p>
      <p class="muted">${a.status==='pending'?'管理员审核并分配邮箱后，即可进入工作台并接收对应邮件提醒。':a.status==='approved'?'你已获准加入，可以进入授权邮箱工作台。':'请提交加入申请，由 管理员审核并分配可见邮箱。'}</p>
      ${a.review_note?`<p class="notice">审核说明：${esc(a.review_note)}</p>`:''}
      ${['new','rejected'].includes(a.status)?'<label for="join-note">申请说明（选填）</label><textarea id="join-note" maxlength="300" rows="3" placeholder="请说明负责的店铺或工作内容"></textarea><button id="join-submit" class="btn btn-primary btn-block">提交加入申请</button>':''}
      ${a.status==='approved'?'<button id="join-enter" class="btn btn-primary btn-block">进入工作台</button>':'<button id="join-refresh" class="btn btn-block">刷新状态</button>'}
      <button id="join-logout" class="btn btn-block">退出当前飞书身份</button></div>`;
    $('#join-submit')?.addEventListener('click',async()=>{
      $('#join-submit').disabled=true;
      try {await api('/api/auth/admission',{method:'POST',body:{note:$('#join-note').value}});await renderAdmission();}
      catch(e){toast(e.message,true);if($('#join-submit'))$('#join-submit').disabled=false;}
    });
    $('#join-refresh')?.addEventListener('click',renderAdmission);
    $('#join-enter')?.addEventListener('click',async()=>{
      try {await api('/api/auth/admission/complete',{method:'POST'});
        const target=sessionStorage.getItem('xynigo_login_destination')||'/#/inbox';
        location.assign(/^\/#\/(inbox|dashboard|message\/\d+|event\/\d+|store\/\d+)$/.test(target)?target:'/#/inbox');
        location.reload();
      }catch(e){toast(e.message,true);}
    });
    $('#join-logout').addEventListener('click',async()=>{await api('/api/auth/logout',{method:'POST'});location.assign('/');});
  } catch(e) {root.innerHTML='<div class="login-card"><h1>请重新登录</h1><p>申请会话已过期。</p><a class="btn btn-primary" href="/">返回登录</a></div>';}
}

export async function loadAdmissions() {
  const box=$('#admissions-panel');
  if(!box)return;
  try {
    const {applications}=await api('/api/web/admissions');
    const pending=applications.filter(a=>a.status==='pending');
    box.innerHTML=`<div class="panel-title"><h3>加入申请 <span class="badge">${pending.length}</span></h3><button id="admissions-refresh" class="btn btn-sm">刷新</button></div>`+
      (pending.length?pending.map(a=>`<div class="admission-row"><div><strong>${esc(a.name)}</strong><p>${esc(a.request_note||'未填写说明')}</p><small class="muted">${esc(fmtTime(a.requested_at*1000))}</small></div><button class="btn btn-primary" data-review="${a.id}">审核并分配邮箱</button></div>`).join(''):'<p class="muted">暂无待审核申请</p>');
    $('#admissions-refresh').addEventListener('click',loadAdmissions);
    $$('[data-review]',box).forEach(b=>b.addEventListener('click',()=>reviewModal(pending.find(a=>a.id===Number(b.dataset.review)))));
  }catch(e){box.textContent='申请加载失败：'+e.message;}
}

async function reviewModal(a) {
  try {
    const {users}=await api('/api/web/users');
    const candidates=users.filter(u=>!u.is_superadmin&&u.enabled&&!u.feishu_bound);
    openModal(`<h3>审核 ${esc(a.name)}</h3><p>${esc(a.request_note||'未填写说明')}</p>
      <div class="field"><label for="join-existing">成员账号</label><select id="join-existing"><option value="">创建新的普通成员</option>${candidates.map(u=>`<option value="${u.id}">关联已有成员：${esc(personName(u))}（${esc(u.username)}）</option>`).join('')}</select><div class="hint">关联已有成员可保留其已读、收藏与处理记录。</div></div>
      <div class="field"><label for="join-role">成员角色</label><select id="join-role">${roleOptions()}</select></div>
      <div class="field"><label>授权邮箱 / 管理员个人提醒邮箱</label><div class="access-list">${state.accounts.map(acc=>`<label class="access-row"><input class="join-account" type="checkbox" value="${acc.id}"><span>${esc(storeName(acc))}<small> ${esc(acc.site)} · ${esc(acc.email)}</small></span></label>`).join('')}</div><div class="hint">前期面向主管、总监和管理员开放。按整邮箱授权和提醒；管理员仍可查看全部邮箱。</div></div>
      <div class="field"><label for="review-note">审核说明</label><textarea id="review-note" maxlength="300" rows="2"></textarea></div>
      <div class="actions"><button id="review-cancel" class="btn">取消</button><button id="review-reject" class="btn btn-danger">拒绝</button><button id="review-approve" class="btn btn-primary">批准并授权</button></div>`);
    $('#join-existing').addEventListener('change',()=>{
      const u=candidates.find(u=>u.id===Number($('#join-existing').value));
      $('#join-role').value=u?.role||'supervisor';
      $$('.join-account').forEach(c=>c.checked=!!u?.account_ids.includes(Number(c.value)));
    });
    $('#review-cancel').addEventListener('click',closeModal);
    async function decide(decision) {
      const existing=$('#join-existing').value;
      const body={decision,role:$('#join-role').value,note:$('#review-note').value,account_ids:$$('.join-account').filter(c=>c.checked).map(c=>Number(c.value))};
      if(existing)body.existing_user_id=Number(existing);
      $('#review-approve').disabled=$('#review-reject').disabled=true;
      try {await api(`/api/web/admissions/${a.id}/review`,{method:'POST',body});closeModal();toast(decision==='approve'?'已批准并分配邮箱':'已拒绝申请');location.reload();}
      catch(e){toast(e.message,true);$('#review-approve').disabled=$('#review-reject').disabled=false;}
    }
    $('#review-approve').addEventListener('click',()=>decide('approve'));
    $('#review-reject').addEventListener('click',()=>decide('reject'));
  }catch(e){toast(e.message,true);}
}

export function privateNotifyPanel(n) {
  const names={pending:'待发送',sending:'发送中',retry:'等待重试',sent:'已发送',failed:'失败',skipped:'已取消',blocked:'未绑定'};
  const counts=Object.entries(n.counts||{}).map(([k,v])=>`${names[k]||k} ${v}`).join(' · ')||'暂无通知记录';
  return `<h3>飞书个人收件提醒</h3><p class="muted">所有启用邮箱的新邮件，按整邮箱授权发送给主管、总监或管理员。主管核实重要内容后转告本组运营及时处理。</p>
    <label class="checkline"><input id="n-enabled" type="checkbox" ${n.enabled?'checked':''} ${n.configured?'':'disabled'}>开启全局个人提醒</label>
    <p class="small">应用 ${n.configured?'已配置':'未配置完整'} · 通知服务 ${n.worker_running?'运行中':'未启动'}</p>
    <p class="hint">开启后从当前时间起提醒新邮件，历史邮件不补推。每次发送前重新检查邮箱权限。</p>
    ${n.unbound_users?.length?`<p class="notice">以下授权成员尚未绑定飞书，暂时无法接收提醒：${n.unbound_users.map(u=>esc(personName(u))).join('、')}</p>`:''}
    <div class="actions"><button class="btn" id="n-test">向我的飞书发送测试</button><button class="btn btn-primary" id="n-save">保存设置</button></div>
    <h4>通知投递记录</h4><p class="small">${esc(counts)}</p>
    <div class="notify-history">${(n.deliveries||[]).map(d=>`<div class="admission-row"><div><strong>${esc(d.display_name)} → ${esc(personName(d))}</strong><small> ${esc(fmtTime(d.created_at*1000))} · ${esc(d.last_error||'')}</small></div><span class="badge">${names[d.status]||esc(d.status)}</span></div>`).join('')||'<p class="muted">暂无投递记录</p>'}</div>`;
}
