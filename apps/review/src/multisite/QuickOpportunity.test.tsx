import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import QuickOpportunity from "./QuickOpportunity";
import { calculateSite, newSiteDraft, MARKETPLACES, type FinanceProfile,
  type Marketplace, type TaxProfile } from "./domain";

afterEach(cleanup);

describe("粗算展示与正式评审口径", () => {
  it.each([
    { site: "CA", price: 100, salesTax: 0, withholding: 0, exchange: 4.8137 },
    { site: "MX", price: 116, salesTax: .16, withholding: .2, exchange: .391 },
    { site: "UK", price: 120, salesTax: .2, withholding: 0, exchange: 9.012 },
    { site: "US", price: 100, salesTax: 0, withholding: 0, exchange: 6.7094 },
    { site: "AU", price: 110, salesTax: .1, withholding: 0, exchange: 4.7877 },
    { site: "AE", price: 105, salesTax: .05, withholding: 0, exchange: 2 },
  ])("$site 头程保留人民币，损耗展示及 ROI 与同成本正式测算一致", (example) => {
    const site = example.site as Marketplace;
    const currency = MARKETPLACES[site].currency;
    const tax: TaxProfile = {
      site, currency, region: "全站", taxClass: "标准", roughConfigured: true,
      salesTaxRate: example.salesTax, priceIncludesTax: example.salesTax > 0,
      collector: "亚马逊代收", sellerIsImporter: false, registrationStatus: "未登记",
      registrationEvidence: "", importerDocumentEvidence: "",
      importTaxRecoverable: false, importRecoverabilityEvidence: "",
      feeTaxRecoverable: false, feeRecoverabilityEvidence: "", importDutyRuleEvidence: "",
      feeTaxRate: 0, incomeTaxWithholdingRate: example.withholding,
      incomeTaxWithholdingRecoverable: false, incomeTaxWithholdingRuleEvidence: "",
      incomeTaxWithholdingRecoveryEvidence: "", importChargesIncludedInHeadhaul: true,
      approved: true, effectiveFrom: 0, effectiveUntil: null,
      version: "test", source: "test", approvalEvidence: "test",
    };
    const finance: FinanceProfile = {
      site, currency, category: "通用", roughConfigured: true,
      cnyPerCurrency: example.exchange, headhaulCnyPerKg: 50, volumeDivisorCm3PerKg: 5000,
      referralRate: .15, referralBasis: "买家标价", referralFeeIncludesTax: false,
      adRate: .1, returnLossRate: .05, storageLossRate: 0, promotionRate: 0,
      settlementLossRate: 0, targetContributionRate: .25, approved: true,
      effectiveFrom: 0, effectiveUntil: null, version: "test", source: "test",
    };
    const draft = { ...newSiteDraft(site), category: "通用", listedPrice: example.price,
      purchaseCny: 100, processingCny: 0, fbaFeeLocal: 5,
      lengthCm: 20, widthCm: 10, heightCm: 5, unitWeightG: 400 };
    const formal = calculateSite(draft, tax, finance, [])!;
    render(<QuickOpportunity site={site} tax={tax} finance={finance} purchaseCny={100}
      onPurchaseChange={vi.fn()} onCarry={vi.fn()} disabled={false} sitePicker={null} cards={[]} />);
    for (const [label, value] of [
      [`售价（${currency}）`, example.price], ["包装长（cm）", 20], ["包装宽（cm）", 10],
      ["包装高（cm）", 5], ["包装重量（g）", 400], [`FBA 费用（${currency}）`, 5],
    ] as const) {
      fireEvent.change(screen.getByLabelText(label), { target: { value: String(value) } });
    }
    expect(formal.netRevenueLocal).toBeCloseTo(100);
    expect(screen.getByRole("status", { name: "包装与配送尺寸参考" })).toHaveTextContent("头程 ¥20.00");
    expect(screen.getByText(/^广告营销$/)).toHaveTextContent(`广告营销 ${currency} 10.00`);
    expect(screen.getByText(/^售后退货$/)).toHaveTextContent(`售后退货 ${currency} 5.00`);
    expect(screen.getByText(/^粗算利润\/件$/)).toHaveTextContent(
      `粗算利润/件 ${currency} ${formal.contributionLocal.toFixed(2)}`);
    expect(screen.getByText(/^前置资金 ROI$/)).toHaveTextContent(
      `前置资金 ROI ${(formal.upfrontCapitalRoi * 100).toFixed(2)}%`);
  });
});

describe("粗算损耗占比默认值", () => {
  const finance: FinanceProfile = {
    site: "US", currency: "USD", category: "通用", roughConfigured: true,
    cnyPerCurrency: 6.7094, headhaulCnyPerKg: 50, volumeDivisorCm3PerKg: 5000,
    referralRate: .15, referralBasis: "买家标价", referralFeeIncludesTax: false,
    adRate: .1, returnLossRate: .03, storageLossRate: .015, promotionRate: .01,
    settlementLossRate: .005, targetContributionRate: .25, approved: true,
    effectiveFrom: 0, effectiveUntil: null, version: "test", source: "test",
  };
  const view = (profile: FinanceProfile | null) =>
    <QuickOpportunity site="US" tax={null} finance={profile} purchaseCny={null}
      onPurchaseChange={vi.fn()} onCarry={vi.fn()} disabled={false} sitePicker={null} cards={[]} />;

  it("异步加载配置后填默认值，未手改字段随配置刷新", () => {
    const { rerender } = render(view(null));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(null);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(null);
    rerender(view(finance));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(10);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(3);
    fireEvent.change(screen.getByLabelText("广告营销占比（%）"), { target: { value: "18" } });
    rerender(view({ ...finance, adRate: .12, returnLossRate: .04 }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(18);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(4);
  });

  it("保留手动输入的零和主动清空，不被刷新覆盖", () => {
    const { rerender } = render(view(finance));
    fireEvent.change(screen.getByLabelText("广告营销占比（%）"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("售后退货占比（%）"), { target: { value: "" } });
    rerender(view({ ...finance, adRate: .2, returnLossRate: .05 }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(0);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(null);
  });

  it("有效零费率显示零，非法费率保持空白", () => {
    const { rerender } = render(view({ ...finance, adRate: 0, returnLossRate: 0 }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(0);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(0);
    rerender(view({ ...finance, adRate: NaN, returnLossRate: -.03 }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(null);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(null);
  });

  it("不借用其他站点或错误币种的默认费率", () => {
    const { rerender } = render(view({ ...finance, site: "CA", currency: "CAD" }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(null);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(null);
    rerender(view({ ...finance, currency: "CAD" }));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(null);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(null);
    rerender(view(finance));
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(10);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(3);
  });
});
