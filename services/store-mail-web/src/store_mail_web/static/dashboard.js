import { $, $$, api, closeDrawer, esc, fmtTime, isAdmin, can, personName, state, storeName, toast } from "./core.js";
import { openMessage } from "./inbox.js";

/* ---------- 店铺看板 ---------- */

async function renderDashboard() {
  const main = $("#main");
  main.innerHTML = `
    <div class="page-head">
      <div><span class="eyebrow">OPERATIONS OVERVIEW</span><h2>店铺看板</h2>
        <div class="sub">从邮件线索识别风险，明确负责人和下一步动作。</div></div>
      <button class="btn" id="dash-refresh">↻ 刷新看板</button>
    </div>
    <div id="dash-content"><div class="panel empty">正在汇总店铺邮件信号…</div></div>`;
  $("#dash-refresh").addEventListener("click", loadDashboard);
  await loadDashboard();
}

const RISK_LABELS = {
  account: "账号安全", performance: "账号绩效", compliance: "侵权与合规",
  listing: "Listing", fba: "FBA", finance: "资金", customer: "售后",
  ads: "广告与公告", review: "待核对",
};

const eventStoreName = (event) => event.account_chinese_name || event.account_display_name;
const eventPlatformName = (event) => event.account_chinese_name
  ? `（${event.account_display_name}）` : "";

function dueLabel(iso) {
  if (!iso) return "无内部时限";
  const minutes = Math.round((new Date(iso) - new Date()) / 60000);
  if (minutes < 0) return `已逾期 ${Math.ceil(-minutes / 60)} 小时`;
  if (minutes < 60) return `剩余 ${minutes} 分钟`;
  return `剩余 ${Math.ceil(minutes / 60)} 小时`;
}

function riskPill(cell) {
  const labels = { critical: "立即处理", warning: "需跟进", clear: "未见信号", unknown: "监控中断" };
  return `<span class="risk-pill ${esc(cell.state)}"><i></i>${labels[cell.state] || "待核对"}${cell.count ? `<b>${cell.count}</b>` : ""}</span>`;
}

function financeRows(items) {
  return items.map((e) => `
    <button class="finance-row" data-event="${e.id}"><span class="finance-dot ${e.finance_state === "平台已发起" ? "normal" : "risk"}"></span>
      <span><b>${esc(e.subtype)}</b>${e.amount_text ? `<strong>${esc(e.amount_text)}</strong>` : ""}
        <small>${esc(eventStoreName(e))}${esc(eventPlatformName(e))} · ${esc(fmtTime(e.last_seen_at))}</small>
        ${e.finance_state === "平台已发起" ? `<small class="receipt-state ${e.bank_receipt_status === "已核对到账" ? "confirmed" : "pending"}">${esc(e.bank_receipt_status || "待核对到账")} · 以银行记录为准</small>` : ""}
      </span><span class="row-arrow">↗</span></button>`).join("");
}

function financeGroups(items) {
  const groups = [
    ["扣款失败", (e) => e.finance_state === "扣款失败"],
    ["冻结、预留与回款失败", (e) => ["冻结/预留", "回款失败"].includes(e.finance_state)],
    ["平台发起回款", (e) => e.finance_state === "平台已发起"],
    ["其他资金线索", (e) => !["扣款失败", "冻结/预留", "回款失败", "平台已发起"].includes(e.finance_state)],
  ];
  const html = groups.map(([label, matches]) => {
    const rows = items.filter(matches).slice(0, 8);
    return rows.length ? `<div class="finance-group"><div class="finance-group-title">${label}<span>${rows.length}</span></div>${financeRows(rows)}</div>` : "";
  }).join("");
  return html || '<div class="empty">暂无资金类邮件线索</div>';
}

async function loadDashboard() {
  const box = $("#dash-content");
  if (!box) return;
  try {
    const data = await api("/api/web/dashboard");
    state.dashboard = data;
    drawDashboard();
  } catch (e) {
    box.innerHTML = `<div class="panel empty">加载失败：${esc(e.message)}</div>`;
  }
}

function drawDashboard() {
  const data = state.dashboard;
  const box = $("#dash-content");
  if (!data || !box) return;
  const s = data.summary || {};
  const cards = [
    ["critical", "最高风险", s.critical_open, "01", "需立即处理"],
    ["due", "临近期限", s.due_soon, "02", "即将或已经逾期"],
    ["listing", "Listing 异常", s.listing_open, "03", "影响商品可售"],
    ["fba", "FBA 异常", s.fba_open, "04", "货件与库存"],
    ["finance", "资金异常", s.finance_open, "05", "扣款与预留"],
    ["sync", "监控中断", s.sync_interrupted, "06", "需恢复邮件源"],
  ];
  const sites = [...new Set((data.matrix || []).map((x) => x.account.site).filter(Boolean))].sort();
  const totalOpen = (data.matrix || []).reduce((sum, row) => sum + (row.open_count || 0), 0);
  const matrix = (data.matrix || []).filter((x) =>
    (!state.dashboardSite || x.account.site === state.dashboardSite) &&
    (state.dashboardFocus !== "sync" || x.sync_interrupted));
  const queue = (data.queue || []).filter((e) => {
    if (state.dashboardSite && e.site !== state.dashboardSite) return false;
    const f = state.dashboardFocus;
    if (f === "critical") return e.severity === "P0";
    if (f === "due") return e.due_at && new Date(e.due_at) - new Date() <= 3600000;
    if (["listing", "fba", "finance"].includes(f)) return e.event_type === f;
    if (f === "sync") return false;
    return true;
  });
  const finance = (data.finance || []).filter((e) => !state.dashboardSite || e.site === state.dashboardSite);
  const awaitingReceipt = finance.filter((e) => e.finance_state === "平台已发起" && e.bank_receipt_status !== "已核对到账").length;
  box.innerHTML = `
    <section class="overview-hero">
      <div class="hero-copy"><span class="eyebrow light">LIVE MAIL SIGNALS · ${sites.length} MARKETPLACES</span>
        <h3>${totalOpen ? `当前有 <em>${totalOpen}</em> 项待处理信号` : "当前队列暂无待处理信号"}</h3>
        <p>把邮件中的风险、负责人和跟进时限集中到一个工作视图。</p>
        <div class="hero-facts"><span>${(data.matrix || []).length} 家店铺</span><span>${sites.length} 个站点</span><span>仅依据已同步邮件</span></div>
      </div>
    </section>
    <div class="scope-note"><span class="scope-icon">i</span><span>邮件是线索，未见异常不代表账号健康。处理要求与期限请到卖家后台核实。</span></div>
    <div class="section-heading"><div><span class="eyebrow">SIGNAL SNAPSHOT</span><h3>需要关注</h3></div><span class="section-caption">点击指标筛选处理队列</span></div>
    <div class="risk-strip">${cards.map(([key, label, value, index, hint]) => `
      <button class="risk-card ${state.dashboardFocus === key ? "selected" : ""} ${key}"
              data-focus="${key}" aria-pressed="${state.dashboardFocus === key}">
        <span class="risk-card-top"><span>${label}</span><i>${index}</i></span><strong>${value || 0}</strong>
        <small>${hint}</small></button>`).join("")}</div>
    <div class="board-columns" id="queue-anchor">
      <section class="panel work-panel"><div class="panel-title"><div><span class="eyebrow">ACTION QUEUE</span><h3>优先处理</h3></div><span class="count-label">${queue.length === 100 ? "显示前 100 项" : `${queue.length} 项待办`}</span></div>
        ${queue.length ? `<div class="event-list">${queue.slice(0, 50).map((e) => `
          <button class="event-row" data-event="${e.id}">
            <span class="severity ${esc(e.severity.toLowerCase())}">${esc(e.severity)}</span>
            <span class="event-main"><b>${esc(e.subtype)}</b><small>${esc(eventStoreName(e))}${esc(eventPlatformName(e))} <span class="inline-dot">·</span> ${esc(e.site || "站点待设置")}${e.entity_ref ? ` <span class="inline-dot">·</span> ${esc(e.entity_type)} ${esc(e.entity_ref)}` : ""}</small>
              <small class="event-subject">${esc(e.title)}</small></span>
            <span class="event-side"><b>${esc(e.owner_display_name || e.owner_name || "待分配")}</b><small>${esc(e.status)}</small>
              <small class="due-text ${e.due_at && new Date(e.due_at) < new Date() ? "danger-text" : ""}">${esc(dueLabel(e.due_at))}</small></span><span class="row-arrow">↗</span>
          </button>`).join("")}</div>` : (state.dashboardFocus === "sync"
            ? `<div class="empty">失联邮箱已列在下方店铺矩阵。${isAdmin() ? '<a href="#/accounts">前往邮箱账号恢复连接 →</a>' : "请联系管理员恢复连接。"}</div>`
            : '<div class="empty">当前筛选下没有待处理事件。可以切换指标或打开店铺时间线。</div>')}
      </section>
      <aside class="panel finance-panel"><div class="panel-title"><div><span class="eyebrow">MONEY MOVEMENT</span><h3>资金邮件</h3></div><span class="count-label">${awaitingReceipt} 笔待核对到账</span></div>
        <div class="finance-list">${financeGroups(finance)}</div>
      </aside>
    </div>
    <section class="matrix-section" id="matrix-anchor"><div class="section-heading board-toolbar">
      <div><span class="eyebrow">STORE MATRIX</span><h3>店铺矩阵</h3><span class="section-caption">按店铺与站点查看邮件风险和同步状态</span><span class="matrix-swipe">左右滑动查看全部信号 →</span></div>
      <label class="select-label">站点 <select id="dash-site"><option value="">全部站点</option>
        ${sites.map((site) => `<option value="${esc(site)}" ${site === state.dashboardSite ? "selected" : ""}>${esc(site)}</option>`).join("")}
      </select></label>
    </div>
    <div class="panel matrix-wrap">${matrix.length ? `<table class="list matrix"><thead><tr>
      <th>店铺 / 站点</th><th>账号风险</th><th>Listing</th><th>FBA</th><th>资金</th><th>售后</th>
      <th>未结待办</th><th>最后成功同步</th></tr></thead><tbody>
      ${matrix.map((row) => `<tr class="clickable" data-store="${row.account.id}" tabindex="0" role="button" aria-label="查看店铺 ${esc(storeName(row.account))} 的事件时间线">
        <td><b>${esc(storeName(row.account))}</b><div class="row-sub">${row.account.chinese_name ? `${esc(row.account.display_name)} <span class="inline-dot">·</span> ` : ""}${esc(row.account.site || "站点待设置")} <span class="inline-dot">·</span> ${esc(row.account.email)}</div></td>
        <td>${riskPill(row.account_risk)}</td><td>${riskPill(row.listing)}</td>
        <td>${riskPill(row.fba)}</td><td>${riskPill(row.finance)}</td>
        <td>${riskPill(row.customer)}</td><td><span class="open-count ${row.open_count ? "has-open" : ""}">${row.open_count}</span></td>
        <td><span class="${row.sync_interrupted ? "danger-text" : ""}">${row.sync_interrupted ? "监控中断" : esc(fmtTime(row.account.last_success_at))}</span>${row.sync_interrupted ? `<div class="row-sub">上次成功 ${esc(fmtTime(row.account.last_success_at))}</div>` : ""}</td>
      </tr>`).join("")}</tbody></table>` : '<div class="empty">当前站点下没有店铺。请调整站点筛选。</div>'}</div>
    </section>`;
  $("#dash-site").addEventListener("change", (ev) => {
    state.dashboardSite = ev.target.value; drawDashboard();
  });
  $$("[data-focus]", box).forEach((el) => el.addEventListener("click", () => {
    state.dashboardFocus = el.dataset.focus;
    drawDashboard();
    $(state.dashboardFocus === "sync" ? "#matrix-anchor" : "#queue-anchor").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  $$("[data-store]", box).forEach((el) => {
    const open = () => { location.hash = `#/store/${el.dataset.store}`; };
    el.addEventListener("click", open);
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); }
    });
  });
  $$("[data-event]", box).forEach((el) => el.addEventListener("click", () => openRiskEvent(Number(el.dataset.event))));
}

async function renderStoreSignals(accountId) {
  const main = $("#main");
  main.innerHTML = '<div class="panel empty">加载店铺邮件事件…</div>';
  try {
    const data = await api(`/api/web/stores/${accountId}/signals`);
    const a = data.account;
    main.innerHTML = `<div class="page-head store-page-head"><div><span class="eyebrow">STORE SIGNAL HISTORY</span>
      <h2>${esc(storeName(a))} <span class="site-tag">${esc(a.site || "站点待设置")}</span></h2>
      <div class="sub">${a.chinese_name ? `${esc(a.display_name)} · ` : ""}${esc(a.email)} · 最近成功同步 ${esc(fmtTime(a.last_success_at))}</div></div><button class="btn" id="store-back">← 返回看板</button></div>
      <div class="scope-note">同一 ASIN、SKU、货件的重复通知合并展示；每封原邮件均可打开。实际处理以卖家后台为准。</div>
      ${(data.groups || []).some((g) => g.entity_ref) ? `<div class="panel" style="margin-bottom:16px"><div class="panel-title"><h3>关联实体</h3><span>按编号追踪</span></div>
        <div class="entity-grid">${data.groups.filter((g) => g.entity_ref).map((g) => `<button class="entity-card" data-event="${g.events[0].id}">
          <b>${esc(g.entity_type)} ${esc(g.entity_ref)}</b><small>${g.events.length} 项事件 · ${g.events.reduce((n, e) => n + e.message_count, 0)} 封邮件</small></button>`).join("")}</div></div>` : ""}
      <div class="panel"><div class="panel-title"><h3>事件时间线</h3><span>${data.events.length} 项</span></div>
      ${(data.events || []).length ? `<div class="event-list">${data.events.map((e) => `
        <button class="event-row" data-event="${e.id}"><span class="severity ${esc(e.severity.toLowerCase())}">${esc(e.severity)}</span>
          <span class="event-main"><b>${esc(e.subtype)}</b><small>${esc(e.entity_type || "")} ${esc(e.entity_ref || "")} · ${esc(e.title)}</small></span>
          <span class="event-side"><b>${esc(e.status)}</b><small>${e.message_count} 封原邮件</small><small>${esc(fmtTime(e.last_seen_at))}</small></span>
        </button>`).join("")}</div>` : '<div class="empty">暂无识别到的邮件事件。普通邮件仍可在收件箱查看。</div>'}</div>`;
    $("#store-back").addEventListener("click", () => { location.hash = "#/dashboard"; });
    $$("[data-event]", main).forEach((el) => el.addEventListener("click", () => openRiskEvent(Number(el.dataset.event))));
  } catch (e) {
    main.innerHTML = `<div class="panel empty">加载失败：${esc(e.message)}</div>`;
  }
}

async function openRiskEvent(eventId) {
  const drawer = $("#drawer");
  $("#drawer-mask").classList.remove("hidden"); drawer.classList.remove("hidden");
  drawer.innerHTML = '<div class="drawer-body"><div class="empty">加载事件…</div></div>';
  try {
    const { event: e } = await api(`/api/web/risk-events/${eventId}`);
    const assignees = await api(`/api/web/stores/${e.account_id}/assignees`);
    drawer.innerHTML = `<div class="drawer-head"><div class="risk-drawer-top"><span class="severity ${esc(e.severity.toLowerCase())}">${esc(e.severity)}</span>
      <span class="risk-pill ${e.archived ? "unknown" : "warning"}">${e.archived ? "历史邮件" : esc(e.status)}</span></div>
      <h3>${esc(e.subtype)}</h3><div class="meta"><span>${esc(eventStoreName(e))}${esc(eventPlatformName(e))} · ${esc(e.site || "站点待设置")} · ${esc(RISK_LABELS[e.event_type] || e.event_type)}</span>
      <span>${esc(e.entity_type || "")} ${esc(e.entity_ref || "")} · ${esc(fmtTime(e.last_seen_at))}</span>
      <span>判定依据：${esc(e.basis)} · 来源：${esc(e.source_trust)}</span>
      ${e.deadline_text ? `<span>邮件提及期限：${esc(e.deadline_text)}（请到卖家后台核实）</span>` : ""}
      <span>内部跟进目标：${esc(e.due_at ? fmtTime(e.due_at) : "无")} ${e.due_at ? `(${esc(dueLabel(e.due_at))})` : ""}</span></div></div>
      <div class="drawer-body risk-drawer-body">
        <div class="risk-section"><h4>原始邮件 · ${e.messages.length}</h4>${e.messages.map((m) => `<button class="mail-link" data-mail="${m.id}">
          <b>${esc(m.subject || "(无主题)")}</b><small>${esc(fmtTime(m.received_at))} · ${esc(m.from_addr)}</small></button>`).join("")}</div>
        <div class="risk-section"><h4>处理</h4>
          <label>负责人 <select id="risk-owner" ${can("events.assign") ? "" : "disabled"}><option value="">待分配</option>${(assignees.users || []).map((u) =>
            `<option value="${u.id}" ${u.id === e.owner_user_id ? "selected" : ""}>${esc(personName(u))}（${esc(u.username)}）</option>`).join("")}</select></label>
          ${can("events.update") && !can("events.assign") && !e.owner_user_id ? '<button class="btn" id="risk-claim">认领给我</button>' : ""}
          <label>状态 <select id="risk-status" ${can("events.update") ? "" : "disabled"}>${["待处理", "处理中", "已完成", "误报", "待核对"].map((v) =>
            `<option value="${v}" ${v === e.status ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          ${e.finance_state === "平台已发起" ? `<label>银行到账核对 <select id="risk-bank-receipt" ${can("finance.confirm") ? "" : "disabled"}>
            <option value="待核对到账" ${e.bank_receipt_status !== "已核对到账" ? "selected" : ""}>待核对到账</option>
            <option value="已核对到账" ${e.bank_receipt_status === "已核对到账" ? "selected" : ""}>已人工核对到账</option>
          </select></label><div class="row-sub">确认到账时请在备注中填写银行核对依据；邮件不能证明实际到账。</div>` : ""}
          <label>处理备注 <textarea id="risk-note" ${can("events.update") ? "" : "disabled"} rows="3" maxlength="2000" placeholder="记录在卖家后台核实的事实、处理动作与结果"></textarea></label>
          <button class="btn btn-primary" id="risk-save" ${can("events.update") ? "" : "disabled"}>保存处理记录</button>
        </div>
        <div class="risk-section"><h4>处理轨迹</h4>${e.activity.map((a) => `<div class="activity-row"><b>${esc(a.full_name || a.username || "系统")}</b>
          <span>${esc(a.action)} · ${esc(fmtTime(a.created_at))}</span><p>${esc(a.detail)}</p></div>`).join("")}</div>
      </div><div class="drawer-actions"><button class="btn" id="risk-close">关闭</button></div>`;
    $("#risk-close").addEventListener("click", closeDrawer);
    $$("[data-mail]", drawer).forEach((el) => el.addEventListener("click", () => openMessage(Number(el.dataset.mail))));
    const claim = $("#risk-claim");
    if (claim) claim.addEventListener("click", async () => {
      try {
        await api(`/api/web/risk-events/${eventId}/update`, {
          method: "POST", body: { owner_user_id: state.user.id, version: e.version },
        });
        toast("已认领"); await openRiskEvent(eventId);
        if (location.hash.startsWith("#/store/")) renderStoreSignals(e.account_id);
        else loadDashboard();
      } catch (err) { toast(err.message, true); }
    });
    $("#risk-save").addEventListener("click", async () => {
      const body = { status: $("#risk-status").value, note: $("#risk-note").value.trim(), version: e.version };
      const receipt = $("#risk-bank-receipt");
      if (receipt && !receipt.disabled && receipt.value !== (e.bank_receipt_status || "待核对到账")) body.bank_receipt_status = receipt.value;
      const owner = $("#risk-owner").value;
      if (owner !== String(e.owner_user_id || "")) body.owner_user_id = owner ? Number(owner) : null;
      try {
        await api(`/api/web/risk-events/${eventId}/update`, { method: "POST", body });
        toast("处理记录已保存");
        await openRiskEvent(eventId);
        if (location.hash.startsWith("#/store/")) renderStoreSignals(e.account_id);
        else loadDashboard();
      } catch (err) { toast(err.message, true); }
    });
  } catch (e) {
    drawer.innerHTML = `<div class="drawer-body empty">加载失败：${esc(e.message)}</div><div class="drawer-actions"><button class="btn" id="risk-close">关闭</button></div>`;
    $("#risk-close").addEventListener("click", closeDrawer);
  }
}


export { renderDashboard, loadDashboard, renderStoreSignals, openRiskEvent };
