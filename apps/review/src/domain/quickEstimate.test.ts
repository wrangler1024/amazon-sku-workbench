import { describe, expect, it } from "vitest";

import { SAMPLE_RATES } from "./sample";
import {
  calculateQuickEstimate,
  meetsQuickScreeningRate,
  quickEstimateError,
  quickInputFromRates,
  quickScreeningRate,
} from "./quickEstimate";

const input = {
  salePriceCad: 20,
  purchaseCostCny: 30,
  headhaulCny: 5,
  fbaFeeCad: 5,
  marketingAfterSalesRate: 0.13,
  cadPerCny: 0.2,
  referralFeeRate: 0.15,
};

describe("快速机会粗算", () => {
  it("按混合币种计算销售佣金、利润、利润率和前置资金ROI", () => {
    const result = calculateQuickEstimate(input);
    expect(result).not.toBeNull();
    expect(result?.salesRevenueCny).toBeCloseTo(100, 8);
    expect(result?.salesCommissionCad).toBeCloseTo(3, 8);
    expect(result?.salesCommissionCny).toBeCloseTo(15, 8);
    expect(result?.fbaFeeCny).toBeCloseTo(25, 8);
    expect(result?.marketingAfterSalesCostCny).toBeCloseTo(13, 8);
    expect(result?.quickProfitCny).toBeCloseTo(12, 8);
    expect(result?.quickMarginRate).toBeCloseTo(0.12, 8);
    expect(result?.upfrontCapitalRoi).toBeCloseTo(12 / 35, 8);
  });

  it("从参数快照带出汇率、佣金率和13%营销及售后默认值", () => {
    const initial = quickInputFromRates(SAMPLE_RATES);
    expect(initial.cadPerCny).toBe(SAMPLE_RATES.cadPerCny);
    expect(initial.referralFeeRate).toBe(SAMPLE_RATES.referralFeeRate);
    expect(initial.marketingAfterSalesRate).toBeCloseTo(0.13, 8);
    expect(initial.salePriceCad).toBeNull();
  });

  it("粗算参考线由正式目标加三项未覆盖损失动态组成", () => {
    expect(quickScreeningRate(SAMPLE_RATES)).toBeCloseTo(0.28, 8);
    expect(meetsQuickScreeningRate(0.2799, 0.28)).toBe(false);
    expect(meetsQuickScreeningRate(0.28, 0.28)).toBe(true);
  });

  it("缺少任意粗算金额时不把空值当成0", () => {
    const empty = quickInputFromRates(SAMPLE_RATES);
    expect(calculateQuickEstimate(empty)).toBeNull();
    expect(quickEstimateError(empty)).toContain("请填写");
  });

  it("拒绝负数、无效汇率和ROI分母为0", () => {
    expect(quickEstimateError({ ...input, fbaFeeCad: -1 })).toContain("不得为负数");
    expect(quickEstimateError({ ...input, cadPerCny: 0 })).toContain("汇率无效");
    expect(
      quickEstimateError({ ...input, purchaseCostCny: 0, headhaulCny: 0 }),
    ).toContain("必须大于0");
  });
});
