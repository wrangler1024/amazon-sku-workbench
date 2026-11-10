import { $, $$, api, esc, state, toast } from './core.js';
export const ROLE_NAMES={operator:'运营专员',supervisor:'运营主管',director:'运营总监',admin:'管理员'};
export function roleOptions(selected='supervisor') {
  return Object.entries(ROLE_NAMES).filter(([key])=>key!=='operator'||selected==='operator').map(([key,name])=>`<option value="${key}" ${key===selected?'selected':''}>${name}</option>`).join('');
}
export async function renderRoles() {
  const main=$('#main');
  main.innerHTML='<div class="page-head"><div><span class="eyebrow">ROLE PERMISSIONS</span><h2>角色功能权限</h2><div class="sub">功能按角色开放，三类运营角色的邮件范围仍由成员邮箱授权决定。</div></div></div><div id="roles-body" class="panel admin-panel">正在加载…</div>';
  try {
    const data=await api('/api/web/roles'), box=$('#roles-body');
    box.innerHTML=`<p class="role-help">管理员可管理全局。成员管理、加入审批和角色设置为管理员固定权限；超级管理员保留全部权限。</p>
      <div class="role-table-scroll"><table class="list role-table"><thead><tr><th>功能</th>${data.roles.map(r=>`<th>${esc(r.name)}<small>${r.role==='admin'?'全局管理':'按邮箱授权'}</small></th>`).join('')}</tr></thead><tbody>
      ${data.permissions.map(p=>`<tr><td><strong>${esc(p.name)}</strong><small>${esc(p.description)}</small></td>${data.roles.map(r=>{
        const locked=(p.admin_only&&r.role!=='admin')||(p.locked_admin&&r.role==='admin');
        return `<td><input type="checkbox" class="role-permission" data-role="${r.role}" data-permission="${p.key}" aria-label="${esc(r.name+'：'+p.name)}" ${r.permissions.includes(p.key)?'checked':''} ${locked?'disabled':''}></td>`;
      }).join('')}</tr>`).join('')}
      </tbody><tfoot><tr><td>按角色单独保存</td>${data.roles.map(r=>`<td><button class="btn btn-primary btn-sm" data-save-role="${r.role}">保存${esc(r.name)}</button></td>`).join('')}</tr></tfoot></table></div>
      <p class="hint">改动保存后立即用于服务端鉴权。停用前置功能会同步取消依赖它的功能；运营角色不能获得全局管理功能。</p>`;
    const catalog=Object.fromEntries(data.permissions.map(p=>[p.key,p]));
    function input(role,key){return $(`.role-permission[data-role="${role}"][data-permission="${key}"]`);}
    function enable(role,key){const el=input(role,key);el.checked=true;(catalog[key].requires||[]).forEach(k=>enable(role,k));}
    function disable(role,key){input(role,key).checked=false;data.permissions.filter(p=>p.requires.includes(key)).forEach(p=>disable(role,p.key));}
    $$('.role-permission',box).forEach(el=>el.addEventListener('change',()=>el.checked?enable(el.dataset.role,el.dataset.permission):disable(el.dataset.role,el.dataset.permission)));
    $$('[data-save-role]',box).forEach(btn=>btn.addEventListener('click',async()=>{
      const key=btn.dataset.saveRole, role=data.roles.find(r=>r.role===key);
      const selected=$$(`.role-permission[data-role="${key}"]`).filter(e=>e.checked).map(e=>e.dataset.permission);
      btn.disabled=true;
      try {
        await api('/api/web/roles/'+key,{method:'PUT',body:{permissions:selected,version:role.version}});
        toast(role.name+'权限已保存');
        // Current role's permission changes must refresh navigation as well.
        if(state.user.role===key&&!state.user.is_superadmin)location.reload();
        else await renderRoles();
      } catch(e){toast(e.message,true);btn.disabled=false;}
    }));
  } catch(e){$('#roles-body').textContent='加载失败：'+e.message;}
}
