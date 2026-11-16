import type { CardEstimate, CardRequest } from "./rateCards";
import { MARKETPLACES } from "./domain";

// The original AE exports remain compatible with integrations using this module.
export const usesRemoteFba = (site: string) => Object.prototype.hasOwnProperty.call(MARKETPLACES, site);
const taxIncluded = (site: string) => site === "MX" || site === "JP";
const currencyScale = (site: string) => site === "JP" ? 1 : 100;
const sources: Record<string, string> = {
  CA: "https://sell.amazon.ca/pricing", MX: "https://vender.amazon.com.mx/precios",
  UK: "https://sell.amazon.co.uk/pricing", US: "https://sell.amazon.com/pricing",
  AU: "https://sell.amazon.com.au/pricing", AE: "https://sell.amazon.ae/pricing",
  SA: "https://sell.amazon.sa/pricing", JP: "https://sell.amazon.co.jp/pricing",
  DE: "https://sell.amazon.de/preisgestaltung",
};

export const FBA_ENDPOINT = import.meta.env.DEV ? "/__ae-fba/v1/fba/calculate-batch"
  : (import.meta.env.VITE_FBACC_SERVICE_URL || "") + "/v1/fba/calculate-batch";
export const FBA_TIMEOUT_MS = 5000;
const cache = new Map<string, { until: number; value: CardEstimate[] }>();
const tiers: Record<string, string> = {
  "small-envelope": "小信封", "standard-envelope": "标准信封",
  "large-envelope": "大信封", "standard-parcel": "标准包裹", oversize: "超大件",
  "light-envelope": "轻型信封", "extra-large-envelope": "加大信封", "small-parcel": "小包裹",
  "small-oversize": "小型超大件", "standard-oversize-light": "标准超大件（轻）",
  "standard-oversize-heavy": "标准超大件（重）", "standard-oversize-large": "标准超大件（大）",
  "bulky-oversize": "大体积超大件", "heavy-oversize": "重型超大件",
  envelope: "信封", standard: "标准件", parcel: "包裹",
  "medium-oversize": "中型超大件", "large-oversize": "大型超大件",
  "standard-oversize": "标准超大件", "extra-large": "特大型",
  "small-standard": "小号标准件", "large-standard": "大号标准件",
  "small-bulky": "小号大件", "large-bulky": "大号大件",
  "extra-large-0-50": "特大型（不超过 50 lb）", "extra-large-50-70": "特大型（50–70 lb）",
  "extra-large-70-150": "特大型（70–150 lb）",
  "jp-small": "小型", "jp-standard-thin": "标准薄件",
  ...Object.fromEntries([20, 30, 40, 50, 60, 80, 100].map(size => [`jp-standard-${size}`, `标准件 ${size}`])),
  ...Object.fromEntries([60, 80, 100, 120, 140, 160, 180, 200].map(size => [`jp-oversize-${size}`, `大型 ${size}`])),
  ...Object.fromEntries([200, 220, 240, 260, 400].map(size => [`jp-supersize-${size}`, `特大型 ${size}`])),
};
const quote = (reason: string): CardEstimate => ({ status: "quote_required", reason });

export function fbaInputProblem(request: CardRequest, date: string): string | null {
  if (!usesRemoteFba(request.site) || request.category !== "普通商品") return "特殊商品需人工核价";
  if (![request.price, request.unitWeightG, ...request.dimensionsCm].every(n => Number.isFinite(n) && n > 0))
    return "待填售价与包装信息";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) ||
    new Date(date).toISOString().slice(0, 10) !== date) return "请填写有效的预计开售日期";
  if (request.site === "JP" && !Number.isInteger(request.price)) return "日本售价请按整数日元填写";
  if (Math.abs(request.price * currencyScale(request.site) - Math.round(request.price * currencyScale(request.site))) > 1e-6)
    return "售价请保留最多两位小数";
  return null;
}

export function fbaRequest(request: CardRequest, date: string) {
  const problem = fbaInputProblem(request, date);
  if (problem) throw new Error(problem);
  const [length, width, height] = request.dimensionsCm;
  return { marketplace: request.site, price: { amount: request.price, currency: MARKETPLACES[request.site].currency },
    package: { dimensions: { length, width, height, unit: "cm" },
      weight: { value: request.unitWeightG, unit: "g" } },
    saleDate: date, category: "general", program: "domestic",
    ...(["UK", "DE"].includes(request.site) ? { options: { lowPriceEligibility: "conservative",
      ...(request.site === "DE" ? { centralEuropeProgram: false } : {}) } } : {}) };
}

type JsonObject = Record<string, unknown>;
function object(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("预估服务返回格式异常");
  return value as JsonObject;
}
function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}
function parseResult(value: unknown, date: string, request: CardRequest): CardEstimate {
  const result = object(value);
  if (result.marketplace !== request.site || result.currency !== MARKETPLACES[request.site].currency || result.saleDate !== date)
    throw new Error("预估服务返回站点或日期不符");
  if (result.status === "quote_required") {
    const code = object(result.reason).code;
    return quote(code === "OFFICIAL_SOURCE_CONFLICT" ? "该规格的官方费率存在差异，请核对后手填 FBA 费用"
      : code === "NO_HISTORICAL_RATE" ? "该日期暂无已核验费率，请核对后手填 FBA 费用"
        : "该规格需人工核价，请核对后手填 FBA 费用");
  }
  if (result.status !== "estimated") throw new Error("预估服务未返回有效结果");
  const fee = object(result.fee), classification = object(result.classification);
  const scale = currencyScale(request.site);
  const weightUnit = request.site === "US" ? "lb" : "kg";
  if (!positive(fee.total) || !Number.isInteger(fee.totalMinorUnits) ||
    Math.abs(fee.total * scale - (fee.totalMinorUnits as number)) > 1e-6 ||
    fee.includesTax !== taxIncluded(request.site) || !positive(classification.shippingWeight) || classification.weightUnit !== weightUnit ||
    typeof classification.tier !== "string" || !tiers[classification.tier] ||
    !["regular", "low_price", ...(["CA", "US", "UK", "DE"].includes(request.site) ? ["peak"] : [])].includes(String(classification.plan)) ||
    typeof result.rateVersion !== "string" || !result.rateVersion)
    throw new Error("预估服务返回的金额或计重格式异常");
  if (["UK", "DE"].includes(request.site)) {
    const assumptions = object(result.assumptions);
    if ((request.site === "DE" && assumptions.centralEuropeProgram !== false) ||
      assumptions.lowPriceEligibility !== "conservative" || result.program !== "domestic")
      throw new Error("预估服务返回的配送计划或低价资格不符");
  }
  return { status: "matched", reason: request.site === "DE"
      ? "德国本土配送、未参加中欧计划；含适用附加费，未含 VAT，仅供参考"
      : `按本站本土普通商品配送费率预估，${taxIncluded(request.site) ? "已含本站适用税费" : "未另加适用税费"}，仅供参考`,
    fee: fee.total, shippingWeightG: classification.shippingWeight * (weightUnit === "lb" ? 453.59237 : 1000),
    reference: { tier: tiers[classification.tier], plan: classification.plan === "low_price" ? "低价" : classification.plan === "peak" ? "旺季" : "常规",
      version: result.rateVersion, source: sources[request.site], feeIncludesTax: taxIncluded(request.site) } };
}

export async function fetchFbaEstimates(requests: CardRequest[], date: string, signal: AbortSignal): Promise<CardEstimate[]> {
  const body = JSON.stringify({ items: requests.map(request => fbaRequest(request, date)) });
  const cached = cache.get(body);
  if (cached && cached.until > Date.now()) return cached.value;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(cancel, FBA_TIMEOUT_MS);
  try {
    const response = await fetch(FBA_ENDPOINT, { method: "POST", credentials: "omit", signal: controller.signal,
      headers: { "content-type": "application/json" }, body });
    if (!response.ok) throw new Error(response.status === 429 ? "预估服务繁忙，请稍后重试" : "预估服务暂不可用");
    const data = object(await response.json());
    if (!Array.isArray(data.results) || data.results.length !== requests.length)
      throw new Error("预估服务返回数量不符");
    const result = data.results.map((item, index) => {
      const row = object(item);
      if (row.ok !== true) throw new Error("预估参数暂无法处理");
      return parseResult(row.result, date, requests[index]);
    });
    if (signal.aborted) throw new Error("已取消旧预估");
    if (cache.size >= 100) cache.delete(cache.keys().next().value!);
    cache.set(body, { until: Date.now() + 5 * 60_000, value: result });
    return result;
  } finally {
    clearTimeout(timeout);
    signal.removeEventListener("abort", cancel);
  }
}

export const aeInputProblem = fbaInputProblem;
export const aeRequest = fbaRequest;
export const fetchAeEstimates = fetchFbaEstimates;
export const AE_FBA_ENDPOINT = FBA_ENDPOINT;
export const AE_FBA_TIMEOUT_MS = FBA_TIMEOUT_MS;
