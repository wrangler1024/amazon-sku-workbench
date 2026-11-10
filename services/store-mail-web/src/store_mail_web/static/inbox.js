import { $, $$, FLAG_META, accountName, api, closeDrawer, esc, flagIcons, fmtSize, fmtTime, state, storeName, toast } from "./core.js";

/* ---------- 收件箱 ---------- */

async function renderInbox() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">UNIFIED INBOX</span><h2>聚合收件箱</h2><div class="sub">全部店铺邮件，在同一处检索与核对。</div></div>
      <button class="btn" id="inbox-refresh">↻ 刷新列表</button>
    </div>
    <div class="panel inbox-filter-panel">
      <div class="inbox-filter-heading"><div><span class="eyebrow">FIND A MESSAGE</span><h3>筛选邮件</h3></div><span>资金与货件可同时勾选</span></div>
      <div class="filter-bar">
      <select id="f-account" aria-label="选择店铺邮箱">
        <option value="">全部账号</option>
        ${state.accounts.map((a) =>
          `<option value="${a.id}" ${String(a.id) === state.inbox.account_id ? "selected" : ""}>` +
          `${esc(storeName(a))}${a.chinese_name ? `（${esc(a.display_name)}）` : ""} · ${esc(a.email)}</option>`).join("")}
      </select>
      <div class="search-field"><span aria-hidden="true">⌕</span><input type="search" id="f-q" placeholder="搜索主题、发件人或摘要"
             value="${esc(state.inbox.q)}"><button class="btn btn-primary" id="inbox-search">搜索</button></div>
      <select id="f-category" aria-label="选择邮件分类">
        <option value="">全部分类</option>
        ${["告警","侵权","绩效","扣款","回款","货件","客户","广告","其他"].map((c) =>
          `<option ${c === state.inbox.category ? "selected" : ""}>${c}</option>`).join("")}
      </select>
      <select id="f-flag" aria-label="选择邮件标记">
        <option value="">全部标记</option>
        <option value="any" ${state.inbox.flag === "any" ? "selected" : ""}>任意标记</option>
        ${Object.keys(FLAG_META).map((f) =>
          `<option value="${f}" ${state.inbox.flag === f ? "selected" : ""}>${FLAG_META[f].icon} ${FLAG_META[f].label}</option>`).join("")}
      </select>
      </div>
      <div class="filter-bottom"><span class="filter-caption">快捷筛选</span>
        <div class="filter-chips">
          <label class="check"><input type="checkbox" id="f-important" ${state.inbox.important_only ? "checked" : ""}>重要</label>
          <label class="check"><input type="checkbox" id="f-amazon" ${state.inbox.amazon_only ? "checked" : ""}>亚马逊</label>
          <label class="check"><input type="checkbox" id="f-finance" ${state.inbox.finance_only ? "checked" : ""}>资金</label>
          <label class="check"><input type="checkbox" id="f-shipment" ${state.inbox.shipment_only ? "checked" : ""}>货件</label>
          <label class="check"><input type="checkbox" id="f-unread" ${state.inbox.unread_only ? "checked" : ""}>未读</label>
        </div><button class="text-button" id="inbox-clear">清空筛选</button>
      </div>
    </div>
    <div class="panel inbox-results"><div class="panel-title"><div><span class="eyebrow">MESSAGE LIST</span><h3>邮件列表</h3></div><div class="inbox-result-actions"><span class="count-label" id="inbox-sub">加载中…</span><button class="text-button" id="inbox-read-all" title="把当前可见的全部邮件标为已读(仅影响自己)">全部标为已读</button></div></div>
      <div id="inbox-list"><div class="empty">正在加载邮件…</div></div></div>`;

  const apply = () => {
    state.inbox.account_id = $("#f-account").value;
    state.inbox.q = $("#f-q").value.trim();
    state.inbox.category = $("#f-category").value;
    state.inbox.flag = $("#f-flag").value;
    state.inbox.important_only = $("#f-important").checked;
    state.inbox.amazon_only = $("#f-amazon").checked;
    state.inbox.finance_only = $("#f-finance").checked;
    state.inbox.shipment_only = $("#f-shipment").checked;
    state.inbox.unread_only = $("#f-unread").checked;
    state.inbox.page = 0;
    loadInboxList();
  };
  $("#f-account").addEventListener("change", apply);
  $("#f-category").addEventListener("change", apply);
  $("#f-flag").addEventListener("change", apply);
  $("#f-q").addEventListener("keydown", (ev) => { if (ev.key === "Enter") apply(); });
  $("#inbox-search").addEventListener("click", apply);
  $("#inbox-clear").addEventListener("click", () => {
    state.inbox = { ...state.inbox, account_id: "", q: "", category: "", flag: "",
      amazon_only: false, finance_only: false, shipment_only: false,
      unread_only: false, important_only: false, page: 0 };
    renderInbox();
  });
  ["f-important", "f-amazon", "f-finance", "f-shipment", "f-unread"].forEach((id) =>
    $("#" + id).addEventListener("change", apply));
  $("#inbox-refresh").addEventListener("click", () => loadInboxList());
  $("#inbox-read-all").addEventListener("click", async () => {
    const btn = $("#inbox-read-all");
    btn.disabled = true;
    try {
      const r = await api("/api/web/messages/mark-all-read", { method: "POST" });
      toast(`已把 ${r.marked} 封邮件标为已读`);
      state.inbox.page = 0;
      loadInboxList();
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; }
  });
  await loadInboxList();
}

function inboxQuery() {
  const f = state.inbox;
  const params = new URLSearchParams();
  if (f.account_id) params.set("account_id", f.account_id);
  if (f.q) params.set("q", f.q);
  if (f.category) params.set("category", f.category);
  if (f.flag) params.set("flag", f.flag);
  if (f.important_only) params.set("important_only", "1");
  if (f.amazon_only) params.set("amazon_only", "1");
  if (f.finance_only) params.set("finance_only", "1");
  if (f.shipment_only) params.set("shipment_only", "1");
  if (f.unread_only) params.set("unread_only", "1");
  params.set("limit", String(f.limit));
  params.set("offset", String(f.page * f.limit));
  return params.toString();
}

async function loadInboxList() {
  const box = $("#inbox-list");
  if (!box) return;
  try {
    const data = await api("/api/web/messages?" + inboxQuery());
    state.inboxTotal = data.total;
    const pages = Math.max(1, Math.ceil(data.total / state.inbox.limit));
    $("#inbox-sub").textContent =
      `${data.total} 封邮件 · 第 ${state.inbox.page + 1}/${pages} 页`;
    const rows = (data.messages || []).map((m) => `
      <tr class="clickable ${m.unread ? "mail-unread" : "mail-read"}" data-id="${m.id}" tabindex="0" role="button" aria-label="${m.unread ? "未读" : "已读"}邮件：${esc(m.subject || "无主题")}">
        <td><span class="mail-sender-avatar">${esc((m.from_name || m.from_addr || "?").slice(0, 1).toUpperCase())}</span><span class="mail-sender-name">${esc(m.from_name || m.from_addr)}</span></td>
        <td><span class="mail-subject ${m.unread ? "subject-unread" : ""}">${esc(m.subject || "(无主题)")}${m.unread ? '<span class="unread-label">未读</span>' : ""}</span>${flagIcons(m.flags)}
          <div class="mail-preview">${esc(m.preview || "")}</div></td>
        <td><span class="mail-store" title="${esc((state.accounts.find((a) => a.id === m.account_id) || {}).display_name || "")}">${esc(accountName(m.account_id))}</span></td>
        <td><span class="badge cat-${esc(m.category)}">${esc(m.category)}</span>${m.amount_text ? `<span class="badge info">${esc(m.amount_text)}</span>` : ""}</td>
        <td><span class="mail-time">${esc(fmtTime(m.received_at))}</span>${m.unread ? '<span class="unread-dot" aria-hidden="true"></span>' : ""}</td>
      </tr>`).join("");
    box.innerHTML = rows
      ? `<div class="inbox-table-scroll"><table class="list inbox-table"><thead><tr>
           <th>发件人</th><th>主题与摘要</th><th>店铺</th><th>分类</th><th>时间</th>
         </tr></thead><tbody>${rows}</tbody></table>
         </div>
         <div class="pager">
           <button class="btn btn-sm" id="pg-prev" ${state.inbox.page === 0 ? "disabled" : ""}>上一页</button>
           <span>第 ${state.inbox.page + 1} / ${pages} 页</span>
           <button class="btn btn-sm" id="pg-next" ${state.inbox.page + 1 >= pages ? "disabled" : ""}>下一页</button>
         </div>`
      : `<div class="empty">没有符合条件的邮件</div>`;
    $$("#inbox-list tbody tr").forEach((tr) => {
      tr.addEventListener("click", () => openMessage(Number(tr.dataset.id)));
      tr.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openMessage(Number(tr.dataset.id)); }
      });
    });
    const prev = $("#pg-prev"), next = $("#pg-next");
    if (prev) prev.addEventListener("click", () => { state.inbox.page--; loadInboxList(); });
    if (next) next.addEventListener("click", () => { state.inbox.page++; loadInboxList(); });
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败:${esc(e.message)}</div>`;
  }
}

/* ---------- 邮件详情抽屉 ---------- */

async function openMessage(id) {
  const drawer = $("#drawer");
  $("#drawer-mask").classList.remove("hidden");
  drawer.classList.remove("hidden");
  drawer.innerHTML = `<div class="drawer-body"><div class="empty">加载中…</div></div>`;
  try {
    const data = await api(`/api/web/messages/${id}`);
    const m = data.message;
    const canRenderHtml = Boolean(m.has_html && m.body_html);
    const hasExternalResources = canRenderHtml && (
      /<(?:img|link|table|td)\b[^>]*\b(?:src|srcset|href|background)\s*=\s*["']?\s*https?:/i.test(m.body_html) ||
      /(?:url\(\s*["']?|@import\s+["']?)https?:/i.test(m.body_html)
    );
    drawer.innerHTML = `
      <div class="drawer-head">
        <h3>${esc(m.subject || "(无主题)")}</h3>
        <div class="meta">
          <span>店铺:${esc(m.account_chinese_name || m.account_display_name || "")}${m.account_chinese_name ? `（${esc(m.account_display_name)}）` : ""} · ${esc(m.account_email || "")}</span>
          <span>发件人:${esc(m.from_name || "")} &lt;${esc(m.from_addr)}&gt;</span>
          <span>时间:${esc(m.received_at)} · ${fmtSize(m.size_bytes)}</span>
          ${m.html_truncated ? '<span class="mail-truncated">HTML 正文已达到存储上限，展示可能不完整。</span>' : ""}
          <span>
            ${m.is_amazon ? '<span class="badge amazon">Amazon</span>' : ""}
            <span class="badge cat-${esc(m.category)}">${esc(m.category)}</span>
            ${m.amount_text ? `<span class="badge info">${esc(m.amount_text)}</span>` : ""}
          </span>
        </div>
      </div>
      <div class="drawer-body mail-body" id="mail-body"></div>
      <div class="drawer-actions">
        ${Object.keys(FLAG_META).map((f) => `
        <button class="btn btn-flag flag-${f} ${m.flags && m.flags.includes(f) ? "active" : ""}"
                id="d-flag-${f}" title="${FLAG_META[f].label}">
          ${FLAG_META[f].icon} ${FLAG_META[f].label}</button>`).join("")}
        ${canRenderHtml ? '<button class="btn" id="d-toggle-html">查看纯文本</button>' : ""}
        <button class="btn" id="d-unread">标为未读</button>
        <button class="btn btn-primary" id="d-close">关闭</button>
      </div>`;
    $("#d-close").addEventListener("click", closeDrawer);
    Object.keys(FLAG_META).forEach((f) => {
      const btn = $(`#d-flag-${f}`);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        const on = !m.flags.includes(f);
        try {
          const r = await api(`/api/web/messages/${id}/flags`, {
            method: "POST", body: { flag: f, on },
          });
          m.flags = r.flags || [];
          btn.classList.toggle("active", m.flags.includes(f));
          toast(`${FLAG_META[f].label}${on ? "已添加" : "已移除"}`);
          loadInboxList();
        } catch (e) { toast(e.message, true); }
      });
    });
    $("#d-unread").addEventListener("click", async () => {
      try {
        await api(`/api/web/messages/${id}/read`, { method: "POST", body: { unread: true } });
        toast("已标为未读");
        loadInboxList();
      } catch (e) { toast(e.message, true); }
    });

    // 邮件 HTML 从独立受限响应加载，保留布局而不让邮件接触工作台 DOM。
    const body = $("#mail-body");
    const toggle = $("#d-toggle-html");
    let htmlMode = canRenderHtml;
    let remoteAllowed = true;
    const renderText = () => {
      htmlMode = false;
      body.classList.remove("mail-body-html");
      body.innerHTML = `<pre class="body-text">${esc(m.body_text || "(无正文)")}</pre>`;
      if (toggle) toggle.textContent = "查看 HTML";
    };
    const renderHtml = () => {
      htmlMode = true;
      body.classList.add("mail-body-html");
      body.innerHTML = "";
      if (hasExternalResources) {
        const bar = document.createElement("div");
        bar.className = "mail-resource-bar" + (remoteAllowed ? "" : " blocked");
        const status = document.createElement("span");
        status.textContent = remoteAllowed
          ? "已允许加载 HTTPS 图片与样式，发件方可能得知您打开了邮件。"
          : "外部图片与样式已拦截。";
        const button = document.createElement("button");
        button.type = "button";
        button.className = "mail-resource-toggle";
        button.textContent = remoteAllowed ? "停止加载外部资源" : "显示图片与样式";
        button.addEventListener("click", () => {
          remoteAllowed = !remoteAllowed;
          renderHtml();
        });
        bar.append(status, button);
        body.appendChild(bar);
      }
      const mobileHint = document.createElement("div");
      mobileHint.className = "mail-mobile-hint";
      mobileHint.textContent = "左右滑动可查看邮件的完整排版。";
      body.appendChild(mobileHint);
      const iframe = document.createElement("iframe");
      iframe.className = "html-view";
      iframe.title = "邮件原始 HTML 正文";
      iframe.setAttribute("sandbox", "");
      iframe.setAttribute("referrerpolicy", "no-referrer");
      iframe.src = `/api/web/messages/${id}/html?remote=${remoteAllowed ? "1" : "0"}`;
      body.appendChild(iframe);
      if (toggle) toggle.textContent = "查看纯文本";
    };
    if (canRenderHtml) renderHtml();
    else renderText();
    if (toggle) toggle.addEventListener("click", () => {
      if (htmlMode) renderText();
      else renderHtml();
    });
    loadInboxList(); // 刷新未读状态
  } catch (e) {
    drawer.innerHTML = `<div class="drawer-body">
      <div class="empty">加载失败:${esc(e.message)}</div></div>
      <div class="drawer-actions"><button class="btn btn-primary" id="d-close">关闭</button></div>`;
    $("#d-close").addEventListener("click", closeDrawer);
  }
}


export { renderInbox, openMessage };
