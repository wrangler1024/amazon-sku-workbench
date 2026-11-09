import type { Marketplace } from "./domain";

export interface FbaCard {
  key: string;
  site: Marketplace;
  plan: string;
  tier: string;
  category: string;
  priceMin: number;
  priceMax: number | null;
  maxDimsCm: [number, number, number];
  maxUnitWeightG: number;
  shippingMethod: "unit" | "max_dimensional" | "manual_shipping_weight";
  volumeDivisor: number;
  shippingWeightStepG?: number;
  weightMinG: number;
  weightMaxG: number;
  baseFee: number;
  surchargeRate: number;
  includesSurcharge: boolean;
  feeIncludesTax: boolean;
  effectiveFrom: number;
  effectiveUntil: number | null;
  version: string;
  source: string;
  verification: string;
  priority: number;
  caLowPriceThreshold?: number;
  caLowPriceDiscount?: number;
}

export interface CardRequest {
  site: Marketplace;
  category: "普通商品" | "特殊品类或危险品" | "待确认";
  price: number;
  dimensionsCm: [number, number, number];
  unitWeightG: number;
  manualShippingWeightG?: number;
  saleDate: number;
}

export type CardEstimate = {
  status: "matched" | "quote_required";
  reason: string;
  card?: FbaCard;
  shippingWeightG?: number;
  fee?: number;
  surcharge?: number;
  reference?: { tier: string; plan: string; version: string; source: string; feeIncludesTax: boolean };
};

export function estimateFbaCard(cards: FbaCard[], request: CardRequest): CardEstimate {
  const quote = (reason: string): CardEstimate => ({ status: "quote_required", reason });
  if (request.category !== "普通商品") return quote("需逐 SKU 报价：先确认商品为普通、非危险品");
  const dimensions = [...request.dimensionsCm].sort((a, b) => b - a);
  if (![request.price, request.unitWeightG, ...dimensions].every((n) => Number.isFinite(n) && n > 0) ||
      !Number.isFinite(request.saleDate)) return quote("需逐 SKU 报价：补齐标价、尺寸、重量和日期");
  if (["AE", "SA"].includes(request.site) && Math.abs(request.price * 100 - Math.round(request.price * 100)) > 0.000001)
    return quote("售价请按本站币种保留最多两位小数后核价");
  if (request.site === "SA" && request.price === 25)
    return quote("沙特售价恰好 25 SAR，需由亚马逊官方报价确认低价资格");
  if (request.site === "JP" && !Number.isInteger(request.price))
    return quote("日本售价请按整数日元填写后核价");
  const eligible = cards.filter((card) =>
    card.site === request.site && card.category === "普通商品" &&
    // Without an official fee-category mapping, use the agreed UK conservative threshold.
    !(card.site === "UK" && card.plan === "低价" && request.price > 10) &&
    card.priceMin <= request.price && (card.priceMax === null || request.price < card.priceMax) &&
    card.effectiveFrom <= request.saleDate &&
    (card.effectiveUntil === null || request.saleDate < card.effectiveUntil) &&
    dimensions.every((n, i) => n <= card.maxDimsCm[i]) &&
    request.unitWeightG <= card.maxUnitWeightG && card.baseFee > 0);
  if (!eligible.length) return quote("需逐 SKU 报价：无适用的站点、价格、尺寸、重量或生效档位");
  const candidates = eligible.map((card) => {
    const rawWeightG = card.shippingMethod === "unit" ? request.unitWeightG
      : card.shippingMethod === "max_dimensional" && card.volumeDivisor > 0
        ? Math.max(request.unitWeightG, dimensions.reduce((a, b) => a * b, 1) /
          card.volumeDivisor * 1000)
        : request.manualShippingWeightG ?? NaN;
    const step = card.shippingWeightStepG ?? 0;
    const shippingWeightG = step > 0 ? Math.ceil((rawWeightG - 1e-7) / step) * step : rawWeightG;
    return { card, shippingWeightG };
  }).filter(({ card, shippingWeightG }) => Number.isFinite(shippingWeightG) &&
    shippingWeightG > card.weightMinG + 0.0001 &&
    shippingWeightG <= card.weightMaxG + 0.0001);
  if (!candidates.length) return quote(eligible.some((card) => card.shippingMethod === "manual_shipping_weight")
    ? "需逐 SKU 报价：请从亚马逊取得计费重量后填入"
    : "需逐 SKU 报价：无适用的计费重档位");
  // Regular UK cards remain valid as a fallback. Low-price eligibility includes size/weight;
  // peak cards override regular cards only inside their explicit effective dates.
  const ukPlan = request.site !== "UK" ? null
    : candidates.some(({ card }) => card.plan === "低价") ? "低价"
      : candidates.some(({ card }) => card.plan === "旺季") ? "旺季" : "常规";
  const applicable = ukPlan ? candidates.filter(({ card }) => card.plan === ukPlan) : candidates;
  const priority = Math.min(...applicable.map(({ card }) => card.priority));
  const narrow = applicable.filter(({ card }) => card.priority === priority);
  if (narrow.length !== 1) return quote("需逐 SKU 报价：适用档位不唯一");
  const { card, shippingWeightG } = narrow[0];
  const baseFee = card.site === "CA" && card.tier === "标准Standard" &&
    card.weightMaxG > 1500 && shippingWeightG > 1500
    ? card.baseFee + Math.ceil((shippingWeightG - 1500) / 100) * .09 : card.baseFee;
  const discounted = card.site === "CA" && request.price < (card.caLowPriceThreshold ?? 0)
    ? Math.max(baseFee - (card.caLowPriceDiscount ?? 0), 0) : baseFee;
  const surcharge = card.includesSurcharge ? 0 : discounted * card.surchargeRate;
  return { status: "matched", reason: "仅供初选，正式提审须主管核对逐 SKU 官方报价",
    card, shippingWeightG, surcharge, fee: discounted + surcharge };
}

export interface RoughInput {
  listedPrice: number;
  priceIncludesSalesTax: boolean;
  salesTaxRate: number;
  cnyPerLocal: number;
  purchaseCny: number;
  headhaulCny: number;
  fbaFeeLocal: number;
  fbaFeeIncludesTax: boolean;
  referralRate: number;
  referralBasis: "净收入" | "买家标价";
  marketingLossRate: number;
  importDutyLocal: number;
  importTaxLocal: number;
  platformFeeTaxRate: number;
  incomeTaxWithholdingRate: number;
  importChargesIncludedInHeadhaul: boolean;
}

export function calculateRoughOpportunity(input: RoughInput) {
  const values = Object.values(input).filter((value): value is number => typeof value === "number");
  if (!values.every((value) => Number.isFinite(value) && value >= 0) ||
      input.listedPrice <= 0 || input.cnyPerLocal <= 0 || input.salesTaxRate >= 1 ||
      input.referralRate >= 1 || input.marketingLossRate >= 1 ||
      input.incomeTaxWithholdingRate > 1) return null;
  const revenue = input.priceIncludesSalesTax
    ? input.listedPrice / (1 + input.salesTaxRate) : input.listedPrice;
  const referral = (input.referralBasis === "买家标价" ? input.listedPrice : revenue) *
    input.referralRate;
  const feeTax = ((input.fbaFeeIncludesTax ? 0 : input.fbaFeeLocal) + referral) *
    input.platformFeeTaxRate;
  const incomeTaxWithholding = revenue * input.incomeTaxWithholdingRate;
  const acquisition = (input.purchaseCny + input.headhaulCny) / input.cnyPerLocal;
  const contribution = revenue * (1 - input.marketingLossRate) - referral -
    input.fbaFeeLocal - feeTax - incomeTaxWithholding - acquisition -
    (input.importChargesIncludedInHeadhaul ? 0 : input.importDutyLocal + input.importTaxLocal);
  const cash = acquisition +
    (input.importChargesIncludedInHeadhaul ? 0 : input.importDutyLocal + input.importTaxLocal) + feeTax;
  return { revenue, contribution, contributionRate: contribution / revenue,
    upfrontRoi: cash > 0 ? contribution / cash : null, feeTax, incomeTaxWithholding };
}
