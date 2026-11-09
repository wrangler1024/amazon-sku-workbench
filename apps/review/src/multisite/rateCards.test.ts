import { describe, expect, it } from "vitest";
import data from "../../data/fba-rate-cards-2026.json";
import { calculateRoughOpportunity, estimateFbaCard, type FbaCard, type RoughInput } from "./rateCards";
import type { Marketplace } from "./domain";

const cards: FbaCard[] = data.rows.map((row) => ({ ...row,
  site: row.site as Marketplace,
  maxDimsCm: row.maxDimsCm as [number, number, number],
  shippingMethod: row.shippingMethod as FbaCard["shippingMethod"],
  effectiveFrom: Date.parse(row.effectiveFrom),
  effectiveUntil: row.effectiveUntil ? Date.parse(row.effectiveUntil) : null,
}));
const request = (site: Marketplace, price: number, date = "2026-09-28") => ({
  site, price, saleDate: Date.parse(date), category: "普通商品" as const,
  dimensionsCm: [20, 15, 1] as [number, number, number], unitWeightG: 50,
});

describe("粗算前置资金 ROI", () => {
  const input: RoughInput = {
    listedPrice: 9, priceIncludesSalesTax: false, salesTaxRate: 0,
    cnyPerLocal: 6.7094, purchaseCny: 4, headhaulCny: 4, fbaFeeLocal: 4,
    fbaFeeIncludesTax: false, referralRate: .15, referralBasis: "买家标价",
    marketingLossRate: .15, importDutyLocal: 0, importTaxLocal: 0,
    platformFeeTaxRate: 0, incomeTaxWithholdingRate: 0,
    importChargesIncludedInHeadhaul: true,
  };

  it("美国站已核实样例先换算人民币成本，FBA 仅扣利润不占前置资金", () => {
    const result = calculateRoughOpportunity(input)!;
    expect(result.contribution).toBeCloseTo(1.1076430083);
    expect(result.contributionRate).toBeCloseTo(.1230714454);
    expect(result.upfrontRoi).toBeCloseTo(.9289525);
  });

  it("只有 FBA 费用而无前置投入时不生成 ROI", () => {
    const result = calculateRoughOpportunity({ ...input, purchaseCny: 0, headhaulCny: 0 });
    expect(result?.upfrontRoi).toBeNull();
  });
});

describe("官方分档只作预估", () => {
  it("沙特低价边界保守处理，出库计费重未取得不估费", () => {
    expect(estimateFbaCard(cards, request("SA", 24.99)).reason).toContain("取得计费重量");
    for (const [price, fee] of [[24.99, 5.5], [25.01, 7.5]]) {
      const result = estimateFbaCard(cards, { ...request("SA", price), manualShippingWeightG: 100 });
      expect(result.fee).toBe(fee);
      expect(result.card?.feeIncludesTax).toBe(false);
    }
    expect(estimateFbaCard(cards, { ...request("SA", 25), manualShippingWeightG: 100 }).reason).toContain("低价资格");
    expect(estimateFbaCard(cards, { ...request("SA", 25.001), manualShippingWeightG: 100 }).status).toBe("quote_required");
    expect(estimateFbaCard(cards, { ...request("SA", 30), dimensionsCm: [45, 34, 26], unitWeightG: 12000,
      manualShippingWeightG: 12000 }).fee).toBe(22);
  });
  it("日本整数日元1000归低价，薄件范围之外人工核价，费用已含税", () => {
    const req = request("JP", 1000, "2026-10-29");
    expect(estimateFbaCard(cards, req).fee).toBe(222);
    expect(estimateFbaCard(cards, { ...req, price: 1001 }).fee).toBe(288);
    const thin = { ...req, dimensionsCm: [35, 30, 3.3] as [number, number, number], unitWeightG: 1000 };
    expect(estimateFbaCard(cards, thin).fee).toBe(252);
    expect(estimateFbaCard(cards, { ...thin, price: 1001 }).fee).toBe(318);
    expect(estimateFbaCard(cards, thin).card?.feeIncludesTax).toBe(true);
    expect(estimateFbaCard(cards, { ...thin, dimensionsCm: [35, 30, 3.31] }).status).toBe("quote_required");
    expect(estimateFbaCard(cards, { ...thin, unitWeightG: 1001 }).status).toBe("quote_required");
    expect(estimateFbaCard(cards, { ...req, price: 1000.5 }).reason).toContain("整数日元");
  });
  it("阿联酋先核实出库计费重，25 AED 归低价，25.01 AED 归常规", () => {
    expect(estimateFbaCard(cards, request("AE", 25)).reason).toContain("取得计费重量");
    for (const [price, fee] of [[24.99, 5.5], [25, 5.5], [25.01, 7.5]]) {
      const result = estimateFbaCard(cards, { ...request("AE", price), manualShippingWeightG: 100 });
      expect(result.fee).toBe(fee);
      expect(result.card?.feeIncludesTax).toBe(false);
    }
    expect(estimateFbaCard(cards, { ...request("AE", 25.001), manualShippingWeightG: 100 }).status)
      .toBe("quote_required");
  });

  it("阿联酋包材后的计费重跨档与超尺寸不会套用小信封价", () => {
    expect(estimateFbaCard(cards, { ...request("AE", 30), manualShippingWeightG: 101 }).fee).toBe(8.2);
    expect(estimateFbaCard(cards, { ...request("AE", 30), dimensionsCm: [33, 23, 2.5],
      unitWeightG: 450, manualShippingWeightG: 500 }).fee).toBe(8.5);
    expect(estimateFbaCard(cards, { ...request("AE", 30), dimensionsCm: [33, 23, 5],
      unitWeightG: 950, manualShippingWeightG: 1000 }).fee).toBe(7.5);
    expect(estimateFbaCard(cards, { ...request("AE", 30), dimensionsCm: [45, 34, 26],
      unitWeightG: 12000, manualShippingWeightG: 12000 }).fee).toBe(21.5);
    expect(estimateFbaCard(cards, { ...request("AE", 30), dimensionsCm: [46, 34, 26],
      manualShippingWeightG: 500 }).status).toBe("quote_required");
  });
  it("墨西哥按四个价段匹配，未知计费重要求逐 SKU 报价", () => {
    expect(estimateFbaCard(cards, request("MX", 149)).status).toBe("quote_required");
    for (const [price, fee] of [[149, 27], [150, 33], [299, 49], [499, 60]]) {
      const result = estimateFbaCard(cards, { ...request("MX", price), manualShippingWeightG: 100 });
      expect(result.card?.baseFee).toBe(fee);
      expect(result.card?.feeIncludesTax).toBe(true);
    }
  });

  it("英国低价与普通档、旺季和普通品类门槛不串档", () => {
    const low = estimateFbaCard(cards, request("UK", 10));
    expect(low.card?.plan).toBe("低价");
    expect(low.card?.baseFee).toBe(1.52);
    const normal = estimateFbaCard(cards, request("UK", 20));
    expect(normal.card?.baseFee).toBe(1.89);
    const envelope = { ...request("UK", 21), dimensionsCm: [30, 20, 3] as [number, number, number],
      unitWeightG: 500 };
    expect(estimateFbaCard(cards, envelope).card?.baseFee).toBe(2.72);
    const peak = estimateFbaCard(cards, { ...envelope, saleDate: Date.parse("2026-10-16") });
    expect(peak.card?.plan).toBe("旺季");
    expect(peak.card?.baseFee).toBe(2.79);
    expect(peak.fee).toBeCloseTo(2.79 * 1.015);
    expect(estimateFbaCard(cards, { ...request("UK", 10),
      category: "特殊品类或危险品" }).status).toBe("quote_required");
  });

  it("美国低价、常规、旺季价格与重量边界", () => {
    expect(estimateFbaCard(cards, request("US", 9.99)).card?.baseFee).toBe(2.43);
    expect(estimateFbaCard(cards, request("US", 10)).card?.baseFee).toBe(3.32);
    expect(estimateFbaCard(cards, request("US", 50.01)).card?.baseFee).toBe(3.58);
    expect(estimateFbaCard(cards, request("US", 10, "2026-10-16")).card?.baseFee).toBe(3.51);
    expect(estimateFbaCard(cards, request("US", 10)).fee).toBeCloseTo(3.32 * 1.035);
    expect(estimateFbaCard(cards, request("US", 10, "2027-01-15")).status).toBe("quote_required");
    const atTwoOz = estimateFbaCard(cards, { ...request("US", 10), unitWeightG: 2 * 28.349523125 });
    expect(atTwoOz.card?.baseFee).toBe(3.32);
    const overTwoOz = estimateFbaCard(cards, { ...request("US", 10), unitWeightG: 2 * 28.349523125 + 1 });
    expect(overTwoOz.card?.baseFee).toBe(3.42);
    expect(estimateFbaCard(cards, { ...request("US", 10), dimensionsCm: [100, 30, 20] }).status)
      .toBe("quote_required");
  });

  it("澳洲低价档与 GST 口径，不猜未公布旺季", () => {
    expect(estimateFbaCard(cards, { ...request("AU", 12), unitWeightG: 70 }).card?.baseFee).toBe(3.64);
    expect(estimateFbaCard(cards, { ...request("AU", 13), unitWeightG: 70 }).card?.baseFee).toBe(4.55);
    expect(estimateFbaCard(cards, request("AU", 13)).card?.feeIncludesTax).toBe(false);
    expect(cards.some((card) => card.site === "AU" && card.plan.includes("旺季"))).toBe(false);
  });

  it("含税标价只拆一次，进口税与平台费税按不可抵扣成本计算", () => {
    const base = { listedPrice: 120, priceIncludesSalesTax: true, salesTaxRate: .2,
      cnyPerLocal: 10, purchaseCny: 100, headhaulCny: 0, fbaFeeLocal: 10,
      fbaFeeIncludesTax: false, referralRate: .1, referralBasis: "净收入" as const,
      marketingLossRate: 0,
      importDutyLocal: 1, importTaxLocal: 2, platformFeeTaxRate: .2,
      incomeTaxWithholdingRate: 0, importChargesIncludedInHeadhaul: false };
    const result = calculateRoughOpportunity(base);
    expect(result?.revenue).toBe(100);
    expect(result?.feeTax).toBe(4);
    expect(result?.contribution).toBe(63);
    expect(calculateRoughOpportunity({ ...base, referralBasis: "买家标价" })?.contribution)
      .toBeCloseTo(60.6);
    expect(calculateRoughOpportunity({ ...base, fbaFeeIncludesTax: true })?.feeTax).toBe(2);
  });

  it("墨西哥无 RFC 情景从税前收入单列 20% 预提，货代全包时不重计进口税", () => {
    const input = { listedPrice: 116, priceIncludesSalesTax: true, salesTaxRate: .16,
      cnyPerLocal: 10, purchaseCny: 100, headhaulCny: 20, fbaFeeLocal: 5,
      fbaFeeIncludesTax: false, referralRate: 0, referralBasis: "净收入" as const,
      marketingLossRate: 0,
      importDutyLocal: 0, importTaxLocal: 0, platformFeeTaxRate: 0,
      incomeTaxWithholdingRate: .2, importChargesIncludedInHeadhaul: true };
    const result = calculateRoughOpportunity(input);
    expect(result?.revenue).toBeCloseTo(100);
    expect(result?.incomeTaxWithholding).toBeCloseTo(20);
    expect(result?.contribution).toBeCloseTo(63);
    expect(calculateRoughOpportunity({ ...input, importTaxLocal: 1 })?.contribution)
      .toBeCloseTo(63);
  });
});
