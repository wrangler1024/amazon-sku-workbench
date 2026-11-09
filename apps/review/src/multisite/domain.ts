/** The v2 review key is physical SKU + marketplace; JP numbers remain legacy row IDs. */
export const MARKETPLACES = {
  CA: { label: "加拿大", currency: "CAD" },
  MX: { label: "墨西哥", currency: "MXN" },
  UK: { label: "英国", currency: "GBP" },
  US: { label: "美国", currency: "USD" },
  AU: { label: "澳大利亚", currency: "AUD" },
  AE: { label: "阿联酋", currency: "AED" },
  SA: { label: "沙特", currency: "SAR" },
  JP: { label: "日本", currency: "JPY" },
} as const;

export type Marketplace = keyof typeof MARKETPLACES;
export type Collector = "亚马逊代收" | "卖家申报";
export type TaxClass = "标准" | "低税率" | "零税率" | "免税";

export interface TaxProfile {
  site: Marketplace;
  assumptionNote?: string;
  roughConfigured?: boolean;
  region: string;
  taxClass: TaxClass;
  currency: string;
  salesTaxRate: number;
  priceIncludesTax: boolean;
  collector: Collector;
  sellerIsImporter: boolean;
  registrationStatus: "已登记" | "未登记" | "待核验";
  registrationEvidence: string;
  importerDocumentEvidence: string;
  importTaxRecoverable: boolean;
  importRecoverabilityEvidence: string;
  feeTaxRecoverable: boolean;
  feeRecoverabilityEvidence: string;
  importDutyRuleEvidence: string;
  feeTaxRate: number;
  incomeTaxWithholdingRate: number;
  incomeTaxWithholdingRecoverable: boolean;
  incomeTaxWithholdingRuleEvidence: string;
  incomeTaxWithholdingRecoveryEvidence: string;
  importChargesIncludedInHeadhaul: boolean;
  approved: boolean;
  effectiveFrom: number;
  effectiveUntil: number | null;
  version: string;
  source: string;
  approvalEvidence: string;
}

export interface FinanceProfile {
  site: Marketplace;
  category: string;
  currency?: string;
  roughConfigured?: boolean;
  cnyPerCurrency: number;
  headhaulCnyPerKg: number;
  referralRate: number;
  referralBasis: "净收入" | "买家标价";
  referralFeeIncludesTax: boolean;
  adRate: number;
  returnLossRate: number;
  storageLossRate: number;
  promotionRate: number;
  settlementLossRate: number;
  targetContributionRate: number;
  volumeDivisorCm3PerKg: number;
  approved: boolean;
  effectiveFrom: number;
  effectiveUntil: number | null;
  version: string;
  source: string;
}

export interface FbaRateRule {
  site: Marketplace;
  entitySku: string;
  currency: string;
  category: string;
  quotedPrice: number;
  quotedLengthCm: number;
  quotedWidthCm: number;
  quotedHeightCm: number;
  quotedWeightG: number;
  sizeTier: string;
  maxLengthCm: number;
  maxMiddleCm: number;
  maxShortestCm: number;
  maxUnitWeightG: number;
  shippingWeightMethod: "unit" | "max_dimensional";
  minShippingWeightG: number;
  maxShippingWeightG: number;
  minPrice: number;
  maxPrice: number;
  fee: number;
  surchargeRate: number;
  includesSurcharge: boolean;
  feeIncludesTax: boolean;
  approved: boolean;
  verifiedAt: number;
  verifiedBy: string;
  verificationEvidence: string;
  effectiveFrom: number;
  effectiveUntil: number | null;
  version: string;
  source: string;
}

export interface SiteDraft {
  entitySku: string;
  site: Marketplace;
  expectedSaleDate: string;
  region: string;
  taxClass: TaxClass;
  productName: string;
  productNameEn: string;
  category: string;
  referenceAsins: string;
  sourceUrl: string;
  supplierName: string;
  moq: number | null;
  supplierCooperation: string;
  sampleAndLeadTimeNotes: string;
  tieredQuotationNotes: string;
  notes: string;
  selectionRationale: string;
  listedPrice: number;
  fbaFeeLocal: number;
  purchaseCny: number;
  processingCny: number;
  lengthCm: number;
  widthCm: number;
  heightCm: number;
  unitWeightG: number;
  importDutyLocal: number;
  importTaxLocal: number;
  customsCode: string;
  importCostEvidence: string;
  trademarkRisk: "未排查" | "无风险" | "有风险" | "待确认";
  patentRisk: "未排查" | "无风险" | "有风险" | "待确认";
  copyrightRisk: "未排查" | "无风险" | "有风险" | "待确认";
  complianceNotes: string;
}

export interface SiteCalculation {
  shippingWeightG: number;
  fbaRule: FbaRateRule | null;
  fbaSurchargeLocal: number;
  salesTaxLocal: number;
  checkoutTaxLocal: number;
  netRevenueLocal: number;
  fbaFeeEconomicLocal: number;
  referralFeeEconomicLocal: number;
  platformFeeTaxLocal: number;
  platformFeeTaxRecoverableLocal: number;
  platformFeeTaxNonrecoverableLocal: number;
  incomeTaxWithholdingLocal: number;
  incomeTaxWithholdingRecoverableLocal: number;
  incomeTaxWithholdingNonrecoverableLocal: number;
  importTaxNonrecoverableLocal: number;
  importTaxRecoverableLocal: number;
  headhaulLocal: number;
  acquisitionLocal: number;
  contributionLocal: number;
  contributionRate: number;
  upfrontCapitalRoi: number;
}

export const EMPTY_SITE_DRAFT: SiteDraft = {
  entitySku: "", site: "CA", expectedSaleDate: "", region: "", taxClass: "标准", productName: "",
  productNameEn: "", category: "", referenceAsins: "", sourceUrl: "",
  supplierName: "", moq: null, supplierCooperation: "", sampleAndLeadTimeNotes: "",
  tieredQuotationNotes: "", notes: "", selectionRationale: "", listedPrice: 0, fbaFeeLocal: 0, purchaseCny: 0,
  processingCny: 0, lengthCm: 0, widthCm: 0, heightCm: 0,
  unitWeightG: 0, importDutyLocal: 0, importTaxLocal: 0,
  customsCode: "", importCostEvidence: "",
  trademarkRisk: "未排查", patentRisk: "未排查", copyrightRisk: "未排查",
  complianceNotes: "",
};

export function currentShanghaiDate(): string {
  return new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

export function newSiteDraft(site: Marketplace = "CA"): SiteDraft {
  return { ...EMPTY_SITE_DRAFT, site, expectedSaleDate: currentShanghaiDate(),
    region: "全站", taxClass: "标准" };
}

export function activeAt(start: number, end: number | null, now: number): boolean {
  return start <= now && (end === null || end > now);
}

export function forecastTimestamp(date: string): number {
  return /^\d{4}-\d{2}-\d{2}$/.test(date)
    ? Date.parse(`${date}T12:00:00+08:00`) : NaN;
}

function nonnegative(value: number): boolean {
  return Number.isFinite(value) && value >= 0;
}

export function selectFbaRule(
  draft: SiteDraft,
  finance: FinanceProfile,
  rules: FbaRateRule[],
  now = Date.now(),
): { rule: FbaRateRule | null; shippingWeightG: number } {
  const dimensions = [draft.lengthCm, draft.widthCm, draft.heightCm]
    .sort((a, b) => b - a);
  const divisor = finance.volumeDivisorCm3PerKg;
  const dimensionalWeightG = divisor > 0
    ? Math.max(draft.unitWeightG, dimensions.reduce((a, b) => a * b, 1) / divisor * 1000)
    : NaN;
  if (![...dimensions, draft.unitWeightG, dimensionalWeightG, draft.listedPrice].every(nonnegative) ||
      dimensions[2] <= 0 || draft.unitWeightG <= 0 || draft.listedPrice <= 0) {
    return { rule: null, shippingWeightG: dimensionalWeightG };
  }
  if (!draft.entitySku.trim()) return { rule: null, shippingWeightG: dimensionalWeightG };
  const matches = rules.filter((item) =>
    item.approved && Boolean(item.version) && Boolean(item.source) &&
    Boolean(item.verificationEvidence) && Boolean(item.verifiedBy) && item.verifiedAt > 0 &&
    item.entitySku === draft.entitySku && item.fee > 0 &&
    Number.isFinite(item.surchargeRate) && item.surchargeRate >= 0 &&
    item.site === draft.site &&
    item.currency === MARKETPLACES[draft.site].currency &&
    activeAt(item.effectiveFrom, item.effectiveUntil, now) &&
    item.category === draft.category &&
    Math.abs(item.quotedPrice - draft.listedPrice) <= 0.01 &&
    Math.abs(item.quotedLengthCm - draft.lengthCm) <= 0.1 &&
    Math.abs(item.quotedWidthCm - draft.widthCm) <= 0.1 &&
    Math.abs(item.quotedHeightCm - draft.heightCm) <= 0.1 &&
    Math.abs(item.quotedWeightG - draft.unitWeightG) <= 1
  ).sort((a, b) => b.effectiveFrom - a.effectiveFrom ||
    b.verifiedAt - a.verifiedAt);
  const rule = matches[0] ?? null;
  return { rule, shippingWeightG: rule?.shippingWeightMethod === "unit" ? draft.unitWeightG : dimensionalWeightG };
}

export function calculateSite(
  draft: SiteDraft,
  tax: TaxProfile,
  finance: FinanceProfile,
  rules: FbaRateRule[],
  now = Date.now(),
): SiteCalculation | null {
  if (!finance.approved || tax.roughConfigured === false || tax.site !== draft.site || finance.site !== draft.site ||
      ![draft.category, "通用", "全站", "待确认"].includes(finance.category) ||
      tax.currency !== MARKETPLACES[draft.site].currency ||
      !Number.isFinite(tax.salesTaxRate) || tax.salesTaxRate < 0 ||
      !Number.isFinite(tax.incomeTaxWithholdingRate) ||
      tax.incomeTaxWithholdingRate < 0 || tax.incomeTaxWithholdingRate > 1 ||
      !Number.isFinite(finance.cnyPerCurrency) || finance.cnyPerCurrency <= 0) return null;
  const usesManualFba = Number.isFinite(draft.fbaFeeLocal) && draft.fbaFeeLocal > 0;
  const selected = usesManualFba ? null : selectFbaRule(draft, finance, rules, now);
  const rule = selected?.rule ?? null;
  const shippingWeightG = usesManualFba
    ? Math.max(draft.unitWeightG,
      draft.lengthCm * draft.widthCm * draft.heightCm / finance.volumeDivisorCm3PerKg * 1000)
    : selected?.shippingWeightG ?? NaN;
  if ((!rule && !usesManualFba) || !Number.isFinite(shippingWeightG) || shippingWeightG <= 0) return null;
  const netRevenueLocal = tax.priceIncludesTax
    ? draft.listedPrice / (1 + tax.salesTaxRate) : draft.listedPrice;
  const salesTaxLocal = tax.priceIncludesTax ? draft.listedPrice - netRevenueLocal : 0;
  const checkoutTaxLocal = tax.priceIncludesTax ? 0 : draft.listedPrice * tax.salesTaxRate;
  const acquisitionLocal = (draft.purchaseCny + draft.processingCny) / finance.cnyPerCurrency;
  const headhaulLocal = shippingWeightG / 1000 * finance.headhaulCnyPerKg / finance.cnyPerCurrency;
  // The seller's freight-forwarder quote is an all-in landed cost in this selection model.
  const importTaxNonrecoverableLocal = 0;
  const importTaxRecoverableLocal = 0;
  const fbaSurchargeLocal = usesManualFba || rule!.includesSurcharge ? 0 : rule!.fee * rule!.surchargeRate;
  const fbaTotal = usesManualFba ? draft.fbaFeeLocal : rule!.fee + fbaSurchargeLocal;
  const referralBase = finance.referralBasis === "买家标价"
    ? draft.listedPrice : netRevenueLocal;
  const referralGross = referralBase * finance.referralRate;
  const includedTax = (gross: number, includesTax: boolean) =>
    includesTax ? gross - gross / (1 + tax.feeTaxRate) : 0;
  const fbaIncludedTax = includedTax(fbaTotal, !usesManualFba && rule!.feeIncludesTax);
  const referralIncludedTax = includedTax(referralGross, finance.referralFeeIncludesTax);
  const platformFeeTaxLocal = fbaIncludedTax + referralIncludedTax;
  const platformFeeTaxRecoverableLocal = tax.feeTaxRecoverable ? platformFeeTaxLocal : 0;
  const platformFeeTaxNonrecoverableLocal = platformFeeTaxLocal - platformFeeTaxRecoverableLocal;
  const incomeTaxWithholdingLocal = netRevenueLocal * tax.incomeTaxWithholdingRate;
  const incomeTaxWithholdingRecoverableLocal = tax.incomeTaxWithholdingRecoverable
    ? incomeTaxWithholdingLocal : 0;
  const incomeTaxWithholdingNonrecoverableLocal = incomeTaxWithholdingLocal -
    incomeTaxWithholdingRecoverableLocal;
  const fbaFeeEconomicLocal = fbaTotal - (tax.feeTaxRecoverable ? fbaIncludedTax : 0);
  const referralFeeEconomicLocal = referralGross -
    (tax.feeTaxRecoverable ? referralIncludedTax : 0);
  const proportionalLoss = finance.adRate + finance.returnLossRate + finance.storageLossRate +
    finance.promotionRate + finance.settlementLossRate;
  const contributionLocal = netRevenueLocal * (1 - proportionalLoss) -
    referralFeeEconomicLocal -
    acquisitionLocal - headhaulLocal - fbaFeeEconomicLocal -
    incomeTaxWithholdingNonrecoverableLocal;
  const capital = acquisitionLocal + headhaulLocal;
  const result: SiteCalculation = {
    shippingWeightG, fbaRule: rule, fbaSurchargeLocal,
    salesTaxLocal, checkoutTaxLocal, netRevenueLocal,
    fbaFeeEconomicLocal, referralFeeEconomicLocal, platformFeeTaxLocal,
    platformFeeTaxRecoverableLocal, platformFeeTaxNonrecoverableLocal,
    incomeTaxWithholdingLocal, incomeTaxWithholdingRecoverableLocal,
    incomeTaxWithholdingNonrecoverableLocal,
    importTaxNonrecoverableLocal,
    importTaxRecoverableLocal, headhaulLocal, acquisitionLocal,
    contributionLocal,
    contributionRate: netRevenueLocal > 0 ? contributionLocal / netRevenueLocal : 0,
    upfrontCapitalRoi: capital > 0 ? contributionLocal / capital : 0,
  };
  return Object.values(result).every((value) => typeof value !== "number" || Number.isFinite(value))
    ? result : null;
}

export function submissionProblems(
  draft: SiteDraft,
  tax: TaxProfile | null,
  finance: FinanceProfile | null,
  calculation: SiteCalculation | null,
  now = Date.now(),
  checkedAt = Date.now(),
): string[] {
  const problems: string[] = [];
  if (!Number.isFinite(forecastTimestamp(draft.expectedSaleDate)))
    problems.push("需填写有效的预计开售日期以匹配FBA费率生效期");
  if (!draft.productName.trim() || !draft.category.trim() || !draft.sourceUrl.trim())
    problems.push("产品名称、品类和采购链接需完整");
  if (!draft.region.trim()) problems.push("适用地区需填写");
  if ([draft.listedPrice, draft.fbaFeeLocal, draft.purchaseCny, draft.lengthCm, draft.widthCm,
      draft.heightCm, draft.unitWeightG].some((value) => !Number.isFinite(value) || value <= 0) ||
      [draft.processingCny]
        .some((value) => !nonnegative(value))) problems.push("售价、采购、包装、重量或手填 FBA 费用无效");
  if ([draft.trademarkRisk, draft.patentRisk, draft.copyrightRisk]
    .some((value) => value !== "无风险") || !draft.complianceNotes.trim())
    problems.push("站点侵权排查尚未完成");
  if (!tax || tax.roughConfigured === false || !activeAt(tax.effectiveFrom, tax.effectiveUntil, now) ||
      tax.currency !== MARKETPLACES[draft.site].currency || !tax.version ||
      !Number.isFinite(tax.salesTaxRate) || tax.salesTaxRate < 0 || tax.salesTaxRate > 1 ||
      !Number.isFinite(tax.incomeTaxWithholdingRate) ||
      tax.incomeTaxWithholdingRate < 0 || tax.incomeTaxWithholdingRate > 1)
    problems.push("该站通用税率测算值未配置");
  if (draft.taxClass !== "标准")
    problems.push("非标准税类不适用通用平均税率，请人工核对后再提审");
  if (!finance || !finance.approved || !activeAt(finance.effectiveFrom, finance.effectiveUntil, now) ||
      !finance.source || !finance.version || finance.targetContributionRate !== 0.25 ||
      !Number.isFinite(finance.cnyPerCurrency) || finance.cnyPerCurrency <= 0 ||
      !Number.isFinite(finance.headhaulCnyPerKg) || finance.headhaulCnyPerKg <= 0 ||
      !Number.isFinite(finance.volumeDivisorCm3PerKg) || finance.volumeDivisorCm3PerKg <= 0 ||
      [finance.referralRate, finance.adRate, finance.returnLossRate,
        finance.storageLossRate, finance.promotionRate, finance.settlementLossRate]
        .some((rate) => !Number.isFinite(rate) || rate < 0 || rate > 1))
    problems.push("该站财务参数尚未获确认");
  if (!calculation) problems.push("站点测算参数或手填 FBA 费用不完整");
  if (calculation && finance && calculation.contributionRate + 1e-9 < finance.targetContributionRate)
    problems.push("毛利率低于25%");
  return problems;
}
