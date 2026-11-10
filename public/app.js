import {
  MARKETS,
  MARKET_NAMES,
  CURRENCIES,
  EXAMPLE_PRICES,
  convertUnit,
  makeRequest,
  lowPricePresentation,
  formatMoney,
  formatWeight,
  tierLabel,
  planLabel,
  quoteMessage,
  noticeText,
  resultText,
  comparisonCsv,
} from "./model.mjs?v=0.3.0";

const $ = (id) => document.getElementById(id);
let mode = "single",
  connected = false,
  sequence = 0,
  controller = null,
  lastResult = null,
  lastBatch = [],
  toastTimer,
  hasInitialized = false;
const comparisonFields = new Map();
const marketMetadata = new Map();
const lowPriceSelections = new Map();
const text = (id, value) => {
  $(id).textContent = value;
};
const show = (id, value) => {
  $(id).hidden = !value;
};
function toast(message) {
  text("toast", message);
  show("toast", true);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => show("toast", false), 2500);
}
function values() {
  return {
    length: $("length").value,
    width: $("width").value,
    height: $("height").value,
    dimensionUnit: $("dimension-unit").value,
    weight: $("weight").value,
    weightUnit: $("weight-unit").value,
    saleDate: $("sale-date").value,
    lowPriceByMarketplace: Object.fromEntries(lowPriceSelections),
    cep: $("cep").checked,
  };
}
function invalidate() {
  sequence++;
  controller?.abort();
  controller = null;
  lastResult = null;
  lastBatch = [];
  for (const id of [
    "single-result",
    "compare-result",
    "quote-result",
    "form-error",
  ])
    show(id, false);
  show("result-placeholder", true);
  $("calculate-button").classList.remove("busy");
  $("calculate-button").disabled = !connected;
  text(
    "calculate-label",
    mode === "compare" ? "对比所选站点费用" : "计算 FBA 配送费",
  );
}
function lowPriceField(code, id) {
  const presentation = lowPricePresentation(code, marketMetadata.get(code));
  if (!presentation) return null;
  const group = document.createElement("div"), label = document.createElement("label"),
    select = document.createElement("select"), hint = document.createElement("p");
  label.className = "field";
  label.textContent = `${MARKET_NAMES[code]}低价费率选择`;
  select.id = id;
  select.setAttribute("aria-describedby", `${id}-hint`);
  for (const [value, title] of presentation.labels) {
    const option = document.createElement("option");
    option.value = value; option.textContent = title; select.append(option);
  }
  select.value = lowPriceSelections.get(code) ?? "conservative";
  select.addEventListener("change", () => {
    lowPriceSelections.set(code, select.value);
    invalidate();
  });
  hint.id = `${id}-hint`; hint.className = "field-hint";
  hint.textContent = presentation.hint;
  label.append(select); group.append(label, hint);
  return group;
}
function relevantOptions() {
  const selected = mode === "compare"
    ? [...comparisonFields].filter(([, fields]) => fields.check.checked).map(([code]) => code)
    : [$("marketplace").value];
  const hasGermany = selected.includes("DE");
  show("cep-field", hasGermany);
  show("cep-hint", hasGermany);
  const container = $("low-price-fields");
  container.replaceChildren();
  for (const code of selected) {
    const field = lowPriceField(code, mode === "compare" ? `low-price-${code}` : "low-price-eligibility");
    if (field) container.append(field);
  }
  show("low-price-compare-note", mode === "compare" && container.children.length > 0);
  show("options-card", container.children.length > 0 || hasGermany);
}
function switchView(view) {
  mode = view;
  invalidate();
  document.querySelectorAll("[data-view]").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  const titles = {
    single: [
      "单站测算",
      "FBA 配送费计算器",
      "输入销售包装尺寸，预估每一件商品的配送费用。",
    ],
    compare: [
      "多站对比",
      "同一包装，多站测算",
      "使用同一套包装参数，按各站本币售价分别计算配送费。",
    ],
    api: [
      "API 与规则",
      "费用计算，统一接入",
      "了解接口、覆盖范围与可追溯的官方费率来源。",
    ],
  };
  text("breadcrumb-title", titles[view][0]);
  text("page-title", titles[view][1]);
  text("page-description", titles[view][2]);
  show("calculator-view", view !== "api");
  show("api-view", view === "api");
  show("single-market-fields", view === "single");
  show("compare-market-fields", view === "compare");
  $("price").disabled = view !== "single";
  $("marketplace").disabled = view !== "single";
  for (const { check, price } of comparisonFields.values())
    price.disabled = view !== "compare" || !check.checked;
  relevantOptions();
}
function fillExample() {
  invalidate();
  $("dimension-unit").value = "cm";
  $("dimension-unit").dataset.previous = "cm";
  $("weight-unit").value = "g";
  $("weight-unit").dataset.previous = "g";
  for (const [key, value] of Object.entries({
    length: 30,
    width: 20,
    height: 5,
    weight: 200,
  }))
    $(key).value = value;
  $("price").value = EXAMPLE_PRICES[$("marketplace").value];
  for (const [code, { price }] of comparisonFields)
    price.value = EXAMPLE_PRICES[code];
  lowPriceSelections.clear();
  relevantOptions();
  $("cep").checked = false;
  show("example-note", true);
}
function initializeMarkets(available) {
  marketMetadata.clear();
  for (const item of available) marketMetadata.set(item.marketplace, item);
  const supported = new Set(available.map((m) => m.marketplace));
  $("marketplace").replaceChildren();
  $("market-price-grid").replaceChildren();
  $("supported-markets").replaceChildren();
  comparisonFields.clear();
  for (const [code, name, currency] of MARKETS.filter(([code]) =>
    supported.has(code),
  )) {
    const option = document.createElement("option");
    option.value = code;
    option.textContent = `${name} · ${currency}`;
    $("marketplace").append(option);
    const wrapper = document.createElement("div");
    wrapper.className = "market-price";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.id = `include-${code}`;
    check.checked = ["AU", "CA", "UK"].includes(code);
    check.setAttribute("aria-label", `选择${name}`);
    const label = document.createElement("label");
    label.htmlFor = check.id;
    label.append(document.createTextNode(name));
    const small = document.createElement("small");
    small.textContent = currency;
    label.append(small);
    const price = document.createElement("input");
    price.type = "number";
    price.id = `price-${code}`;
    price.min = currency === "JPY" ? "1" : ".01";
    price.max = "100000000";
    price.step = currency === "JPY" ? "1" : ".01";
    price.required = true;
    price.inputMode = "decimal";
    price.placeholder = "售价";
    price.setAttribute("aria-label", `${name}售价 ${currency}`);
    price.disabled = true;
    check.addEventListener("change", () => {
      price.disabled = mode !== "compare" || !check.checked;
      relevantOptions();
      invalidate();
    });
    wrapper.append(check, label, price);
    $("market-price-grid").append(wrapper);
    comparisonFields.set(code, { check, price });
    const badge = document.createElement("span");
    badge.textContent = `${name} ${currency}`;
    $("supported-markets").append(badge);
  }
  $("marketplace").value = "CA";
  text("currency-label", "CAD");
}
function sources(elementId, items) {
  const list = $(elementId);
  list.replaceChildren();
  for (const item of items ?? []) {
    let url;
    try {
      url = new URL(item.url);
    } catch {
      continue;
    }
    if (url.protocol !== "https:") continue;
    const li = document.createElement("li"),
      a = document.createElement("a");
    a.href = url.href;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    const label = item.url.includes("media-amazon")
      ? "亚马逊欧洲 / 英国官方费率卡"
      : item.url.includes("G59RXFJCSKH9E9PK")
        ? "亚马逊体积重与尺寸规则"
        : item.url.includes("/pricing")
          ? "亚马逊官方定价与优惠"
          : item.title;
    a.textContent =
      label + (item.pages ? ` · 第 ${item.pages.join("、")} 页` : "");
    li.append(a);
    list.append(li);
  }
}
function notices(elementId, result) {
  const list = $(elementId);
  list.replaceChildren();
  for (const warning of result.warnings ?? []) {
    const div = document.createElement("div");
    div.className =
      "notice" +
      ([
        "FUTURE_DATE",
        "NEW_YEAR_RATE_REVIEW",
        "AU_INBOUND_ELIGIBILITY",
      ].includes(warning.code)
        ? " warning"
        : "");
    div.textContent = noticeText(warning, result.marketplace);
    list.append(div);
  }
}
function renderSingle(result) {
  lastResult = result;
  show("result-placeholder", false);
  if (result.status !== "estimated") {
    text("quote-message", quoteMessage(result.reason?.code));
    sources("quote-sources", result.sources);
    show("quote-result", true);
    return;
  }
  show("single-result", true);
  const { fee, classification, currency } = result;
  text(
    "result-market",
    `${MARKET_NAMES[result.marketplace]} ${result.marketplace}`,
  );
  text("result-currency", currency);
  text("result-total", fee.total.toFixed(currency === "JPY" ? 0 : 2));
  text(
    "result-tax",
    fee.includesTax
      ? (currency === "JPY" ? "费用已含 10% 消费税；未重复加税" : "费用已含 IVA；未另加税费")
      : "配送费未额外包含 VAT / GST 等税费",
  );
  text(
    "result-plan",
    `${planLabel(classification.plan)} · ${classification.weightBasis === "dimensional" ? "按体积重计费" : "按实重计费"}`,
  );
  text("base-fee", formatMoney(fee.base, currency));
  text(
    "discount-fee",
    `${fee.lowPriceDiscount ? "- " : ""}${formatMoney(fee.lowPriceDiscount, currency)}`,
  );
  text("fuel-fee", formatMoney(fee.fuelSurcharge, currency));
  text(
    "fuel-percent",
    fee.fuelRate ? `(${Number((fee.fuelRate * 100).toFixed(2))}%)` : "",
  );
  text("result-tier", tierLabel(classification.tier));
  text(
    "result-shipping-weight",
    formatWeight(classification.shippingWeight, classification.weightUnit),
  );
  text("result-unit-weight", formatWeight(result.package.unitWeightG, "g"));
  text(
    "result-dimensional-weight",
    ["AE","SA","JP"].includes(result.marketplace) ? "不参与本站费用计重" : formatWeight(classification.dimensionalWeight, classification.weightUnit),
  );
  text("result-date", `售出日期 ${result.saleDate}`);
  text("result-verified-date", `核验 ${result.rateCard.verifiedAt}`);
  notices("result-notices", result);
  sources("result-sources", result.sources);
}
function comparisonDetail(item, row) {
  document
    .querySelectorAll(".comparison-row")
    .forEach((r) => r.classList.toggle("selected", r === row));
  const panel = $("compare-detail");
  panel.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = MARKET_NAMES[item.request.marketplace];
  panel.append(title);
  const p = document.createElement("div");
  p.textContent =
    item.result?.status === "estimated"
      ? `基础费 ${formatMoney(item.result.fee.base, item.result.currency)} − 优惠 ${item.result.fee.lowPriceDiscount.toFixed(2)} + 附加费 ${item.result.fee.fuelSurcharge.toFixed(2)}；计费重 ${formatWeight(item.result.classification.shippingWeight, item.result.classification.weightUnit)}。`
      : quoteMessage(item.result?.reason?.code);
  panel.append(p);
  if (item.result)
    for (const n of item.result.warnings ?? []) {
      const line = document.createElement("p");
      line.textContent = noticeText(n, item.request.marketplace);
      panel.append(line);
    }
  show("compare-detail", true);
}
function renderComparison(items, results) {
  show("result-placeholder", false);
  show("compare-result", true);
  show("compare-detail", false);
  $("compare-results-body").replaceChildren();
  $("compare-notices").replaceChildren();
  lastBatch = items.map((request, index) => ({
    request,
    result: results[index]?.ok ? results[index].result : null,
    error: results[index]?.error,
  }));
  lastBatch.forEach((item) => {
    const row = document.createElement("tr");
    row.className = "comparison-row";
    row.tabIndex = 0;
    row.setAttribute(
      "aria-label",
      `查看${MARKET_NAMES[item.request.marketplace]}费用明细`,
    );
    const name = document.createElement("td");
    name.textContent = MARKET_NAMES[item.request.marketplace];
    const currency = document.createElement("small");
    currency.textContent = item.request.price.currency;
    name.append(currency);
    const price = document.createElement("td");
    price.textContent = item.request.price.amount.toFixed(item.request.price.currency === "JPY" ? 0 : 2);
    const fee = document.createElement("td");
    fee.textContent =
      item.result?.status === "estimated"
        ? item.result.fee.total.toFixed(item.result.currency === "JPY" ? 0 : 2)
        : item.error
          ? "参数有误"
          : "需报价";
    if (item.result?.status !== "estimated") fee.className = "quote-cell";
    const tier = document.createElement("td");
    tier.textContent = item.result?.classification
      ? tierLabel(item.result.classification.tier)
      : "—";
    row.append(name, price, fee, tier);
    row.addEventListener("click", () => comparisonDetail(item, row));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        comparisonDetail(item, row);
      }
    });
    $("compare-results-body").append(row);
  });
  const hint = document.createElement("div");
  hint.className = "notice";
  hint.textContent =
    "点击任一站点查看费用组成及适用条件。各站按独立本币售价测算；爱尔兰采用当前促销费率。";
  $("compare-notices").append(hint);
}
async function calculate(event) {
  event?.preventDefault();
  if (!connected || !$("calculator-form").reportValidity()) return;
  const batch = mode === "compare";
  const common = values();
  const items = batch
    ? [...comparisonFields]
        .filter(([, field]) => field.check.checked)
        .map(([code, field]) => makeRequest(common, code, field.price.value))
    : [makeRequest(common, $("marketplace").value, $("price").value)];
  if (!items.length) {
    text("form-error", "请至少选择一个站点。");
    show("form-error", true);
    return;
  }
  invalidate();
  const current = sequence;
  controller = new AbortController();
  const requestController = controller;
  $("calculate-button").disabled = true;
  $("calculate-button").classList.add("busy");
  text("calculate-label", "正在测算…");
  const timeout = setTimeout(() => requestController.abort(), 15000);
  try {
    const response = await fetch(
      batch ? "/v1/fba/calculate-batch" : "/v1/fba/calculate",
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(batch ? { items } : items[0]),
        signal: requestController.signal,
      },
    );
    const result = await response.json().catch(() => null);
    if (current !== sequence) return;
    if (!response.ok) {
      const error = new Error(
        response.status === 429
          ? "请求较频繁，请稍后重试。"
          : response.status === 401
            ? "服务需要接口认证，请联系管理员。"
            : response.status === 422
              ? "参数未通过校验，请检查售价、尺寸、重量和日期。"
              : "服务暂时不可用，请稍后重试。",
      );
      throw error;
    }
    if (!result) throw new Error("服务返回了无法读取的结果，请稍后重试。");
    if (batch) renderComparison(items, result.results);
    else renderSingle(result);
  } catch (error) {
    if (current !== sequence) return;
    text(
      "form-error",
      error.name === "AbortError"
        ? "请求超时，请稍后重试。"
        : error.message === "Failed to fetch"
          ? "网络连接中断，请稍后重试。"
          : error.message,
    );
    show("form-error", true);
  } finally {
    clearTimeout(timeout);
    if (current === sequence) {
      controller = null;
      $("calculate-button").disabled = !connected;
      $("calculate-button").classList.remove("busy");
      text("calculate-label", batch ? "对比所选站点费用" : "计算 FBA 配送费");
    }
  }
}
async function initialize() {
  text("connection-status", "正在连接");
  $("connection-dot").className = "status-dot loading";
  show("retry-connection", false);
  try {
    const [healthResponse, metadataResponse] = await Promise.all([
      fetch("/healthz", { signal: AbortSignal.timeout(10000) }),
      fetch("/v1/fba/marketplaces", { signal: AbortSignal.timeout(10000) }),
    ]);
    if (!healthResponse.ok || !metadataResponse.ok)
      throw new Error("Connection failed");
    const health = await healthResponse.json(),
      metadata = await metadataResponse.json();
    if (!hasInitialized) {
      initializeMarkets(metadata.marketplaces);
      fillExample();
      hasInitialized = true;
    }
    connected = true;
    $("calculate-button").disabled = false;
    text("connection-status", "费用服务已连接");
    $("connection-dot").className = "status-dot";
    text("footer-rate", `费率版本 ${health.rateVersion}`);
    text("api-service-version", health.version);
    text("api-rate-version", health.rateVersion);
    text(
      "api-request-example",
      JSON.stringify(makeRequest(values(), "CA", 13.99), null, 2),
    );
  } catch {
    connected = false;
    $("calculate-button").disabled = true;
    text("connection-status", "服务暂未连接");
    $("connection-dot").className = "status-dot error";
    show("retry-connection", true);
    text("footer-rate", "请连接服务后计算");
  }
}

document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () => switchView(button.dataset.view)),
  );
$("calculator-form").addEventListener("submit", calculate);
$("calculator-form").addEventListener("input", () => {
  invalidate();
  show("example-note", false);
});
$("calculator-form").addEventListener("change", (event) => {
  if (
    event.target.id === "low-price-eligibility" ||
    event.target.id === "sale-date" ||
    event.target.id === "cep"
  )
    invalidate();
});
$("marketplace").addEventListener("change", () => {
  invalidate();
  $("price").value = "";
  text("currency-label", CURRENCIES[$("marketplace").value]);
  $("price").step = $("marketplace").value === "JP" ? "1" : ".01";
  $("price").min = $("marketplace").value === "JP" ? "1" : ".01";
  show("example-note", false);
  relevantOptions();
});
$("dimension-unit").dataset.previous = "cm";
$("weight-unit").dataset.previous = "g";
for (const [id, fields] of [
  ["dimension-unit", ["length", "width", "height"]],
  ["weight-unit", ["weight"]],
])
  $(id).addEventListener("change", () => {
    const select = $(id);
    for (const field of fields)
      $(field).value = convertUnit(
        $(field).value,
        select.dataset.previous,
        select.value,
      );
    select.dataset.previous = select.value;
    invalidate();
  });
$("fill-example").addEventListener("click", fillExample);
$("retry-connection").addEventListener("click", initialize);
$("select-all-markets").addEventListener("click", () => {
  const all = [...comparisonFields.values()].every((f) => f.check.checked);
  for (const { check, price } of comparisonFields.values()) {
    check.checked = !all;
    price.disabled = all || mode !== "compare";
  }
  text("select-all-markets", all ? "选择全部" : "取消全选");
  relevantOptions();
  invalidate();
});
$("copy-result").addEventListener("click", async () => {
  if (!lastResult) return;
  try {
    await navigator.clipboard.writeText(resultText(lastResult));
    toast("测算结果已复制");
  } catch {
    toast("浏览器未允许复制，请手动选中结果。");
  }
});
$("download-comparison").addEventListener("click", () => {
  if (!lastBatch.length) return;
  const blob = new Blob([comparisonCsv(lastBatch)], {
      type: "text/csv;charset=utf-8",
    }),
    url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `FBA多站测算-${$("sale-date").value}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
$("sale-date").value = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date());
await initialize();
