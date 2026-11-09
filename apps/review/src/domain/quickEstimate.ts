import type {
  QuickEstimateInput,
  QuickEstimateResult,
  RateSnapshot,
} from "./types";

const EPSILON = 1e-9;

const REQUIRED_INPUTS: Array<{
  key: keyof Pick<
    QuickEstimateInput,
    | "salePriceCad"
    | "purchaseCostCny"
    | "headhaulCny"
    | "fbaFeeCad"
    | "marketingAfterSalesRate"
  >;
  label: string;
}> = [
  { key: "salePriceCad", label: "售价" },
  { key: "purchaseCostCny", label: "采购成本" },
  { key: "headhaulCny", label: "头程运费" },
  { key: "fbaFeeCad", label: "FBA费用" },
  { key: "marketingAfterSalesRate", label: "营销及售后成本占比" },
];

export function quickInputFromRates(rates: RateSnapshot | null): QuickEstimateInput {
  return {
    salePriceCad: null,
    purchaseCostCny: null,
    headhaulCny: null,
    fbaFeeCad: null,
    marketingAfterSalesRate: rates ? rates.adRate + rates.returnLossRate : null,
    cadPerCny: rates?.cadPerCny ?? 0,
    referralFeeRate: rates?.referralFeeRate ?? 0,
  };
}

export function quickScreeningRate(rates: RateSnapshot): number {
  return (
    rates.targetContributionRate +
    rates.storageRemovalDefectRate +
    rates.promotionRate +
    rates.fxSettlementLossRate
  );
}

export function meetsQuickScreeningRate(
  marginRate: number,
  screeningRate: number,
): boolean {
  return marginRate + EPSILON >= screeningRate;
}

export function quickEstimateError(input: QuickEstimateInput): string | null {
  const missing = REQUIRED_INPUTS
    .filter(({ key }) => input[key] === null)
    .map(({ label }) => label);
  if (missing.length) return `请填写${missing.join("、")}后查看粗算结果`;
  if (!Number.isFinite(input.cadPerCny) || input.cadPerCny <= 0) {
    return "当前汇率无效，请由主管检查站点财务参数";
  }
  if (
    !Number.isFinite(input.referralFeeRate) ||
    input.referralFeeRate < 0 ||
    input.referralFeeRate > 1
  ) {
    return "当前销售佣金率无效，请由主管检查站点财务参数";
  }
  if ((input.salePriceCad ?? 0) <= 0) return "售价必须大于0";
  if (
    (input.purchaseCostCny ?? 0) < 0 ||
    (input.headhaulCny ?? 0) < 0 ||
    (input.fbaFeeCad ?? 0) < 0
  ) {
    return "采购成本、头程运费和FBA费用不得为负数";
  }
  if (
    (input.marketingAfterSalesRate ?? 0) < 0 ||
    (input.marketingAfterSalesRate ?? 0) > 1
  ) {
    return "营销及售后成本占比必须在0%至100%之间";
  }
  if ((input.purchaseCostCny ?? 0) + (input.headhaulCny ?? 0) <= 0) {
    return "采购成本与头程运费合计必须大于0，才能计算前置资金ROI";
  }
  return null;
}

export function calculateQuickEstimate(
  input: QuickEstimateInput,
): QuickEstimateResult | null {
  if (quickEstimateError(input)) return null;
  const salePriceCad = input.salePriceCad as number;
  const purchaseCostCny = input.purchaseCostCny as number;
  const headhaulCny = input.headhaulCny as number;
  const fbaFeeCad = input.fbaFeeCad as number;
  const marketingAfterSalesRate = input.marketingAfterSalesRate as number;
  const salesRevenueCny = salePriceCad / input.cadPerCny;
  const salesCommissionCad = salePriceCad * input.referralFeeRate;
  const salesCommissionCny = salesCommissionCad / input.cadPerCny;
  const fbaFeeCny = fbaFeeCad / input.cadPerCny;
  const marketingAfterSalesCostCny =
    salesRevenueCny * marketingAfterSalesRate;
  const quickProfitCny =
    salesRevenueCny -
    purchaseCostCny -
    headhaulCny -
    fbaFeeCny -
    salesCommissionCny -
    marketingAfterSalesCostCny;
  const result = {
    salesRevenueCny,
    salesCommissionCad,
    salesCommissionCny,
    fbaFeeCny,
    marketingAfterSalesCostCny,
    quickProfitCny,
    quickMarginRate: quickProfitCny / salesRevenueCny,
    upfrontCapitalRoi: quickProfitCny / (purchaseCostCny + headhaulCny),
  };
  return Object.values(result).every(Number.isFinite) ? result : null;
}
