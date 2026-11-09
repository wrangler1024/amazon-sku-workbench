import { describe, expect, it } from "vitest";

import {
  calculateSelection,
  calculationMatches,
  chargeableWeightKg,
  classifyFbaSize,
  volumeWeightKg,
} from "./calculator";
import { evaluateSubmissionGate } from "./gates";
import { displayedNumberTolerance, parseFormulaNumber } from "./formulaValue";
import {
  SAMPLE_INPUT as TEST_INPUT,
  SAMPLE_RATES as TEST_RATES,
  SAMPLE_TIERS as TEST_TIERS,
} from "./sample";

describe("加拿大选品计算器", () => {
  it("按厘米公式计算体积重，不再多除1000", () => {
    const input = { ...TEST_INPUT, lengthCm: 30, widthCm: 20, heightCm: 10 };
    expect(volumeWeightKg(input)).toBeCloseTo(1, 8);
    expect(chargeableWeightKg(input)).toBeCloseTo(1, 8);
  });

  it("正确识别Envelope和Standard边界", () => {
    expect(classifyFbaSize({ ...TEST_INPUT, lengthCm: 38, widthCm: 27, heightCm: 2, weightG: 500 })).toBe("信封Envelope");
    expect(classifyFbaSize({ ...TEST_INPUT, lengthCm: 45, widthCm: 35, heightCm: 20, weightG: 9_000 })).toBe("标准Standard");
  });

  it("超出一期范围时明确判为不支持", () => {
    expect(classifyFbaSize({ ...TEST_INPUT, lengthCm: 46 })).toBe("不支持测算");
  });

  it("CAD 13.99享受低价折扣，CAD 14.00不享受", () => {
    const low = calculateSelection({ ...TEST_INPUT, targetPriceCad: 13.99 }, TEST_RATES, TEST_TIERS);
    const regular = calculateSelection({ ...TEST_INPUT, targetPriceCad: 14 }, TEST_RATES, TEST_TIERS);
    expect(low.lowPriceDiscountCad).toBe(0.8);
    expect(regular.lowPriceDiscountCad).toBe(0);
  });

  it("FBA最终费用在折扣后计算3.5%附加费", () => {
    const result = calculateSelection({ ...TEST_INPUT, targetPriceCad: 13.99 }, TEST_RATES, TEST_TIERS);
    expect(result.fbaBaseFeeCad).toBe(4.71);
    expect(result.fbaSurchargeCad).toBeCloseTo((4.71 - 0.8) * 0.035, 8);
    expect(result.fbaFinalFeeCad).toBeCloseTo((4.71 - 0.8) * 1.035, 8);
  });

  it("标准件超过1500g按每100g向上取整加价", () => {
    const result = calculateSelection(
      { ...TEST_INPUT, lengthCm: 40, widthCm: 20, heightCm: 10, weightG: 1_501 },
      TEST_RATES,
      TEST_TIERS,
    );
    expect(result.sizeTier).toBe("标准Standard");
    expect(result.fbaBaseFeeCad).toBeCloseTo(10.41, 8);
  });

  it("完整成本贡献在预估毛利基础上扣除四项损失", () => {
    const result = calculateSelection(TEST_INPUT, TEST_RATES, TEST_TIERS);
    const lossRate = 0.03 + 0.015 + 0.01 + 0.005;
    expect(result.completeContributionCad).toBeCloseTo(
      result.estimatedProfitCad - TEST_INPUT.targetPriceCad * lossRate,
      8,
    );
  });

  it("正式ROI按完整成本贡献除以采购包装和头程前置资金", () => {
    const result = calculateSelection(TEST_INPUT, TEST_RATES, TEST_TIERS);
    expect(result.upfrontCapitalRoi).toBeCloseTo(
      result.completeContributionCad /
        (result.procurementAndProcessingCad + result.headhaulCad),
      8,
    );
  });

  it("金额和比率均在容差内才通过公式回读校验", () => {
    const result = calculateSelection(TEST_INPUT, TEST_RATES, TEST_TIERS);
    expect(calculationMatches(result, result)).toBe(true);
    expect(calculationMatches(result, { ...result, fbaFinalFeeCad: result.fbaFinalFeeCad + 0.02 })).toBe(false);
    expect(calculationMatches(result, { ...result, upfrontCapitalRoi: result.upfrontCapitalRoi + 0.001 })).toBe(false);
  });

  it("百分比和 ROI 改为显示精度后仍按原计算结果校验", () => {
    const result = calculateSelection(TEST_INPUT, TEST_RATES, TEST_TIERS);
    const displayedRate = `${(result.completeContributionRate * 100).toFixed(2)}%`;
    const displayedRoi = Number(result.upfrontCapitalRoi.toFixed(2));
    const actual = {
      ...result,
      completeContributionRate: parseFormulaNumber(displayedRate) ?? 0,
      upfrontCapitalRoi: displayedRoi,
    };
    const tolerances = {
      completeContributionRate: displayedNumberTolerance(displayedRate, 0.00010001),
      upfrontCapitalRoi: displayedNumberTolerance(displayedRoi, 0.00010001),
    };
    expect(calculationMatches(result, actual, tolerances)).toBe(true);
    expect(calculationMatches(result, { ...actual, upfrontCapitalRoi: displayedRoi + 0.02 }, tolerances)).toBe(false);
  });

  it("硬门槛要求图片、三项无风险、有效FBA和贡献率达标", () => {
    const result = calculateSelection(TEST_INPUT, TEST_RATES, TEST_TIERS);
    expect(evaluateSubmissionGate(TEST_INPUT, result, TEST_RATES, true).passed).toBe(true);
    expect(
      evaluateSubmissionGate(
        { ...TEST_INPUT, patentRisk: "待确认" },
        result,
        TEST_RATES,
        true,
      ).passed,
    ).toBe(false);
    expect(evaluateSubmissionGate(TEST_INPUT, result, TEST_RATES, false).passed).toBe(false);
  });
});
