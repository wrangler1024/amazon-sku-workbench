export const MARKETS = [
  ["US", "美国", "USD"],
  ["CA", "加拿大", "CAD"],
  ["MX", "墨西哥", "MXN"],
  ["UK", "英国", "GBP"],
  ["DE", "德国", "EUR"],
  ["FR", "法国", "EUR"],
  ["IT", "意大利", "EUR"],
  ["ES", "西班牙", "EUR"],
  ["NL", "荷兰", "EUR"],
  ["SE", "瑞典", "SEK"],
  ["PL", "波兰", "PLN"],
  ["BE", "比利时", "EUR"],
  ["IE", "爱尔兰", "EUR"],
  ["AU", "澳大利亚", "AUD"],
  ["AE", "阿联酋", "AED"],
  ["SA", "沙特", "SAR"],
  ["JP", "日本", "JPY"],
];
export const MARKET_NAMES = Object.fromEntries(
  MARKETS.map(([code, name]) => [code, name]),
);
export const CURRENCIES = Object.fromEntries(
  MARKETS.map(([code, , currency]) => [code, currency]),
);
export const EXAMPLE_PRICES = {
  US: 25,
  CA: 13.99,
  MX: 449,
  UK: 15,
  DE: 25,
  FR: 25,
  IT: 25,
  ES: 25,
  NL: 25,
  SE: 250,
  PL: 100,
  BE: 25,
  IE: 25,
  AU: 25,
  AE: 20,
  SA: 30,
  JP: 1500,
};
const FACTORS = {
  cm: 1,
  in: 2.54,
  g: 1,
  kg: 1000,
  lb: 453.59237,
  oz: 28.349523125,
};
export function convertUnit(value, from, to) {
  if (value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number) || !FACTORS[from] || !FACTORS[to]) return "";
  if (["cm", "in"].includes(from) !== ["cm", "in"].includes(to))
    throw new Error("Incompatible units");
  return String(
    Number(((number * FACTORS[from]) / FACTORS[to]).toPrecision(15)),
  );
}
const EXTENDED_LOW_PRICE_MARKETS = new Set([
  "UK", "DE", "FR", "IT", "ES", "NL", "SE", "PL", "BE", "IE",
]);
export function lowPriceChoices(marketplace) {
  if (EXTENDED_LOW_PRICE_MARKETS.has(marketplace))
    return ["conservative", "confirmed_eligible", "ineligible"];
  return marketplace === "AU" ? ["conservative", "ineligible"] : [];
}
export function lowPricePresentation(marketplace, metadata) {
  const choices = lowPriceChoices(marketplace);
  const low = metadata?.lowPrice;
  if (!choices.length || !Number.isFinite(low?.maximum)) return null;
  const cap = `${low.inclusive ? "≤" : "<"}${metadata.currency} ${low.maximum}`;
  if (marketplace === "AU") return {
    labels: [
      ["conservative", `按售价自动判断（${cap}）`],
      ["ineligible", "不使用低价费率"],
    ],
    hint: `澳大利亚没有扩展低价档。普通商品售价${cap}且包装符合条件时使用低价费率，否则使用常规费率。`,
  };
  const extended = `${low.inclusive ? "≤" : "<"}${metadata.currency} ${low.extendedMaximum}`;
  return {
    labels: [
      ["conservative", `未确认扩展资格（${cap}）`],
      ["confirmed_eligible", `已确认扩展资格（${extended}）`],
      ["ineligible", "不使用低价优惠（按常规或旺季费率）"],
    ],
    hint: `${MARKET_NAMES[marketplace]}：未确认时按售价${cap}判断；只有已核实官方费用类目符合扩展优惠，才选择${extended}。包装仍须满足低价档尺寸、重量要求，不符合时回到常规或旺季费率。此选择仅用于预估，不会申请或核实官方资格。`,
  };
}
export function makeRequest(values, marketplace, price) {
  const options = {};
  const choices = lowPriceChoices(marketplace);
  if (choices.length) {
    const selected = values.lowPriceByMarketplace
      ? values.lowPriceByMarketplace[marketplace]
      : values.lowPriceEligibility;
    options.lowPriceEligibility = choices.includes(selected) ? selected : "conservative";
  }
  if (marketplace === "DE") options.centralEuropeProgram = values.cep;
  return {
    marketplace,
    price: { amount: Number(price), currency: CURRENCIES[marketplace] },
    package: {
      dimensions: {
        length: Number(values.length),
        width: Number(values.width),
        height: Number(values.height),
        unit: values.dimensionUnit,
      },
      weight: { value: Number(values.weight), unit: values.weightUnit },
    },
    saleDate: values.saleDate,
    category: "general",
    program: "domestic",
    options,
  };
}
export function formatMoney(amount, currency) {
  return `${currency} ${Number(amount).toFixed(currency === "JPY" ? 0 : 2)}`;
}
export function formatWeight(value, unit) {
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value)} ${unit}`;
}
const TIERS = {
  "small-envelope": "小信封",
  "light-envelope": "轻信封",
  "standard-envelope": "标准信封",
  "large-envelope": "大信封",
  "extra-large-envelope": "特大信封",
  envelope: "信封",
  parcel: "包裹",
  "small-parcel": "小包裹",
  "standard-parcel": "标准包裹",
  standard: "标准件",
  "small-standard": "小号标准件",
  "large-standard": "大号标准件",
  "small-bulky": "小号大件",
  "large-bulky": "大号大件",
  "small-oversize": "小号超大件",
  "medium-oversize": "中号超大件",
  "large-oversize": "大号超大件",
  "standard-oversize": "标准超大件",
  "standard-oversize-light": "轻型标准超大件",
  "standard-oversize-heavy": "重型标准超大件",
  "standard-oversize-large": "大型标准超大件",
  "bulky-oversize": "大体积超大件",
  "heavy-oversize": "重型超大件",
  "extra-large": "特大件",
  oversize: "大件",
  "extra-large-0-50": "特大件 ≤50 lb",
  "extra-large-50-70": "特大件 50–70 lb",
  "extra-large-70-150": "特大件 70–150 lb",
};
export function tierLabel(id) {
  if (TIERS[id]) return TIERS[id];
  if (id === 'jp-small') return '日本小型';
  if (id === 'jp-standard-thin') return '日本标准薄件';
  const match = /^jp-(standard|oversize|supersize)-(\d+)$/.exec(id);
  if (!match) return id;
  const kind = {standard:'标准',oversize:'大型',supersize:'特大型'}[match[1]];
  return `日本${kind} · 三边合计≤${match[2]} cm`;
}
export const planLabel = (plan) =>
  ({ regular: "常规费率", peak: "旺季费率", low_price: "低价费率" })[plan] ??
  plan;
export function quoteMessage(code) {
  return (
    {
      PRICE_BOUNDARY_UNCONFIRMED: "沙特官方费用列未明确售价恰好 SAR 25 的归属，请取得官方报价。",
      OFFICIAL_SOURCE_CONFLICT:
        "该条件下的官方费用来源存在不一致。请使用亚马逊收入计算器或官方报价核实。",
      PACKAGE_OUT_OF_SCOPE:
        "当前包装超出本工具已支持的尺寸或重量范围，请取得亚马逊官方报价。",
      UNSUPPORTED_MARKETPLACE: "当前站点尚未纳入已核验的费率范围。",
      UNSUPPORTED_PROGRAM:
        "当前仅支持国内 FBA 配送。跨境、远程配送和特殊计划需单独报价。",
      UNSUPPORTED_CATEGORY:
        "当前仅支持普通、非服装、非危险品商品。请先核实官方费用类目。",
      NO_HISTORICAL_RATE:
        "所选日期早于当前费率表的生效时间，请选择其他日期或核实历史报价。",
      RATE_REVIEW_REQUIRED: "该条件下的费率仍需进一步核实，请取得官方报价。",
    }[code] ?? "当前条件下暂时无法给出可靠金额，请核实官方报价。"
  );
}
export function noticeText(notice, market) {
  const cap =
    { UK: "10 GBP", DE: "11 EUR", SE: "140 SEK", PL: "55 PLN" }[market] ??
    "12 EUR";
  return (
    {
      ME_UNIT_WEIGHT: "本站按包装实重选择费用档，不按体积重收费；费用未含 VAT。超大件仍须确认入仓资格。",
      JP_ORDINARY_NON_FROZEN: "日本按包装实重和尺寸条件计价，费用已含 10% 消费税。仅支持普通非冷冻商品；特大型还须满足入仓及服务限制。",
      CONSERVATIVE_LOW_PRICE: `未确认官方费用类目，低价优惠按 ${cap} 的保守门槛判断。`,
      FUTURE_DATE:
        "当前为未来日期测算，使用核验日已公布的规则；实际售出前请复核费率更新。",
      NEW_YEAR_RATE_REVIEW:
        "当前使用旺季结束后的常规费率，尚不代表已核验下一年度的新费率。",
      DE_STORAGE_ASSUMPTION: notice.message.includes("Uses CEP")
        ? "按中欧计划（CEP）费率测算，请确认已符合该计划资格。"
        : "按仅德国存储的费率测算，已包含相应费率差额。",
      IE_PROMOTIONAL_RATES:
        "爱尔兰采用官方当前有效的促销费率，亚马逊可能提前 30 天通知调整。",
      AU_INBOUND_ELIGIBILITY:
        "包装超出澳洲普通入仓限重或尺寸；费用预估不代表已获得入仓资格。",
    }[notice.code] ?? notice.message
  );
}
export function resultText(result) {
  if (result.status !== "estimated")
    return `${MARKET_NAMES[result.marketplace]}：${quoteMessage(result.reason?.code)}`;
  return [
    `${MARKET_NAMES[result.marketplace]} FBA 配送费：${formatMoney(result.fee.total, result.currency)}/件`,
    `包装：${result.package.dimensionsCm.join(" × ")} cm；实重 ${result.package.unitWeightG} g`,
    `尺寸档：${tierLabel(result.classification.tier)}；计费重 ${formatWeight(result.classification.shippingWeight, result.classification.weightUnit)}`,
    `预计售出日期：${result.saleDate}；${planLabel(result.classification.plan)}`,
    `费率版本：${result.rateVersion}；${result.fee.includesTax ? "费表已含税" : "未额外含税"}`,
  ].join("\n");
}
export function comparisonCsv(results) {
  const rows = [
    [
      "站点",
      "币种",
      "售价",
      "FBA配送费",
      "尺寸档",
      "计费重量",
      "计费重量单位",
      "预计售出日期",
      "状态",
      "费率版本",
    ],
  ];
  for (const { request, result } of results)
    rows.push([
      MARKET_NAMES[request.marketplace],
      request.price.currency,
      request.price.amount,
      result?.status === "estimated" ? result.fee.total : "",
      result?.classification ? tierLabel(result.classification.tier) : "",
      result?.classification?.shippingWeight ?? "",
      result?.classification?.weightUnit ?? "",
      request.saleDate,
      result?.status === "estimated" ? "已测算" : "需报价",
      result?.rateVersion ?? "",
    ]);
  return (
    "\uFEFF" +
    rows
      .map((row) =>
        row
          .map((cell) => '"' + String(cell).replaceAll('"', '""') + '"')
          .join(","),
      )
      .join("\r\n")
  );
}
