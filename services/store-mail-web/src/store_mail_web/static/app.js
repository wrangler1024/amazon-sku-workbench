/* Xynigo 邮箱客户端 — 模块入口 */
import { $, $$, api, closeDrawer, closeModal, esc, isAdmin, can, loadAccounts, personName, refreshSyncMeta, showLogin, state, toast } from "./core.js";
import { renderDashboard, loadDashboard, renderStoreSignals, openRiskEvent } from "./dashboard.js";
import { renderInbox, openMessage } from "./inbox.js";
import { renderAccounts, renderUsers, renderKeys, renderSettings } from "./admin.js";

import { renderRoles } from "./roles.js";
import { setupFeishu, startFeishu, renderAdmission } from "./feishu.js";

async function boot() {
  $("#admission-layer").classList.add("hidden");
  $("#login-layer").classList.add("hidden");
  $("#app").classList.remove("hidden");
  applyRoleNav();
  try {
    await loadAccounts();
  } catch (e) { /* 已由 api() 处理 */ }
  route();
  refreshSyncMeta();
}

function applyRoleNav() {
  const admin = isAdmin();
  $("#my-feishu-bind").classList.toggle("hidden", !state.feishuEnabled || !!state.user?.feishu_bound);
  $$("#nav [data-permission]").forEach(a => a.classList.toggle("hidden", !can(a.dataset.permission)));
  $$("#nav .nav-label[data-admin]").forEach(a => a.classList.toggle("hidden", !admin));
  const syncAll = $("#sync-all-btn");
  if (syncAll) syncAll.classList.toggle("hidden", !admin || !can("mail.sync"));
  const who = $("#whoami");
  if (who && state.user) {
    who.innerHTML = `<strong>${esc(personName(state.user))}</strong><small>${esc(state.user.username)} · ${state.user.is_superadmin ? "超级管理员" : esc(state.user.role_name)}${admin ? "" : ` · ${state.user.account_ids.length} 个邮箱`}</small>`;
    $("#user-avatar").textContent = personName(state.user).slice(0, 1).toUpperCase();
  }
}

function bindLogin() {
  $("#login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const btn = $("#login-submit");
    btn.disabled = true;
    btn.textContent = "登录中…";
    const errBox = $("#login-error");
    errBox.classList.add("hidden");
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: {
          username: $("#login-user").value.trim(),
          password: $("#login-pass").value,
        },
      }).then((data) => { state.user = data.user || null; });
      $("#login-pass").value = "";
      await boot();
    } catch (e) {
      errBox.textContent = e.message || "登录失败";
      errBox.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "登录";
    }
  });

  $("#logout-btn").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
    state.user = null;
    showLogin();
  });

  $("#sync-all-btn").addEventListener("click", syncAll);
}

async function syncAll() {
  const btn = $("#sync-all-btn");
  btn.disabled = true;
  btn.textContent = "同步中…";
  try {
    const data = await api("/api/web/sync", { method: "POST", body: { force: true } });
    const results = data.results || [];
    const okCount = results.filter((r) => r.status === "ok").length;
    const newCount = results.reduce((sum, r) => sum + (r.new_messages || 0), 0);
    const skipped = results.filter((r) => r.status === "skipped");
    let msg = `同步完成:${okCount} 个账号,新邮件 ${newCount} 封`;
    if (skipped.length) msg += `;${skipped.length} 个跳过`;
    toast(msg);
    await loadAccounts();
    route();
    refreshSyncMeta();
  } catch (e) {
    toast(e.message || "同步失败", true);
  } finally {
    btn.disabled = false;
    btn.textContent = "↻ 同步全部";
  }
}

/* ---------- 路由 ---------- */

const VIEWS = { dashboard: renderDashboard, inbox: renderInbox,
                accounts: renderAccounts, keys: renderKeys, users: renderUsers,
                settings: renderSettings, roles: renderRoles };

const VIEW_PERMISSIONS = { inbox: "mail.read", dashboard: "dashboard.read", accounts: "accounts.manage", keys: "api_keys.manage", users: "members.manage", settings: "settings.manage", roles: "roles.manage" };
const VIEW_TITLES = { dashboard: "店铺看板", inbox: "聚合收件箱", accounts: "邮箱账号",
  users: "团队成员", roles: "角色权限", keys: "Agent 接口", settings: "工作台设置" };

function closeMobileNav() {
  $("#app").classList.remove("nav-open");
  $("#mobile-nav").setAttribute("aria-expanded", "false");
}

function route() {
  if (!state.user) return;
  const hash = (location.hash || "#/dashboard").replace(/^#\//, "");
  closeMobileNav();
  if (/^message\/\d+$/.test(hash) && can("mail.read")) {
    state.view = "inbox";
    $("#topbar-view").textContent = "邮件详情";
    renderInbox().then(() => openMessage(Number(hash.split("/")[1]))).catch(e => toast(e.message, true));
    return;
  }
  if (/^event\/\d+$/.test(hash) && can("dashboard.read")) {
    state.view = "dashboard";
    $("#topbar-view").textContent = "事件详情";
    $$("#nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === "dashboard"));
    renderDashboard().then(() => openRiskEvent(Number(hash.split("/")[1])));
    return;
  }
  if (/^store\/\d+$/.test(hash) && can("dashboard.read")) {
    state.view = "dashboard";
    $("#topbar-view").textContent = "店铺详情";
    $$("#nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.view === "dashboard"));
    closeDrawer(); closeModal();
    renderStoreSignals(Number(hash.split("/")[1]));
    return;
  }
  let view = VIEWS[hash] ? hash : "dashboard";
  if (!can(VIEW_PERMISSIONS[view])) view = Object.keys(VIEWS).find(v => can(VIEW_PERMISSIONS[v]));
  if (!view) { $("#main").innerHTML = '<div class="panel empty">当前角色暂无可用功能，请联系云邮箱管理员。</div>'; return; }
  state.view = view;
  $("#topbar-view").textContent = VIEW_TITLES[view] || "店铺看板";
  $$("#nav a").forEach((a) =>
    a.classList.toggle("active", a.dataset.view === view));
  closeDrawer();
  closeModal();
  VIEWS[view]();
}

/* ---------- 启动 ---------- */

async function init() {
  const environmentBadge = $("#environment-badge");
  environmentBadge.textContent = "独立服务";
  bindLogin();
  await setupFeishu();
  if (location.hash === "#/join") { await renderAdmission(); return; }
  $("#mobile-nav").addEventListener("click", () => {
    const open = $("#app").classList.toggle("nav-open");
    $("#mobile-nav").setAttribute("aria-expanded", String(open));
  });
  $("#nav-backdrop").addEventListener("click", closeMobileNav);
  $("#drawer-mask").addEventListener("click", closeDrawer);
  $("#modal-mask").addEventListener("click", closeModal);
  window.addEventListener("hashchange", route);
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") { closeDrawer(); closeModal(); closeMobileNav(); }
  });
  // 看板仅在页面可见时自动刷新。
  setInterval(() => {
    if (state.user && can("dashboard.read") && state.view === "dashboard" && document.visibilityState === "visible") {
      loadDashboard();
    }
  }, 60000);
  try {
    const data = await api("/api/auth/me");
    state.user = data.user || null;
    await boot();
  } catch (e) {
    showLogin();
  }
}

init();
