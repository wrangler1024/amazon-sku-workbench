import type { CardEstimate, CardRequest } from "./rateCards";

export const AE_FBA_ENDPOINT = import.meta.env.DEV ? "/__ae-fba/v1/fba/calculate-batch"
  : (import.meta.env.VITE_FBACC_SERVICE_URL || "") + "/v1/fba/calculate-batch";
export const AE_FBA_TIMEOUT_MS = 5000;
const cache = new Map<string, { until: number; value: CardEstimate[] }>();
const tiers: Record<string, string> = {
  "small-envelope": "小信封", "standard-envelope": "标准信封",
  "large-envelope": "大信封", "standard-parcel": "标准包裹", oversize: "超大件",
};
const quote = (reason: string): CardEstimate => ({ status: "quote_required", reason });

export function aeInputProblem(request: CardRequest, date: string): string | null {
  if (request.site !== "AE" || request.category !== "普通商品") return "特殊商品需人工核价";
  if (![request.price, request.unitWeightG, ...request.dimensionsCm].every(n => Number.isFinite(n) && n > 0))
    return "待填售价与包装信息";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date)) ||
    new Date(date).toISOString().slice(0, 10) !== date) return "请填写有效的预计开售日期";
  if (Math.abs(request.price * 100 - Math.round(request.price * 100)) > 1e-6)
    return "售价请保留最多两位小数";
  return null;
}

export function aeRequest(request: CardRequest, date: string) {
  const [length, width, height] = request.dimensionsCm;
  return { marketplace: "AE", price: { amount: request.price, currency: "AED" },
    package: { dimensions: { length, width, height, unit: "cm" },
      weight: { value: request.unitWeightG, unit: "g" } },
    saleDate: date, category: "general", program: "domestic" };
}

type JsonObject = Record<string, unknown>;
function object(value: unknown): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("预估服务返回格式异常");
  return value as JsonObject;
}
function positive(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}
function parseResult(value: unknown, date: string): CardEstimate {
  const result = object(value);
  if (result.marketplace !== "AE" || result.currency !== "AED" || result.saleDate !== date)
    throw new Error("预估服务返回站点或日期不符");
  if (result.status === "quote_required") {
    const code = object(result.reason).code;
    return quote(code === "OFFICIAL_SOURCE_CONFLICT" ? "该规格的官方费率存在差异，请核对后手填 FBA 费用"
      : code === "NO_HISTORICAL_RATE" ? "该日期暂无已核验费率，请核对后手填 FBA 费用"
        : "该规格需人工核价，请核对后手填 FBA 费用");
  }
  if (result.status !== "estimated") throw new Error("预估服务未返回有效结果");
  const fee = object(result.fee), classification = object(result.classification);
  if (!positive(fee.total) || !Number.isInteger(fee.totalMinorUnits) ||
    Math.abs(fee.total * 100 - (fee.totalMinorUnits as number)) > 1e-6 ||
    fee.includesTax !== false || !positive(classification.shippingWeight) || classification.weightUnit !== "kg" ||
    typeof classification.tier !== "string" || !tiers[classification.tier] ||
    !["regular", "low_price"].includes(String(classification.plan)) || typeof result.rateVersion !== "string")
    throw new Error("预估服务返回的金额或计重格式异常");
  return { status: "matched", reason: "按完整销售包装实重自动预估，未含适用 VAT，仅供参考",
    fee: fee.total, shippingWeightG: classification.shippingWeight * 1000,
    reference: { tier: tiers[classification.tier], plan: classification.plan === "low_price" ? "低价" : "常规",
      version: result.rateVersion, source: "https://sell.amazon.ae/pricing", feeIncludesTax: false } };
}

export async function fetchAeEstimates(requests: CardRequest[], date: string, signal: AbortSignal): Promise<CardEstimate[]> {
  const body = JSON.stringify({ items: requests.map(request => aeRequest(request, date)) });
  const cached = cache.get(body);
  if (cached && cached.until > Date.now()) return cached.value;
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) controller.abort();
  const timeout = setTimeout(cancel, AE_FBA_TIMEOUT_MS);
  try {
    const response = await fetch(AE_FBA_ENDPOINT, { method: "POST", credentials: "omit", signal: controller.signal,
      headers: { "content-type": "application/json" }, body });
    if (!response.ok) throw new Error(response.status === 429 ? "预估服务繁忙，请稍后重试" : "预估服务暂不可用");
    const data = object(await response.json());
    if (!Array.isArray(data.results) || data.results.length !== requests.length)
      throw new Error("预估服务返回数量不符");
    const result = data.results.map(item => {
      const row = object(item);
      if (row.ok !== true) throw new Error("预估参数暂无法处理");
      return parseResult(row.result, date);
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
