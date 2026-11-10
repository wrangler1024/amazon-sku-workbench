/* Xynigo 邮箱客户端 — 前端单页应用(无构建、无框架) */
"use strict";

/* ---------- 基础工具 ---------- */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const hm = `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  return d.getFullYear() === now.getFullYear() ? hm : `${d.getFullYear()}-${hm}`;
}

function fmtSize(bytes) {
  if (!bytes) return "—";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1048576).toFixed(1) + " MB";
}

function toast(message, isError) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = message;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), isError ? 5200 : 3200);
}

function copyText(text, tip) {
  const done = () => toast(tip || "已复制");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); done(); }
  catch (e) { toast("复制失败,请手动选择", true); }
  ta.remove();
}

/* ---------- API 封装 ---------- */

class ApiFail extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function api(path, opts) {
  opts = opts || {};
  const init = {
    method: opts.method || "GET",
    credentials: "same-origin",
    headers: { "X-Requested-With": "fetch" },
  };
  if (opts.body !== undefined) {
    init.body = JSON.stringify(opts.body);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (e) { /* 空响应 */ }
  if (res.status === 401 && !path.startsWith("/api/auth/login")) {
    showLogin();
    throw new ApiFail("unauthorized", "未登录或会话已过期");
  }
  if (!res.ok) {
    const err = (data && data.error) || { code: "error", message: "HTTP " + res.status };
    throw new ApiFail(err.code, err.message, res.status);
  }
  return data;
}

/* ---------- 全局状态 ---------- */

const state = {
  accounts: [],
  user: null,          // 当前登录用户 {id, username, role, account_ids}
  view: "dashboard",
  inbox: { account_id: "", q: "", category: "", flag: "",
           amazon_only: false, finance_only: false, shipment_only: false,
           unread_only: false,
           important_only: false, page: 0, limit: 50 },
  inboxTotal: 0,
  dashboard: null,
  dashboardFocus: "all",
  dashboardSite: "",
};

const FLAG_META = {
  star: { icon: "⭐", label: "收藏" },
  flag: { icon: "🚩", label: "小红旗" },
  pin:  { icon: "📌", label: "信标" },
};

function flagIcons(flags) {
  return (flags || []).map((f) =>
    `<span class="flag-ic flag-${esc(f)}" title="${esc(FLAG_META[f] ? FLAG_META[f].label : f)}"></span>`).join("");
}

function isAdmin() {
  return !!state.user && state.user.role === "admin";
}

function can(permission) {
  return !!state.user && (state.user.permissions || []).includes(permission);
}

function accountName(id) {
  const acc = state.accounts.find((a) => a.id === Number(id));
  return acc ? storeName(acc) : "账号#" + id;
}

function storeName(account) {
  return account.chinese_name || account.display_name;
}

function personName(user) {
  return user.full_name || user.username;
}

function showLogin() {
  $("#login-layer").classList.remove("hidden");
  $("#app").classList.add("hidden");
  const err = $("#login-error");
  if (err && !err.textContent) err.classList.add("hidden");
}

async function loadAccounts() {
  const data = await api("/api/web/accounts");
  state.accounts = data.accounts || [];
}

async function refreshSyncMeta() {
  try {
    const data = await api("/api/web/sync/status");
    const accounts = data.accounts || [];
    const enabled = accounts.filter((a) => a.enabled);
    const latest = enabled.map((a) => a.last_success_at).filter(Boolean).sort().pop();
    const staleAfterMs = Math.max(900, (data.interval_seconds || 180) * 3) * 1000;
    const interrupted = enabled.filter((a) =>
      a.last_sync_status !== "ok" || !a.last_success_at ||
      Date.now() - new Date(a.last_success_at).getTime() > staleAfterMs).length;
    $("#sync-meta").textContent = `${data.scheduler_running ? "定时同步运行中" : "定时同步未运行"}${latest ? ` · 最近成功 ${fmtTime(latest)}` : " · 尚无成功同步"}`;
    const indicator = $("#source-indicator");
    const attention = interrupted > 0 || !data.scheduler_running;
    indicator.classList.toggle("attention", attention);
    $(".source-card").classList.toggle("attention", attention);
    $("span", indicator).textContent = interrupted ? `${interrupted} 个账号连接异常` : (data.scheduler_running ? "邮件同步运行中" : "定时同步未运行");
  } catch (e) { /* 静默 */ }
}

function closeDrawer() {
  const drawer = $("#drawer");
  drawer.classList.add("hidden");
  drawer.replaceChildren();
  $("#drawer-mask").classList.add("hidden");
}

/* ---------- 模态 ---------- */

function openModal(innerHtml) {
  $("#modal").innerHTML = innerHtml;
  $("#modal").classList.remove("hidden");
  $("#modal-mask").classList.remove("hidden");
}
function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal").classList.remove("user-access-modal");
  $("#modal-mask").classList.add("hidden");
  $("#modal").innerHTML = "";
}


export { $, $$, esc, fmtTime, fmtSize, toast, copyText, api, state, FLAG_META, flagIcons, isAdmin, can, accountName, storeName, personName, showLogin, loadAccounts, refreshSyncMeta, closeDrawer, openModal, closeModal };
