import { roleOptions } from "./roles.js";
import { loadAdmissions, privateNotifyPanel } from "./feishu.js";
import { $, $$, api, closeModal, copyText, esc, fmtTime, loadAccounts, openModal, personName, refreshSyncMeta, state, storeName, toast } from "./core.js";

/* ---------- 邮箱账号 ---------- */

async function renderAccounts() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">MAIL SOURCES</span><h2>邮箱账号</h2><div class="sub">维护每家店铺的邮箱连接、站点和默认负责人。</div></div>
      <button class="btn btn-primary" id="acc-add">＋ 添加账号</button>
    </div>
    <div class="panel admin-panel"><div class="panel-title"><div><span class="eyebrow">CONNECTED STORES</span><h3>已接入邮箱</h3></div><span class="count-label" id="accounts-sub"></span></div><div id="accounts-table"><div class="empty">加载中…</div></div></div>`;
  $("#acc-add").addEventListener("click", () => accountModal(null));
  await loadAccountsTable();
}

async function loadAccountsTable() {
  const box = $("#accounts-table");
  if (!box) return;
  try {
    await loadAccounts();
    $("#accounts-sub").textContent =
      `共 ${state.accounts.length} 个,启用 ${state.accounts.filter((a) => a.enabled).length} 个`;
    const rows = state.accounts.map((a) => {
      const status = a.last_sync_status
        ? `<span class="badge status-${esc(a.last_sync_status)}">${
            { ok: "正常", error: "同步失败", auth_error: "授权失败" }[a.last_sync_status] || esc(a.last_sync_status)
          }</span>` : '<span class="badge status-skipped">未同步</span>';
      return `
      <tr>
        <td><b>${esc(storeName(a))}</b><div class="row-sub">${a.chinese_name ? `${esc(a.display_name)} · ` : ""}${esc(a.site || "站点待设置")} · 负责人 ${esc(a.owner_display_name || a.owner_name || "未分配")}</div>${a.last_error ? `<div class="row-sub" title="${esc(a.last_error)}">${esc(String(a.last_error).slice(0, 60))}</div>` : ""}</td>
        <td>${esc(a.email)}</td>
        <td>${status}<div class="row-sub">最近尝试 ${esc(fmtTime(a.last_sync_at))}</div><div class="row-sub">最近成功 ${esc(fmtTime(a.last_success_at))}</div></td>
        <td class="num">${a.message_count} 封 / 未读 ${a.unread_count}</td>
        <td>${a.enabled ? '<span class="badge status-ok">启用</span>' : '<span class="badge status-skipped">停用</span>'}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm" data-act="sync" data-id="${a.id}">同步</button>
          <button class="btn btn-sm" data-act="test" data-id="${a.id}">测试</button>
          <button class="btn btn-sm" data-act="edit" data-id="${a.id}">编辑</button>
          <button class="btn btn-sm" data-act="toggle" data-id="${a.id}">${a.enabled ? "停用" : "启用"}</button>
          <button class="btn btn-sm btn-danger" data-act="del" data-id="${a.id}">删除</button>
        </td>
      </tr>`;
    }).join("");
    box.innerHTML = rows
      ? `<table class="list"><thead><tr>
           <th>店铺</th><th>邮箱</th><th>同步状态</th><th>邮件</th><th>状态</th><th>操作</th>
         </tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty">还没有账号。点右上「添加账号」接入第一个 163 邮箱。</div>`;
    $$("#accounts-table [data-act]").forEach((btn) =>
      btn.addEventListener("click", () => accountAction(btn.dataset.act, Number(btn.dataset.id), btn)));
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败:${esc(e.message)}</div>`;
  }
}

async function accountAction(act, id, btn) {
  const account = state.accounts.find((a) => a.id === id);
  if (!account) return;
  try {
    if (act === "sync") {
      btn.disabled = true; btn.textContent = "同步中…";
      const data = await api(`/api/web/accounts/${id}/sync`, { method: "POST" });
      const r = (data.results || [])[0] || {};
      if (r.status === "ok") toast(`${storeName(account)}:新邮件 ${r.new_messages} 封`);
      else toast(`${storeName(account)}:${r.error || r.status}`, r.status === "skipped");
    } else if (act === "test") {
      btn.disabled = true; btn.textContent = "测试中…";
      const r = await api(`/api/web/accounts/${id}/test`, { method: "POST" });
      if (r.ok) toast(`${storeName(account)}:连接正常,收件箱 ${r.exists} 封`);
      else toast(`${storeName(account)}:${r.error}`, true);
    } else if (act === "edit") {
      accountModal(account);
    } else if (act === "toggle") {
      await api(`/api/web/accounts/${id}`, {
        method: "PUT", body: { enabled: !account.enabled },
      });
      toast(account.enabled ? "已停用" : "已启用");
    } else if (act === "del") {
      if (!confirm(`确定删除账号「${storeName(account)}(${account.email})」?\n本地已同步的邮件也会一并删除,不影响邮箱本身。`)) return;
      await api(`/api/web/accounts/${id}`, { method: "DELETE" });
      toast("已删除");
    }
  } catch (e) {
    toast(e.message, true);
  } finally {
    loadAccountsTable();
    refreshSyncMeta();
  }
}

async function accountModal(account) {
  const isEdit = !!account;
  let users = [];
  try { users = (await api("/api/web/users")).users || []; } catch (e) { /* 可稍后配置 */ }
  openModal(`
    <h3>${isEdit ? "编辑账号" : "添加 163 邮箱账号"}</h3>
    <div class="field"><label>平台店铺名</label>
      <input id="m-name" type="text" maxlength="64" value="${esc(account ? account.display_name : "")}"
             placeholder="亚马逊前台显示的店铺名"></div>
    <div class="field"><label>内部中文名称（可选）</label>
      <input id="m-chinese-name" type="text" maxlength="64" value="${esc(account ? account.chinese_name || "" : "")}" placeholder="如：美西一店">
      <div class="hint">内部页面优先显示中文名称；不填写时显示平台店铺名。</div></div>
    <div class="field"><label>邮箱地址</label>
      <input id="m-email" type="text" value="${esc(account ? account.email : "")}"
             placeholder="xxx@163.com"></div>
    <div class="field"><label>站点</label>
      <select id="m-site"><option value="">待设置</option>${["CA", "MX", "UK", "US", "AU", "DE", "FR", "IT", "ES", "JP"].map((site) =>
        `<option value="${site}" ${account && account.site === site ? "selected" : ""}>${site}</option>`).join("")}</select></div>
    <div class="field"><label>默认负责人</label>
      <select id="m-owner"><option value="">待分配</option>${users.filter((u) => u.enabled).map((u) =>
        `<option value="${u.id}" ${account && account.owner_user_id === u.id ? "selected" : ""}>${esc(personName(u))}（${esc(u.username)}）</option>`).join("")}</select>
      <div class="hint">新识别的重要邮件默认交给此人；事件详情可单独改派。</div></div>
    <div class="field"><label>IMAP 授权码 ${isEdit ? "(留空 = 不修改)" : ""}</label>
      <input id="m-code" type="password" autocomplete="new-password"
             placeholder="163 网页版设置的客户端授权码,不是登录密码">
      <div class="hint">在 163 邮箱网页版「设置 - POP3/SMTP/IMAP」里开启服务并生成授权码。</div></div>
    <div class="field"><label>IMAP 服务器(高级,一般不改)</label>
      <div style="display:flex;gap:10px">
        <input id="m-host" type="text" value="${esc(account ? account.imap_host : "imap.163.com")}" class="grow">
        <input id="m-port" type="number" value="${account ? account.imap_port : 993}" style="width:90px">
      </div></div>
    <div class="actions">
      <button class="btn" id="m-cancel">取消</button>
      <button class="btn btn-primary" id="m-save">${isEdit ? "保存" : "添加并启用"}</button>
    </div>`);
  $("#m-cancel").addEventListener("click", closeModal);
  $("#m-save").addEventListener("click", async () => {
    const body = {
      display_name: $("#m-name").value.trim(),
      chinese_name: $("#m-chinese-name").value.trim(),
      email: $("#m-email").value.trim(),
      auth_code: $("#m-code").value,
      imap_host: $("#m-host").value.trim() || "imap.163.com",
      imap_port: Number($("#m-port").value) || 993,
      site: $("#m-site").value,
      owner_user_id: $("#m-owner").value ? Number($("#m-owner").value) : null,
    };
    if (!body.display_name || !body.email) { toast("店铺名称和邮箱必填", true); return; }
    if (!isEdit && !body.auth_code) { toast("新增账号必须填授权码", true); return; }
    if (isEdit && !body.auth_code) delete body.auth_code;
    try {
      if (isEdit) await api(`/api/web/accounts/${account.id}`, { method: "PUT", body });
      else await api("/api/web/accounts", { method: "POST", body });
      toast(isEdit ? "已保存" : "已添加,后台将自动进行首次同步");
      closeModal();
      loadAccountsTable();
    } catch (e) {
      toast(e.message, true);
    }
  });
}

/* ---------- 用户管理(admin) ---------- */

async function renderUsers() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">TEAM ACCESS</span><h2>团队成员</h2>
        <div class="sub">按邮箱分配可见范围。每位成员的已读状态独立保存。</div></div>
      <button class="btn btn-primary" id="user-add">＋ 新建子账号</button>
    </div>
    <div id="admissions-panel" class="panel" style="padding:18px;margin-bottom:16px"></div>
    <div class="panel admin-panel"><div class="panel-title"><div><span class="eyebrow">ACCESS CONTROL</span><h3>成员与权限</h3></div><span class="count-label">按角色配置功能，按邮箱授权数据</span></div><div id="users-table"><div class="empty">加载中…</div></div></div>`;
  $("#user-add").addEventListener("click", () => userModal(null));
  await loadUsersTable();
  if (state.feishuEnabled) await loadAdmissions();
  else $("#admissions-panel").classList.add("hidden");
}

async function loadUsersTable() {
  const box = $("#users-table");
  if (!box) return;
  try {
    const data = await api("/api/web/users");
    await loadAccounts();
    const rows = (data.users || []).map((u) => {
      const stores = u.account_ids.map((id) => {
        const acc = state.accounts.find((a) => a.id === id);
        return acc ? storeName(acc) : "#" + id;
      }).join("、") || "—";
      return `
        <tr>
          <td><b>${esc(personName(u))}</b>${` <span class="badge amazon">${u.is_superadmin ? "超级管理员" : esc(u.role_name)}</span>`}<div class="row-sub">登录名：${esc(u.username)}</div><small>${u.feishu_bound ? `飞书已绑定：${esc(u.feishu_name)}` : "飞书未绑定"}</small></td>
          <td>${u.enabled ? '<span class="badge status-ok">启用</span>' : '<span class="badge status-skipped">停用</span>'}</td>
          <td class="small">${u.role === "admin" ? `全部邮箱<div class="row-sub">个人提醒：${esc(stores)}</div>` : esc(stores)}</td>
          <td class="small">${esc(fmtTime(u.last_login_at))}</td>
          <td class="small">${esc(fmtTime(u.created_at))}</td>
          <td style="white-space:nowrap">
            ${u.is_superadmin ? (state.user.is_superadmin ? `<button class="btn btn-sm" data-act="edit" data-id="${u.id}">编辑</button>` : `<span class="muted">超级管理员</span>`) : `
            <button class="btn btn-sm" data-act="edit" data-id="${u.id}">编辑</button>
            <button class="btn btn-sm" data-act="toggle" data-id="${u.id}">${u.enabled ? "停用" : "启用"}</button>
            <button class="btn btn-sm btn-danger" data-act="del" data-id="${u.id}">删除</button>`}
          </td>
        </tr>`;
    }).join("");
    box.innerHTML = rows
      ? `<table class="list"><thead><tr>
           <th>姓名 / 登录名</th><th>状态</th><th>可见邮箱 / 提醒范围</th><th>最近登录</th><th>创建</th><th>操作</th>
         </tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty">暂无用户</div>`;
    $$("#users-table [data-act]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const id = Number(btn.dataset.id);
        const user = (data.users || []).find((u) => u.id === id);
        try {
          if (btn.dataset.act === "edit") {
            userModal(user);
          } else if (btn.dataset.act === "toggle") {
            await api(`/api/web/users/${id}`, {
              method: "PUT", body: { enabled: !user.enabled },
            });
            toast(user.enabled ? "已停用" : "已启用");
          } else if (btn.dataset.act === "del") {
            if (!confirm(`确定删除子账号「${personName(user)}（${user.username}）」?其已读记录一并删除。`)) return;
            await api(`/api/web/users/${id}`, { method: "DELETE" });
            toast("已删除");
          }
        } catch (e) { toast(e.message, true); }
        loadUsersTable();
      }));
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败:${esc(e.message)}</div>`;
  }
}

function userModal(user) {
  const isEdit = !!user;
  if (user && user.is_superadmin) {
    openModal(`
      <h3>管理员姓名与个人提醒</h3>
      <div class="field"><label for="u-full-name">姓名</label>
        <input id="u-full-name" type="text" maxlength="64" value="${esc(user.full_name || "")}" placeholder="请输入姓名">
        <div class="hint">登录名 ${esc(user.username)} 与管理员密码仍由服务器配置管理。</div></div>
      <div class="field"><label>接收个人提醒的邮箱</label><div class="access-list">${state.accounts.map(a => `<label class="access-row"><input class="admin-notify-account" type="checkbox" value="${a.id}" ${user.account_ids.includes(a.id) ? "checked" : ""}><span>${esc(storeName(a))}<small> ${esc(a.email)}</small></span></label>`).join("")}</div><div class="hint">管理员仍可查看全部邮箱；这里仅选择需要接收飞书提醒的邮箱。</div></div>
      <div class="actions"><button class="btn" id="u-cancel">取消</button><button class="btn btn-primary" id="u-save">保存</button></div>`);
    $("#u-cancel").addEventListener("click", closeModal);
    $("#u-save").addEventListener("click", async () => {
      const fullName = $("#u-full-name").value.trim();
      if (!fullName) { toast("姓名必填", true); return; }
      try {
        await api(`/api/web/users/${user.id}`, { method: "PUT", body: { full_name: fullName, account_ids: $$(".admin-notify-account").filter(c => c.checked).map(c => Number(c.value)) } });
        if (state.user && state.user.id === user.id) {
          state.user.full_name = fullName;
          $("#whoami strong").textContent = fullName;
          $("#user-avatar").textContent = fullName.slice(0, 1).toUpperCase();
        }
        toast("管理员设置已保存"); closeModal(); loadUsersTable();
      } catch (e) { toast(e.message, true); }
    });
    return;
  }
  const checked = new Set(user ? user.account_ids : []);
  const bySite = new Map();
  state.accounts.forEach((account) => {
    const site = "店铺邮箱";
    if (!bySite.has(site)) bySite.set(site, []);
    bySite.get(site).push(account);
  });
  const siteOrder = ["CA", "MX", "UK", "US", "AU"];
  const groups = [...bySite.entries()].sort((a, b) => {
    const ai = siteOrder.indexOf(a[0]), bi = siteOrder.indexOf(b[0]);
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a[0].localeCompare(b[0]);
  }).map(([site, accounts]) => `
    <section class="access-site" data-site="${esc(site)}">
      <div class="access-site-head"><strong>${esc(site)} <span>${accounts.length} 家</span></strong>
        <button type="button" class="access-site-toggle" data-site="${esc(site)}">选择全部</button></div>
      <div class="access-site-rows">${accounts.sort((a, b) => storeName(a).localeCompare(storeName(b), "zh-CN")).map((account) => `
        <label class="access-row" data-search="${esc(`${storeName(account)} ${account.display_name} ${account.email} ${site}`.toLowerCase())}">
          <input type="checkbox" class="u-acc" value="${account.id}" ${checked.has(account.id) ? "checked" : ""}>
          <span class="access-row-copy"><strong>${esc(storeName(account))}</strong>
            <small>${account.chinese_name ? `${esc(account.display_name)} · ` : ""}${esc(account.email)}</small></span>
        </label>`).join("")}</div>
    </section>`).join("");
  openModal(`
    <h3>${isEdit ? `编辑成员 ${esc(personName(user))}` : "新建子账号"}</h3>
    <div class="field"><label for="u-full-name">姓名</label>
      <input id="u-full-name" type="text" maxlength="64" value="${esc(user ? user.full_name || "" : "")}" placeholder="如：张三"></div>
    ${isEdit ? "" : `
    <div class="field"><label for="u-name">用户名（登录用）</label>
      <input id="u-name" type="text" maxlength="32" placeholder="如 kefu01(字母/数字/._-)"></div>`}
    ${isEdit ? `<div class="field"><div class="hint">登录名：${esc(user.username)}</div></div>` : ""}
    <div class="field"><label for="u-role">成员角色</label><select id="u-role" ${user && user.id === state.user.id ? "disabled" : ""}>${roleOptions(user ? user.role : "supervisor")}</select></div>
    <div class="field"><label for="u-pass">${isEdit ? "重置密码（留空则不修改）" : "初始密码（至少 6 位）"}</label>
      <input id="u-pass" type="password" autocomplete="new-password" placeholder="告诉使用者,由管理员保管"></div>
    <div class="field"><div class="access-title"><label for="u-acc-search">可见邮箱 / 管理员个人提醒邮箱</label><strong id="u-selected-count">已选 0 家</strong></div>
      <div class="access-toolbar"><input id="u-acc-search" type="search" placeholder="搜索中文名、平台名或邮箱" aria-label="搜索可见邮箱">
        <button type="button" class="access-clear" id="u-clear">清空已选</button></div>
      <div class="access-list">${groups || '<div class="access-empty">还没有邮箱账号，请先到「邮箱账号」添加。</div>'}
        <div class="access-empty hidden" id="u-search-empty">没有匹配的店铺</div></div>
      <div class="hint">按整邮箱授权，主管负责核实重要邮件并转告本组运营；搜索不会清除已选邮箱。管理员可查看全部邮箱，此处仅决定管理员的个人提醒范围。</div></div>
    <div class="actions">
      <button class="btn" id="u-cancel">取消</button>
      <button class="btn btn-primary" id="u-save">${isEdit ? "保存" : "创建"}</button>
    </div>`);
  $("#modal").classList.add("user-access-modal");
  const updateSelection = () => {
    const selected = $$(".u-acc").filter((input) => input.checked);
    $("#u-selected-count").textContent = `已选 ${selected.length} / ${state.accounts.length} 家`;
    $$(".access-row").forEach((row) => row.classList.toggle("selected", $(".u-acc", row).checked));
    $$(".access-site").forEach((group) => {
      const inputs = $$(".u-acc", group);
      $(".access-site-toggle", group).textContent = inputs.length && inputs.every((input) => input.checked)
        ? "取消全部" : "选择全部";
    });
  };
  $$(".u-acc").forEach((input) => input.addEventListener("change", updateSelection));
  $$(".access-site-toggle").forEach((button) => button.addEventListener("click", () => {
    const group = button.closest(".access-site");
    const inputs = $$(".u-acc", group);
    const select = !inputs.every((input) => input.checked);
    inputs.forEach((input) => { input.checked = select; });
    updateSelection();
  }));
  $("#u-clear").addEventListener("click", () => {
    $$(".u-acc").forEach((input) => { input.checked = false; });
    updateSelection();
  });
  $("#u-acc-search").addEventListener("input", (event) => {
    const query = event.target.value.trim().toLowerCase();
    $$(".access-row").forEach((row) => { row.hidden = !row.dataset.search.includes(query); });
    $$(".access-site").forEach((group) => {
      group.hidden = !$$(".access-row", group).some((row) => !row.hidden);
    });
    $("#u-search-empty").classList.toggle("hidden", !$$(".access-site").every((group) => group.hidden));
  });
  updateSelection();
  $("#u-cancel").addEventListener("click", closeModal);
  $("#u-save").addEventListener("click", async () => {
    const accountIds = $$(".u-acc").filter((c) => c.checked)
      .map((c) => Number(c.value));
    const body = { role: $("#u-role").value, account_ids: accountIds, full_name: $("#u-full-name").value.trim() };
    if (!body.full_name) { toast("姓名必填", true); return; }
    const pass = $("#u-pass").value;
    if (isEdit) {
      if (pass) body.password = pass;
    } else {
      body.username = $("#u-name").value.trim();
      body.password = pass;
      if (!body.username || !body.password) {
        toast("用户名和初始密码必填", true); return;
      }
    }
    try {
      if (isEdit) await api(`/api/web/users/${user.id}`, { method: "PUT", body });
      else await api("/api/web/users", { method: "POST", body });
      toast(isEdit ? "已保存" : "子账号已创建,可把用户名和密码发给使用者");
      closeModal();
      loadUsersTable();
    } catch (e) { toast(e.message, true); }
  });
}

/* ---------- API Key ---------- */

async function renderKeys() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">AGENT ACCESS</span><h2>Agent 接口</h2>
        <div class="sub">管理内部 Agent 的 API Key、限流与调用状态。</div></div>
      <button class="btn btn-primary" id="key-add">＋ 新建 Key</button>
    </div>
    <div class="panel admin-panel"><div class="panel-title"><div><span class="eyebrow">API CREDENTIALS</span><h3>访问密钥</h3></div><span class="count-label">通过 X-API-Key 请求头使用</span></div><div id="keys-table"><div class="empty">加载中…</div></div></div>`;
  $("#key-add").addEventListener("click", () => keyModal());
  await loadKeysTable();
}

async function loadKeysTable() {
  const box = $("#keys-table");
  if (!box) return;
  try {
    const data = await api("/api/web/api-keys");
    const rows = (data.api_keys || []).map((k) => `
      <tr>
        <td><b>${esc(k.name)}</b><div class="row-sub">${esc(k.key_prefix)}…</div></td>
        <td>${k.enabled ? '<span class="badge status-ok">启用</span>' : '<span class="badge status-skipped">停用</span>'}</td>
        <td class="num">${k.rate_limit_per_min}/分钟</td>
        <td class="num">${k.request_count}</td>
        <td class="small">${esc(fmtTime(k.last_used_at))}</td>
        <td class="small">${esc(fmtTime(k.created_at))}</td>
        <td style="white-space:nowrap">
          <button class="btn btn-sm" data-act="toggle" data-id="${k.id}">${k.enabled ? "停用" : "启用"}</button>
          <button class="btn btn-sm btn-danger" data-act="del" data-id="${k.id}">删除</button>
        </td>
      </tr>`).join("");
    box.innerHTML = rows
      ? `<table class="list"><thead><tr><th>名称</th><th>状态</th><th>限流</th>
           <th>请求数</th><th>最近使用</th><th>创建</th><th>操作</th>
         </tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="empty">还没有 API Key。点「新建 Key」给 Agent 发第一张卡。</div>`;
    $$("#keys-table [data-act]").forEach((btn) => btn.addEventListener("click", async () => {
      const id = Number(btn.dataset.id);
      try {
        if (btn.dataset.act === "toggle") {
          const enabled = btn.textContent.trim() === "停用" ? false : true;
          await api(`/api/web/api-keys/${id}/enable`, { method: "POST", body: { enabled } });
          toast(enabled ? "已启用" : "已停用");
        } else {
          if (!confirm("确定删除该 API Key?使用它的 Agent 将立即失效。")) return;
          await api(`/api/web/api-keys/${id}`, { method: "DELETE" });
          toast("已删除");
        }
      } catch (e) { toast(e.message, true); }
      loadKeysTable();
    }));
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败:${esc(e.message)}</div>`;
  }
}

function keyModal() {
  openModal(`
    <h3>新建 API Key</h3>
    <div class="field"><label>名称(用途备注)</label>
      <input id="k-name" type="text" maxlength="64" placeholder="如:Gmail 管家 Agent"></div>
    <div class="field"><label>限流(次/分钟)</label>
      <input id="k-rate" type="number" min="1" max="10000" value="60"></div>
    <div class="actions">
      <button class="btn" id="k-cancel">取消</button>
      <button class="btn btn-primary" id="k-save">创建</button>
    </div>`);
  $("#k-cancel").addEventListener("click", closeModal);
  $("#k-save").addEventListener("click", async () => {
    try {
      const data = await api("/api/web/api-keys", {
        method: "POST",
        body: { name: $("#k-name").value.trim(), rate_limit_per_min: Number($("#k-rate").value) || 60 },
      });
      openModal(`
        <h3>Key 已创建</h3>
        <p>明文只显示这一次,请立即复制交给 Agent 使用:</p>
        <div class="key-reveal" id="key-plain">${esc(data.key)}</div>
        <div class="actions">
          <button class="btn" id="k-copy">复制</button>
          <button class="btn btn-primary" id="k-done">我已保存</button>
        </div>`);
      $("#k-copy").addEventListener("click", () => copyText(data.key, "Key 已复制"));
      $("#k-done").addEventListener("click", () => { closeModal(); loadKeysTable(); });
    } catch (e) {
      toast(e.message, true);
    }
  });
}

/* ---------- 设置 ---------- */

async function renderSettings() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head"><div><span class="eyebrow">WORKSPACE CONFIGURATION</span><h2>工作台设置</h2>
      <div class="sub">管理通知方式、识别规则和运行状态。</div></div></div>
    <div id="settings-body"><div class="empty">加载中…</div></div>`;
  const body = $("#settings-body");
  try {
    const [health, rules, notify] = await Promise.all([
      api("/healthz"), api("/api/web/rules"), api("/api/web/notify-config"),
    ]);
    body.innerHTML = `
      <div class="panel" style="padding:16px 18px;margin-bottom:16px">
        ${notify.mode === "private" ? privateNotifyPanel(notify) : `        <h3>飞书群机器人推送</h3>
        <div class="small muted" style="margin-bottom:10px">
          飞书群里「设置 → 群机器人 → 添加自定义机器人」拿到 Webhook 地址(及可选签名密钥)填到这里。
          首次识别的 P0/P1 待办进入群提醒；重复通知、普通邮件和正常回款不推。状态:
          <b>${notify.configured ? "已配置" : "未配置"}</b>${notify.configured ? (notify.worker_running ? " · 推送线程运行中" : " · 推送线程未启动") : ""}</div>
        <div class="field"><label>Webhook 地址</label>
          <input id="n-url" type="text" style="width:100%" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
                 value="${esc(notify.url)}"></div>
        <div class="field"><label>签名校验密钥(机器人未开签名可留空)</label>
          <input id="n-secret" type="password" style="width:100%" value="${esc(notify.secret)}"></div>
        <div class="field"><label>P1 推送分类(逗号分隔)</label>
          <input id="n-cats" type="text" style="width:100%" value="${esc(notify.push_categories)}">
          <div class="hint">P0 始终提醒。P1 可选:${["告警","侵权","绩效","扣款","回款","货件","客户","广告","其他"].join(" / ")};默认全部</div></div>
        <div style="display:flex;gap:10px;justify-content:flex-end">
          <button class="btn" id="n-test">发送测试消息</button>
          <button class="btn btn-primary" id="n-save">保存配置</button>
        </div>
`}
      </div>
      <div class="panel" style="padding:16px 18px;margin-bottom:16px">
        <h3>识别规则</h3>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
          <div class="small muted">改完规则后点「重新分类存量」,已入库邮件立即按新规则重跑(不用重新拉信)。</div>
          <button class="btn" id="r-reclassify">重新分类存量</button>
        </div>
        <div class="small muted" style="margin-bottom:10px">
          发件人或主题包含以下关键词(小写匹配)即标记为亚马逊邮件。每行一个。</div>
        <div style="display:flex;gap:16px;flex-wrap:wrap">
          <div style="flex:1;min-width:240px">
            <div class="small">发件人关键词</div>
            <textarea id="r-senders" rows="4">${esc(rules.amazon_rules.senders.join("\n"))}</textarea>
          </div>
          <div style="flex:1;min-width:240px">
            <div class="small">主题关键词(发件人未命中时)</div>
            <textarea id="r-subjects" rows="4">${esc(rules.amazon_rules.subject_keywords.join("\n"))}</textarea>
          </div>
        </div>
        <div class="small" style="margin:12px 0 6px">分类关键词(JSON 对象:分类 → 关键词数组)</div>
        <textarea id="r-categories" rows="12">${esc(JSON.stringify(rules.category_rules, null, 2))}</textarea>
        <div style="display:flex;justify-content:flex-end;margin-top:12px">
          <button class="btn btn-primary" id="r-save">保存规则</button>
        </div>
      </div>
      <div class="panel" style="padding:16px 18px">
        <h3>服务状态</h3>
        <div class="small">
          版本 ${esc(health.version)} · 状态 ${health.status === "ok" ? "正常" : health.status === "degraded" ? "部分功能未运行" : esc(health.status)} ·
          调度器 ${health.scheduler_running ? "运行中" : "关闭"} ·
          账号 ${health.accounts.enabled}/${health.accounts.total} 启用 ·
          邮件 ${health.messages} 封 · API Key ${health.api_keys_active} 个<br>
          OpenAPI 规范:<a href="/api/openapi.yaml" target="_blank">/api/openapi.yaml</a>
        </div>
      </div>`;
    $("#n-save").addEventListener("click", async () => {
      try {
        await api("/api/web/notify-config", {
          method: "PUT",
          body: notify.mode === "private" ? { enabled: $("#n-enabled").checked } : {
            url: $("#n-url").value.trim(),
            secret: $("#n-secret").value,
            push_categories: $("#n-cats").value.trim(),
          },
        });
        toast("推送配置已保存");
        renderSettings();
      } catch (e) { toast(e.message, true); }
    });
    $("#n-test").addEventListener("click", async () => {
      const btn = $("#n-test");
      btn.disabled = true; btn.textContent = "发送中…";
      try {
        const r = await api("/api/web/notify/test", { method: "POST" });
        if (r.ok) toast(notify.mode === "private" ? "测试消息已发送到你的个人飞书" : "测试消息已发出");
        else toast(`发送失败:${r.error}`, true);
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; btn.textContent = "发送测试消息"; }
    });
    $("#r-reclassify").addEventListener("click", async () => {
      const btn = $("#r-reclassify");
      btn.disabled = true; btn.textContent = "重新分类中…";
      try {
        const r = await api("/api/web/reclassify", { method: "POST" });
        toast(`重新分类完成:共 ${r.reclassified} 封`);
      } catch (e) { toast(e.message, true); }
      finally { btn.disabled = false; btn.textContent = "重新分类存量"; }
    });
    $("#r-save").addEventListener("click", async () => {
      let categories;
      try {
        categories = JSON.parse($("#r-categories").value);
      } catch (e) {
        toast("分类关键词不是合法 JSON", true);
        return;
      }
      const lines = (id) => $("#" + id).value.split("\n")
        .map((s) => s.trim()).filter(Boolean);
      try {
        await api("/api/web/rules", {
          method: "PUT",
          body: {
            amazon_rules: {
              senders: lines("r-senders"),
              subject_keywords: lines("r-subjects"),
            },
            category_rules: categories,
          },
        });
        toast("规则已保存,对后续同步的邮件生效");
      } catch (e) {
        toast(e.message, true);
      }
    });
  } catch (e) {
    body.innerHTML = `<div class="empty panel">加载失败:${esc(e.message)}</div>`;
  }
}


export { renderAccounts, renderUsers, renderKeys, renderSettings };
