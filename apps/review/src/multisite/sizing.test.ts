import { describe, expect, it } from "vitest";
import type { FbaCard } from "./rateCards";
import { packagingOpportunity, smallSizeAdvice, type SmallSizeRules } from "./sizing";

const rules: SmallSizeRules = { CA: { source: "https://example.com/ca", tiers: [
  { name: "信封档", dimensions: [38, 27, 2], weightG: 500 }] } };

const shared: FbaCard = {
  key: "standard", site: "CA", plan: "常规", tier: "标准Standard", category: "普通商品",
  priceMin: 0, priceMax: null, maxDimsCm: [45, 35, 20], maxUnitWeightG: 9000,
  shippingMethod: "max_dimensional", volumeDivisor: 5000, shippingWeightStepG: 100,
  weightMinG: 200, weightMaxG: 300, baseFee: 6.36,
  surchargeRate: .035, includesSurcharge: false, feeIncludesTax: false,
  effectiveFrom: 0, effectiveUntil: null, version: "CA-test", source: "Amazon",
  verification: "样例", priority: 2,
};
const cards: FbaCard[] = [shared, { ...shared, key: "envelope", tier: "信封Envelope",
  maxDimsCm: [38, 27, 2], maxUnitWeightG: 500, weightMinG: 100, weightMaxG: 200,
  baseFee: 4.71, priority: 1 }];
const request = { site: "CA" as const, category: "普通商品" as const, price: 25,
  dimensionsCm: [26, 18, 2.3] as [number, number, number], unitWeightG: 180,
  saleDate: Date.parse("2026-09-29T12:00:00+08:00") };

describe("包装尺寸参考和配送费优化", () => {
  it("把跨档试包节省额和仓库目标尺寸算到每件，同时不把小件档当提审门槛", () => {
    expect(smallSizeAdvice(rules, "CA", request.dimensionsCm, 180).result).toContain("超出");
    const saving = packagingOpportunity(cards, request, rules);
    expect(saving).toMatchObject({ currentFee: 6.58, trialFee: 4.87,
      savingPerUnit: 1.71, trialDimensions: [26, 18, 2] });
    expect(saving?.reductions).toEqual(["最短边减 0.3 cm"]);
  });

  it("包装已在小档、远超尺寸或无唯一适用费率时不猜测节省额", () => {
    expect(packagingOpportunity(cards, { ...request, dimensionsCm: [26, 18, 2] }, rules)).toBeNull();
    expect(packagingOpportunity(cards, { ...request, dimensionsCm: [50, 40, 25] }, rules)).toBeNull();
    expect(packagingOpportunity([...cards, cards[0]], request, rules)).toBeNull();
  });

  it("缺少站点规则时明确提示待配置，不套用其他站点尺寸", () => {
    expect(smallSizeAdvice({}, "AU", [20, 15, 1], 70).result).toBe("本站尺寸规则待配置");
    expect(packagingOpportunity(cards, request, {})).toBeNull();
  });
});
