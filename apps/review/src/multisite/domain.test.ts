import { describe, expect, it } from "vitest";
import {
  calculateSite, EMPTY_SITE_DRAFT, MARKETPLACES, submissionProblems,
  type FbaRateRule, type FinanceProfile, type TaxProfile,
} from "./domain";

const finance: FinanceProfile = {
  site: "UK", category: "家居", cnyPerCurrency: 10, headhaulCnyPerKg: 0,
  referralRate: 0, referralBasis: "净收入", adRate: 0, returnLossRate: 0, storageLossRate: 0,
  referralFeeIncludesTax: false,
  promotionRate: 0, settlementLossRate: 0, targetContributionRate: 0.25,
  volumeDivisorCm3PerKg: 6000, approved: true, effectiveFrom: 0,
  effectiveUntil: null, version: "test", source: "test",
};
const tax: TaxProfile = {
  site: "UK", region: "GB", taxClass: "标准", currency: "GBP",
  salesTaxRate: 0.2, priceIncludesTax: true, collector: "亚马逊代收",
  sellerIsImporter: true, importTaxRecoverable: false, feeTaxRecoverable: false,
  registrationStatus: "已登记",
  registrationEvidence: "税号登记回执", importerDocumentEvidence: "进口商单证",
  importRecoverabilityEvidence: "财务确认不抵扣", feeRecoverabilityEvidence: "财务确认不抵扣",
  importDutyRuleEvidence: "税则资料",
  feeTaxRate: 0.2,
  incomeTaxWithholdingRate: 0, incomeTaxWithholdingRecoverable: false,
  incomeTaxWithholdingRuleEvidence: "", incomeTaxWithholdingRecoveryEvidence: "",
  importChargesIncludedInHeadhaul: false,
  approved: true, effectiveFrom: 0, effectiveUntil: null, version: "test",
  source: "test", approvalEvidence: "test",
};
const rate: FbaRateRule = {
  site: "UK", entitySku: "DEMO-ITEM-005", currency: "GBP", category: "家居",
  quotedPrice: 120, quotedLengthCm: 10, quotedWidthCm: 10,
  quotedHeightCm: 5, quotedWeightG: 200, sizeTier: "标准",
  maxLengthCm: 45, maxMiddleCm: 35, maxShortestCm: 20,
  maxUnitWeightG: 9000, shippingWeightMethod: "max_dimensional",
  minShippingWeightG: 0, maxShippingWeightG: 9000,
  minPrice: 0, maxPrice: 1000, fee: 5, feeIncludesTax: false,
  surchargeRate: 0, includesSurcharge: true,
  approved: true, effectiveFrom: 0, effectiveUntil: null,
  verifiedAt: Date.now(), verifiedBy: "主管", verificationEvidence: "官方报价记录",
  version: "test", source: "test",
};
const draft = {
  ...EMPTY_SITE_DRAFT, entitySku: "DEMO-ITEM-005", site: "UK" as const,
  region: "GB", expectedSaleDate: "2026-09-28", productName: "折叠碗", category: "家居", sourceUrl: "https://example.com",
  listedPrice: 120, purchaseCny: 100, lengthCm: 10, widthCm: 10,
  heightCm: 5, unitWeightG: 200, importTaxLocal: 4,
  customsCode: "3924", importCostEvidence: "清关测算单",
  trademarkRisk: "无风险" as const, patentRisk: "无风险" as const,
  copyrightRisk: "无风险" as const, complianceNotes: "已检索",
};

describe("site tax and contribution", () => {
  it.each(["SA", "JP"] as const)("%s 已填完整但未确认的财务参数不计算也不提审", (site) => {
    const input = { ...draft, site, fbaFeeLocal: 5 };
    const terms = { ...finance, site, headhaulCnyPerKg: 45, approved: false };
    const taxes = { ...tax, site, currency: MARKETPLACES[site].currency };
    expect(calculateSite(input, taxes, terms, [])).toBeNull();
    expect(submissionProblems(input, taxes, terms, null)).toContain("该站财务参数尚未获确认");
    expect(calculateSite(input, taxes, { ...terms, approved: true }, [])).not.toBeNull();
    expect(calculateSite(input, { ...taxes, roughConfigured: false }, { ...terms, approved: true }, [])).toBeNull();
  });
  it.each([
    ["AE", "AED", 105, .05, 1.8210],
    ["UK", "GBP", 120, .20, 9.012],
  ] as const)("%s rollout uses a 6000 headhaul divisor without changing manual FBA", (site, currency, price, vat, fx) => {
    const input = { ...draft, site, listedPrice: price, fbaFeeLocal: 7.5,
      purchaseCny: 20, processingCny: 0, lengthCm: 30, widthCm: 20, heightCm: 10, unitWeightG: 500 };
    const terms = { ...finance, site, cnyPerCurrency: fx, headhaulCnyPerKg: 45,
      volumeDivisorCm3PerKg: 6000, referralRate: .15, referralBasis: "买家标价" as const,
      adRate: .1, returnLossRate: .03, storageLossRate: .015, promotionRate: .01, settlementLossRate: .005 };
    const result = calculateSite(input, { ...tax, site, currency, salesTaxRate: vat, feeTaxRate: 0 }, terms, [])!;
    expect(result.shippingWeightG).toBe(1000);
    expect(result.headhaulLocal).toBeCloseTo(45 / fx);
    expect(result.fbaFeeEconomicLocal).toBe(7.5);
    expect(result.netRevenueLocal).toBeCloseTo(100);
    expect(result.contributionLocal).toBeCloseTo(100 - price * .15 - 16 - 7.5 - 65 / fx);
    expect(result.contributionRate).toBeCloseTo(result.contributionLocal / 100);
    expect(result.platformFeeTaxLocal).toBe(0);
  });

  it("AE uses the current UK basis with 5% sales VAT and no additional service VAT", () => {
    const input = { ...draft, site: "AE" as const, listedPrice: 105, fbaFeeLocal: 7.5,
      purchaseCny: 20, unitWeightG: 200 };
    const aeTax = { ...tax, site: "AE" as const, currency: "AED", salesTaxRate: .05,
      feeTaxRate: 0, importChargesIncludedInHeadhaul: true };
    const terms = { ...finance, site: "AE" as const, cnyPerCurrency: 2,
      headhaulCnyPerKg: 45, referralRate: .15, referralBasis: "买家标价" as const,
      adRate: .1, returnLossRate: .03, storageLossRate: .015, promotionRate: .01,
      settlementLossRate: .005 };
    const result = calculateSite(input, aeTax, terms, [])!;
    expect(result.netRevenueLocal).toBeCloseTo(100);
    expect(result.salesTaxLocal).toBeCloseTo(5);
    expect(result.referralFeeEconomicLocal).toBeCloseTo(15.75);
    expect(result.fbaFeeEconomicLocal).toBe(7.5);
    expect(result.platformFeeTaxLocal).toBe(0);
    expect(result.contributionLocal).toBeCloseTo(46.25);
    expect(result.contributionRate).toBeCloseTo(.4625);
    expect(result.upfrontCapitalRoi).toBeCloseTo(46.25 / 14.5);
    const uk = calculateSite({ ...input, site: "UK", listedPrice: 120 },
      { ...aeTax, site: "UK", currency: "GBP", salesTaxRate: .2 },
      { ...terms, site: "UK" }, [])!;
    expect(uk.netRevenueLocal).toBe(100);
    expect(uk.contributionLocal).toBeCloseTo(44);
  });
  it("matches the Base formula snapshot when the operator enters the total FBA fee", () => {
    const input = { ...draft, fbaFeeLocal: 5 };
    const configured = { ...finance, category: "通用", headhaulCnyPerKg: 45,
      referralRate: .1, adRate: .08, returnLossRate: .02 };
    const result = calculateSite(input, tax, configured, []);
    expect(result?.netRevenueLocal).toBeCloseTo(100);
    expect(result?.salesTaxLocal).toBeCloseTo(20);
    expect(result?.headhaulLocal).toBeCloseTo(.9);
    expect(result?.fbaFeeEconomicLocal).toBe(5);
    expect(result?.contributionLocal).toBeCloseTo(64.1);
    expect(result?.contributionRate).toBeCloseTo(.641);
  });

  it("extracts inclusive VAT once and treats freight as all-in", () => {
    const result = calculateSite(draft, tax, finance, [rate]);
    expect(result?.netRevenueLocal).toBeCloseTo(100);
    expect(result?.salesTaxLocal).toBeCloseTo(20);
    expect(result?.checkoutTaxLocal).toBe(0);
    expect(result?.importTaxNonrecoverableLocal).toBe(0);
    expect(result?.contributionLocal).toBeCloseTo(85);
  });

  it("keeps separately collected checkout tax out of listed price", () => {
    const result = calculateSite(draft, { ...tax, priceIncludesTax: false }, finance, [rate]);
    expect(result?.netRevenueLocal).toBe(120);
    expect(result?.salesTaxLocal).toBe(0);
    expect(result?.checkoutTaxLocal).toBeCloseTo(24);
  });

  it("ignores a separately entered import tax when freight is all-in", () => {
    const result = calculateSite(draft, { ...tax, importTaxRecoverable: true }, finance, [rate]);
    expect(result?.importTaxNonrecoverableLocal).toBe(0);
    expect(result?.importTaxRecoverableLocal).toBe(0);
    expect(result?.contributionLocal).toBeCloseTo(85);
    expect(result?.upfrontCapitalRoi).toBeCloseTo(85 / 10);
  });

  it("uses a site estimate without registration evidence but still checks expiry", () => {
    expect(calculateSite(draft, tax, finance, [{ ...rate, approved: false }])).toBeNull();
    const result = calculateSite(draft, tax, finance, [rate]);
    expect(submissionProblems(draft, { ...tax, approved: false,
      registrationEvidence: "", importerDocumentEvidence: "" }, finance, result))
      .not.toContain("该站通用税率测算值未配置");
    expect(submissionProblems(draft, { ...tax, effectiveUntil: 1 }, finance, result, 2))
      .toContain("该站通用税率测算值未配置");
    expect(submissionProblems(draft, tax, { ...finance, effectiveUntil: 1 }, result, 2))
      .toContain("该站财务参数尚未获确认");
  });

  it("requires manual review for nonstandard tax classes", () => {
    const result = calculateSite(draft, tax, finance, [rate]);
    expect(submissionProblems({ ...draft, taxClass: "零税率" }, tax, finance, result))
      .toContain("非标准税类不适用通用平均税率，请人工核对后再提审");
  });

  it("does not allow a different currency card to match", () => {
    expect(calculateSite(draft, tax, finance, [{ ...rate, currency: "CAD" }])).toBeNull();
  });

  it.each([
    ["MX", "MXN", 116, 0.16, 100],
    ["AU", "AUD", 110, 0.10, 100],
    ["AE", "AED", 105, 0.05, 100],
  ] as const)("extracts %s standard tax from buyer-visible price", (market, currency, price, rateValue, expected) => {
    const input = { ...draft, site: market, listedPrice: price };
    const result = calculateSite(input,
      { ...tax, site: market, currency, salesTaxRate: rateValue },
      { ...finance, site: market },
      [{ ...rate, site: market, currency, quotedPrice: price }]);
    expect(result?.netRevenueLocal).toBeCloseTo(expected);
  });

  it.each(["CA", "US"] as const)("keeps %s checkout tax outside contribution", (market) => {
    const currency = MARKETPLACES[market].currency;
    const input = { ...draft, site: market, listedPrice: 100 };
    const result = calculateSite(input,
      { ...tax, site: market, currency, salesTaxRate: 0, priceIncludesTax: false },
      { ...finance, site: market },
      [{ ...rate, site: market, currency, quotedPrice: 100 }]);
    expect(result?.netRevenueLocal).toBe(100);
    expect(result?.checkoutTaxLocal).toBe(0);
    expect(result?.contributionLocal).toBeCloseTo(85);
  });

  it("separates recoverable tax in an FBA fee and uses configured commission basis", () => {
    const result = calculateSite(draft,
      { ...tax, feeTaxRecoverable: true, feeTaxRate: 0.2 },
      { ...finance, referralRate: 0.1, referralBasis: "买家标价" },
      [{ ...rate, fee: 6, feeIncludesTax: true }]);
    expect(result?.fbaFeeEconomicLocal).toBeCloseTo(5);
    expect(result?.contributionLocal).toBeCloseTo(73);
  });

  it("records FBA and referral input tax separately without double expensing it", () => {
    const quote = [{ ...rate, fee: 6, feeIncludesTax: true }];
    const terms = { ...finance, referralRate: 0.1, referralFeeIncludesTax: true };
    const recoverable = calculateSite(draft,
      { ...tax, feeTaxRecoverable: true }, terms, quote);
    expect(recoverable?.platformFeeTaxLocal).toBeCloseTo(8 / 3);
    expect(recoverable?.platformFeeTaxRecoverableLocal).toBeCloseTo(8 / 3);
    expect(recoverable?.platformFeeTaxNonrecoverableLocal).toBe(0);
    expect(recoverable?.fbaFeeEconomicLocal).toBeCloseTo(5);
    expect(recoverable?.referralFeeEconomicLocal).toBeCloseTo(25 / 3);
    expect(recoverable?.contributionLocal).toBeCloseTo(230 / 3);

    const nonrecoverable = calculateSite(draft, tax, terms, quote);
    expect(nonrecoverable?.platformFeeTaxLocal).toBeCloseTo(8 / 3);
    expect(nonrecoverable?.platformFeeTaxRecoverableLocal).toBe(0);
    expect(nonrecoverable?.platformFeeTaxNonrecoverableLocal).toBeCloseTo(8 / 3);
    expect(nonrecoverable?.contributionLocal).toBeCloseTo(74);
  });

  it("models Mexico without RFC: withheld IVA is already excluded and 20% ISR is separate", () => {
    const input = { ...draft, site: "MX" as const, region: "MX", listedPrice: 116,
      importTaxLocal: 0, importDutyLocal: 0 };
    const mxTax: TaxProfile = { ...tax, site: "MX", region: "MX", currency: "MXN",
      registrationStatus: "未登记", collector: "亚马逊代收", salesTaxRate: .16,
      incomeTaxWithholdingRate: .20, importChargesIncludedInHeadhaul: true };
    const mxRate = { ...rate, site: "MX" as const, currency: "MXN", quotedPrice: 116 };
    const result = calculateSite(input, mxTax, { ...finance, site: "MX" }, [mxRate]);
    expect(result?.netRevenueLocal).toBeCloseTo(100);
    expect(result?.salesTaxLocal).toBeCloseTo(16);
    expect(result?.incomeTaxWithholdingLocal).toBeCloseTo(20);
    expect(result?.contributionLocal).toBeCloseTo(65);
    expect(submissionProblems(input, mxTax, { ...finance, site: "MX" }, result))
      .not.toContain("该站通用税率测算值未配置");
    expect(calculateSite({ ...input, importTaxLocal: 3 }, mxTax,
      { ...finance, site: "MX" }, [mxRate])?.contributionLocal).toBeCloseTo(65);
  });

  it("rejects an official quote for a different price or package size", () => {
    expect(calculateSite({ ...draft, listedPrice: 121 }, tax, finance, [rate])).toBeNull();
    expect(calculateSite({ ...draft, widthCm: 12 }, tax, finance, [rate])).toBeNull();
  });

  it("uses the handfilled FBA amount for submission while retaining old quote calculations as fallback", () => {
    const manual = { ...draft, fbaFeeLocal: 5 };
    const manualCalc = calculateSite(manual, tax, finance, []);
    expect(manualCalc?.fbaFeeEconomicLocal).toBeCloseTo(5);
    expect(submissionProblems(manual, tax, { ...finance, headhaulCnyPerKg: 45 }, manualCalc))
      .not.toContain("缺少匹配本站点、SKU、售价、尺寸及开售期的已核准官方FBA报价");
    expect(submissionProblems(draft, tax, finance, calculateSite(draft, tax, finance, [rate])))
      .toContain("售价、采购、包装、重量或手填 FBA 费用无效");
    const surcharge = calculateSite(draft, tax, finance,
      [{ ...rate, fee: 5, surchargeRate: 0.035, includesSurcharge: false }]);
    expect(surcharge?.fbaSurchargeLocal).toBeCloseTo(0.175);
    const included = calculateSite(draft, tax, finance,
      [{ ...rate, fee: 5.175, surchargeRate: 0.035, includesSurcharge: true }]);
    expect(included?.fbaSurchargeLocal).toBe(0);
    expect(included?.fbaFeeEconomicLocal).toBeCloseTo(surcharge!.fbaFeeEconomicLocal);
  });
  it("选品SKU可留空且不限制公司四段格式；利润待计算仍拦截", () => {
    const input = { ...draft, entitySku: "TEST-SKU-7", fbaFeeLocal: 5 };
    const terms = { ...finance, headhaulCnyPerKg: 45 };
    const result = calculateSite(input, tax, terms, []);
    expect(submissionProblems(input, tax, terms, result))
      .not.toContain("实体SKU编码不符合公司四段规范");
    expect(submissionProblems({ ...input, entitySku: " " }, tax, terms, result))
      .not.toContain("请填写选品SKU");
    expect(submissionProblems(input, tax, terms, null))
      .toContain("站点测算参数或手填 FBA 费用不完整");
  });
});
