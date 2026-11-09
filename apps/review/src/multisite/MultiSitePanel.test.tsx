import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MultiSitePanel from "./MultiSitePanel";
import { newSiteDraft, type FinanceProfile, type SiteDraft, type TaxProfile } from "./domain";
import type { UserAccess } from "../domain/types";
import type { SiteDashboard, SiteReview } from "./repository";
import type { FbaCard } from "./rateCards";
import cardData from "../../data/fba-rate-cards-2026.json";

const { mockPreferenceLoad, mockPreferenceSave } = vi.hoisted(() => ({
  mockPreferenceLoad: vi.fn(), mockPreferenceSave: vi.fn(async () => "cloud"),
}));
vi.mock("./sitePreference", () => ({ loadSitePreference: mockPreferenceLoad, saveSitePreference: mockPreferenceSave }));
const remembered = (site: "CA" | "AU" | "UK" | "AE" | null, userId = "operator-1") => ({
  scope: { baseId: "base-1", userId }, storage: "cloud",
  preference: site ? { version: 1, baseId: "base-1", userId, site, savedAt: 1 } : null,
});
beforeEach(() => { mockPreferenceLoad.mockReset().mockResolvedValue(remembered("CA")); });

const mockLoad = vi.fn(async (): Promise<SiteDashboard> => ({
  reviews: [], taxes: [], finances: [], rules: [],
  sizeRules: { CA: { source: "https://example.com/ca", tiers: [
    { name: "信封档", dimensions: [38, 27, 2], weightG: 500 }] } }, problems: [],
}));
const mockSave = vi.fn(async (_draft: SiteDraft, _access: UserAccess,
  _id: string | null, _image?: File | null) => "rec-draft");
const mockSubmit = vi.fn(async () => {});
const mockDecide = vi.fn(async () => {});
const mockOpenRecord = vi.fn(async () => {});
const mockCards = vi.fn(async (): Promise<FbaCard[]> => []);
vi.mock("./repository", () => ({
  SubmissionPendingError: class SubmissionPendingError extends Error {},
  ApprovalOverrideRequiredError: class ApprovalOverrideRequiredError extends Error {
    constructor(readonly reasons: string[]) { super(reasons.join("；")); }
  },
  loadSiteDashboard: mockLoad,
  loadFbaRateCards: mockCards,
  saveSiteDraft: mockSave,
  submitSiteReview: mockSubmit,
  decideSiteReview: mockDecide,
  openSiteRecord: mockOpenRecord,
  getSiteImageUrl: vi.fn(async () => "data:image/png;base64,aW1hZ2U="),
}));

const access = {
  userId: "operator-1", role: "运营" as const, submitGroup: "一部一组" as const,
  reviewGroups: [], canReviewAll: false, canSubmit: true, canReview: false,
  tableEditable: true,
};
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
beforeEach(() => { mockCards.mockReset().mockResolvedValue([]); });

describe("多站点草稿入口", () => {
  it("AE异步预估返回不改变手填成本、利润或保存内容，修改售价重新预估", async () => {
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AE"));
    const tax = { site: "AE", currency: "AED", roughConfigured: true, priceIncludesTax: true,
      salesTaxRate: .05, feeTaxRate: 0, incomeTaxWithholdingRate: 0, effectiveFrom: 0, effectiveUntil: null } as TaxProfile;
    const finance = { site: "AE", currency: "AED", category: "通用", roughConfigured: true,
      cnyPerCurrency: 2, headhaulCnyPerKg: 45, volumeDivisorCm3PerKg: 6000,
      referralRate: .15, referralBasis: "买家标价", adRate: .1, returnLossRate: .03, storageLossRate: .015,
      promotionRate: .01, settlementLossRate: .005, targetContributionRate: .25,
      approved: true, effectiveFrom: 0, effectiveUntil: null } as FinanceProfile;
    mockLoad.mockResolvedValueOnce({ reviews: [], taxes: [tax], finances: [finance], rules: [], problems: [] });
    let release: () => void = () => {};
    const fetch = vi.fn(async (_url: string, options: RequestInit) => {
      const input = JSON.parse(options.body as string).items[0];
      if (input.price.amount === 12) await new Promise<void>(resolve => { release = resolve; });
      const amount = input.price.amount > 25 ? 7.5 : 5.5;
      return { ok: true, json: async () => ({ results: [{ ok: true, result: {
        marketplace: "AE", currency: "AED", saleDate: input.saleDate, rateVersion: "2026-10-31.2", status: "estimated",
        fee: { total: amount, totalMinorUnits: amount * 100, includesTax: false },
        classification: { tier: "small-envelope", plan: amount === 5.5 ? "low_price" : "regular",
          shippingWeight: .033, weightUnit: "kg" },
      } }] }) };
    }); vi.stubGlobal("fetch", fetch);
    render(<MultiSitePanel access={access} />);
    await screen.findByLabelText("售价（AED） *");
    for (const [label, value] of [["售价（AED） *", "12"], ["包装长（cm） *", "12"], ["包装宽（cm） *", "5"],
      ["包装高（cm） *", "1"], ["包装重量（g） *", "33"], ["采购单价（¥） *", "1"],
      ["包装加工费（¥）", "1"], ["FBA 费用（AED） *", "9.99"]])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    const before = document.querySelector(".site-result-hero")!.textContent;
    expect(before).not.toContain("利润 / 件—");
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    await act(async () => { release(); });
    expect(await screen.findByText("AED 5.50")).toBeInTheDocument();
    expect(screen.getByLabelText("FBA 费用（AED） *")).toHaveValue(9.99);
    expect(document.querySelector(".site-result-hero")!.textContent).toBe(before);
    expect(mockSave).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("售价（AED） *"), { target: { value: "30" } });
    expect(screen.queryByText("AED 5.50")).not.toBeInTheDocument();
    expect(await screen.findByText("AED 7.50")).toBeInTheDocument();
    expect(screen.getByLabelText("FBA 费用（AED） *")).toHaveValue(9.99);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0].fbaFeeLocal).toBe(9.99);
  });
  it("AE服务不可用仍可手填保存，提示可重试而不再显示计费重待确认", async () => {
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AE"));
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<MultiSitePanel access={access} />);
    await screen.findByLabelText("售价（AED） *");
    for (const [label, value] of [["售价（AED） *", "12.12"], ["包装长（cm） *", "12"], ["包装宽（cm） *", "5"],
      ["包装高（cm） *", "1"], ["包装重量（g） *", "33"], ["FBA 费用（AED） *", "5.55"]])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(await screen.findByText("预估暂不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试预估" })).toBeEnabled();
    expect(screen.queryByText("计费重待确认")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0].fbaFeeLocal).toBe(5.55);
  });
  it("FBA参考价出现或重新计算时不自动填写或覆盖手填费用，保存仍使用手填值", async () => {
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AU"));
    mockCards.mockResolvedValueOnce(cardData.rows.filter(row => row.site === "AU").map(row => ({
      ...row, site: "AU", maxDimsCm: row.maxDimsCm as [number, number, number],
      shippingMethod: row.shippingMethod as FbaCard["shippingMethod"],
      effectiveFrom: Date.parse(row.effectiveFrom),
      effectiveUntil: row.effectiveUntil ? Date.parse(row.effectiveUntil) : null,
    })));
    render(<MultiSitePanel access={access} />);
    await screen.findByLabelText("售价（AUD） *");
    for (const [label, value] of [["售价（AUD） *", "25"], ["包装长（cm） *", "20"],
      ["包装宽（cm） *", "15"], ["包装高（cm） *", "10"], ["包装重量（g） *", "400"]])
      fireEvent.change(screen.getByLabelText(label), { target: { value } });
    expect(await screen.findByText("AUD 7.24")).toBeInTheDocument();
    expect(screen.getByLabelText("FBA 费用（AUD） *")).toHaveValue(null);
    expect(mockSave).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("FBA 费用（AUD） *"), { target: { value: "17.77" } });
    fireEvent.change(screen.getByLabelText("售价（AUD） *"), { target: { value: "12" } });
    expect(await screen.findByText("AUD 6.33")).toBeInTheDocument();
    expect(screen.getByLabelText("FBA 费用（AUD） *")).toHaveValue(17.77);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0].fbaFeeLocal).toBe(17.77);
    expect(mockSubmit).not.toHaveBeenCalled();
  });
  it("记住阿联酋站点并以 AED 带入、保存草稿", async () => {
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AE"));
    render(<MultiSitePanel access={access} />);
    await screen.findByLabelText("售价（AED）");
    fireEvent.change(screen.getByLabelText("售价（AED）"), { target: { value: "105" } });
    fireEvent.change(screen.getByLabelText("采购成本（¥）"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("FBA 费用（AED）"), { target: { value: "7.5" } });
    fireEvent.click(screen.getByRole("button", { name: "带入完整表单" }));
    expect(screen.getByLabelText("售价（AED） *")).toHaveValue(105);
    expect(screen.getByLabelText("FBA 费用（AED） *")).toHaveValue(7.5);
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0]).toMatchObject({ site: "AE", listedPrice: 105, fbaFeeLocal: 7.5 });
    expect(mockPreferenceSave).toHaveBeenCalledWith(expect.anything(), "AE");
  });
  it("审批已成功但列表刷新失败时仍提示通过，不显示审批失败", async () => {
    const review: SiteReview = {
      id: "approved-refresh", number: "MS-refresh", draft: { ...newSiteDraft("CA"),
        productName: "审批刷新回归", category: "家居", listedPrice: 25 },
      status: "待审核", validation: "通过", submitterId: "operator-2", submitterName: "运营",
      group: "一部一组", submittedAt: 1, reviewCount: 0, managerComment: "",
      hasImage: false, imageToken: "", calculation: null,
      officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    };
    mockLoad.mockResolvedValueOnce({ reviews: [review], taxes: [], finances: [], rules: [], problems: [] })
      .mockRejectedValueOnce(new Error("record permission denied error"));
    const supervisor = { ...access, role: "运营主管" as const,
      canReview: true, reviewGroups: ["一部一组" as const] };
    render(<MultiSitePanel access={supervisor} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(await screen.findByText("审批已成功，列表暂未刷新，请点击顶部「刷新数据」。")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "主管评审详情" })).not.toBeInTheDocument();
    expect(screen.queryByText("record permission denied error")).not.toBeInTheDocument();
  });
  it("本组主管自提显示免审核入口，运营仍走提交审核", async () => {
    const supervisor = { ...access, role: "运营主管" as const,
      canReview: true, reviewGroups: ["一部一组" as const] };
    const { rerender } = render(<MultiSitePanel access={supervisor} />);
    expect(await screen.findByRole("button", { name: "提交选品（免审核）" })).toBeInTheDocument();
    rerender(<MultiSitePanel access={access} />);
    expect(screen.getByRole("button", { name: "提交审核" })).toBeInTheDocument();
  });
  it("品类可从下拉选择，也可输入未列出的品类并保存草稿", async () => {
    render(<MultiSitePanel access={access} />);
    await screen.findByText("提审检查");
    const category = screen.getByRole("combobox", { name: "品类 *" });
    fireEvent.click(screen.getByRole("button", { name: "展开品类选项" }));
    fireEvent.click(screen.getByRole("option", { name: "家居" }));
    expect(category).toHaveValue("家居");
    fireEvent.change(category, { target: { value: "庭院装饰" } });
    expect(screen.getByText("没有匹配项，可直接输入新品类")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0].category).toBe("庭院装饰");
  });
  it("粗算手填 FBA 带入完整表单，清空后不覆盖已有金额", async () => {
    render(<MultiSitePanel access={access} />);
    await screen.findByText("提审检查");
    fireEvent.change(screen.getByLabelText("售价（CAD）"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("采购成本（¥）"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("FBA 费用（CAD）"), { target: { value: "4.5" } });
    fireEvent.click(screen.getByRole("button", { name: "带入完整表单" }));
    expect(screen.getByLabelText("FBA 费用（CAD） *")).toHaveValue(4.5);
    fireEvent.change(screen.getByLabelText("FBA 费用（CAD）"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "带入完整表单" }));
    expect(screen.getByLabelText("FBA 费用（CAD） *")).toHaveValue(4.5);
  });
  it("重开本人草稿后可修改 SKU 编码并保存到原记录", async () => {
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    const review: SiteReview = {
      id: "draft-1", number: "MS-1", draft: { ...newSiteDraft("CA"), site: "CA",
        entitySku: "OLD-SKU", productName: "草稿产品", category: "家居",
        listedPrice: 20, purchaseCny: 10, fbaFeeLocal: 3,
        lengthCm: 10, widthCm: 8, heightCm: 2, unitWeightG: 80 },
      status: "草稿", validation: "未提交", submitterId: access.userId,
      submitterName: "测试运营", group: "一部一组", submittedAt: 0,
      reviewCount: 0, managerComment: "", hasImage: false, imageToken: "",
      calculation: null, officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    };
    mockLoad.mockResolvedValueOnce({ reviews: [review], taxes: [], finances: [], rules: [], problems: [] });
    render(<MultiSitePanel access={access} />);
    fireEvent.click(await screen.findByText(/我的草稿 · 从这里继续编辑/));
    fireEvent.click(screen.getByRole("button", { name: "继续填写" }));
    const sku = screen.getByLabelText("选品SKU");
    expect(sku).not.toBeDisabled();
    fireEvent.change(sku, { target: { value: "NEW-SKU" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledWith(
      expect.objectContaining({ entitySku: "NEW-SKU" }), access, "draft-1", null));
  });
  it("统一提交列表搜索同时覆盖站点评审和已存记录，且只出现一个页签卡片", async () => {
    mockLoad.mockResolvedValueOnce({ reviews: [{
      id: "site-1", number: "ST-1", draft: { ...({} as SiteDraft), site: "CA",
        entitySku: "DEMO-ITEM-002", productName: "站点产品", referenceAsins: "B0001",
        category: "家居", listedPrice: 20, fbaFeeLocal: 4, purchaseCny: 10 },
      status: "待审核", validation: "通过", submitterId: access.userId,
      submitterName: "测试运营", group: "一部一组", submittedAt: 1, reviewCount: 0,
      managerComment: "", hasImage: false, imageToken: "", calculation: null,
      officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    }], taxes: [], finances: [], rules: [], problems: [] });
    render(<MultiSitePanel access={access} view="submitted" legacyQueues={{
      submitted: [{ id: "legacy-1", search: "JP-0007 历史灯 B0002", node: <article>历史灯</article> }],
      returned: [], review: [], excludedPendingCount: 0,
    }} />);
    expect(await screen.findByText("站点产品")).toBeInTheDocument();
    expect(screen.getByText("历史灯")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "我的提交" })).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("查找我的提交"), { target: { value: "JP-0007" } });
    expect(screen.getByText("历史灯")).toBeInTheDocument();
    expect(screen.queryByText("站点产品")).not.toBeInTheDocument();
  });
  it("管理员可查看所有站点提交和打回；待审卡有图片及运营姓名，本人不能自审", async () => {
    const review = (id: string, status: SiteReview["status"], submitterId: string): SiteReview => ({
      id, number: id, draft: { ...({} as SiteDraft), site: "CA", entitySku: id,
        productName: `产品${id}`, category: "家居", listedPrice: 25, fbaFeeLocal: 4, purchaseCny: 10,
        lengthCm: 10, widthCm: 10, heightCm: 2, unitWeightG: 100 },
      status, validation: "通过", submitterId, submitterName: "示例运营乙", group: "一部一组",
      submittedAt: 1, reviewCount: 0, managerComment: "", hasImage: true, imageToken: `img-${id}`,
      calculation: null, officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    });
    mockLoad.mockResolvedValueOnce({ reviews: [review("other", "待审核", "operator-2"),
      review("self", "待审核", "operator-1"), review("back", "打回修改", "operator-2")],
      taxes: [], finances: [], rules: [], problems: [] });
    const admin = { ...access, role: "管理员" as const, canReview: true, canReviewAll: true };
    const { rerender } = render(<MultiSitePanel access={admin} view="submitted" />);
    expect(await screen.findByText("产品other")).toBeInTheDocument();
    expect(screen.getByText("产品self")).toBeInTheDocument();
    rerender(<MultiSitePanel access={admin} view="returned" />);
    expect(screen.getByText("产品back")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看原记录" })).toBeInTheDocument();
    rerender(<MultiSitePanel access={admin} view="review" />);
    expect(screen.getByText("产品other")).toBeInTheDocument();
    expect(screen.getAllByText("示例运营乙").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "预览产品图片：产品other" })).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "查看评审详情" })[1]);
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
  });
  it("主管按运营和站点筛选统一队列，汇总只统计当前结果且售价不跨币种合并", async () => {
    const siteReview: SiteReview = {
      id: "au-1", number: "AU-1", draft: { ...({} as SiteDraft), site: "AU",
        entitySku: "DEMO-ITEM-001", productName: "澳站产品", category: "家居",
        listedPrice: 22, purchaseCny: 13, fbaFeeLocal: 4,
        lengthCm: 10, widthCm: 10, heightCm: 2, unitWeightG: 100 },
      status: "待审核", validation: "通过", submitterId: "operator-au",
      submitterName: "澳站运营", group: "一部一组", submittedAt: 1, reviewCount: 0,
      managerComment: "", hasImage: false, imageToken: "", calculation: null,
      profit: { contribution: 5, rate: .29, roi: 1, netRevenue: 20,
        acquisition: null, headhaul: null, referral: null, salesTax: null,
        platformFeeTax: null, fbaEconomic: null, marketingAndOther: null,
        withholding: null, payoutRate: .5 },
      officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    };
    mockLoad.mockResolvedValueOnce({ reviews: [siteReview], taxes: [], finances: [],
      rules: [], problems: [] });
    const admin = { ...access, role: "管理员" as const, canReview: true, canReviewAll: true };
    render(<MultiSitePanel access={admin} view="review" legacyQueues={{
      submitted: [], returned: [], excludedPendingCount: 0,
      review: [{ id: "ca-1", search: "JP-0001", node: <article>加拿大历史产品</article>,
        submitterId: "operator-ca", submitterName: "加拿大运营", site: "CA",
        listedPrice: 25, purchaseCny: 2, contributionRate: .45 }],
    }} />);
    expect(await screen.findByText("澳站产品")).toBeInTheDocument();
    const summary = document.querySelector(".review-summary-inline")!;
    expect(summary).toHaveTextContent("2 条");
    expect(summary).toHaveTextContent("AUD 22.00");
    expect(summary).toHaveTextContent("CAD 25.00");
    expect(summary).toHaveTextContent("¥2.00–¥13.00");
    fireEvent.change(screen.getByLabelText("按运营筛选"), { target: { value: "operator-au" } });
    expect(screen.queryByText("加拿大历史产品")).not.toBeInTheDocument();
    expect(summary).toHaveTextContent("1 条");
    expect(summary).toHaveTextContent("29.00%");
    fireEvent.change(screen.getByLabelText("按站点筛选"), { target: { value: "CA" } });
    expect(screen.getByText("当前筛选无待审核记录")).toBeInTheDocument();
    expect(summary).toHaveTextContent("0 条");
  });
  it("主管风险例外通过需确认并填写审核意见", async () => {
    const review: SiteReview = {
      id: "review-risk", number: "MS-risk", draft: { ...newSiteDraft("CA"),
        entitySku: "RISK-SKU", productName: "风险产品", category: "家居",
        listedPrice: 25, purchaseCny: 10, fbaFeeLocal: 4,
        lengthCm: 10, widthCm: 8, heightCm: 2, unitWeightG: 80 },
      status: "待审核", validation: "通过", submitterId: "operator-2",
      submitterName: "测试运营", group: "一部一组", submittedAt: 1,
      reviewCount: 0, managerComment: "", hasImage: true, imageToken: "img-risk",
      calculation: null, officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    };
    mockLoad.mockResolvedValueOnce({ reviews: [review], taxes: [], finances: [], rules: [], problems: [] });
    const { ApprovalOverrideRequiredError } = await import("./repository");
    mockDecide.mockRejectedValueOnce(new ApprovalOverrideRequiredError(["毛利率低于 25%"]))
      .mockResolvedValueOnce(undefined);
    const supervisor = { ...access, role: "运营主管" as const,
      canReview: true, reviewGroups: ["一部一组" as const] };
    render(<MultiSitePanel access={supervisor} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    const dialog = await screen.findByRole("dialog", { name: "例外通过确认" });
    const confirm = within(dialog).getByRole("button", { name: "确认例外通过" });
    expect(confirm).toBeDisabled();
    fireEvent.change(within(dialog).getByRole("textbox", { name: "例外通过原因" }),
      { target: { value: "主管确认小批量试销" } });
    fireEvent.click(confirm);
    await waitFor(() => expect(mockDecide).toHaveBeenLastCalledWith("review-risk", "approve",
      "主管确认小批量试销", supervisor, expect.any(Object), true, review));
  });
  it("主管审核报价三项选填，留空仍可发起审核", async () => {
    const review: SiteReview = {
      id: "review-optional-quote", number: "MS-optional", draft: { ...newSiteDraft("CA"),
        entitySku: "OPTIONAL-SKU", productName: "测试产品", category: "家居",
        listedPrice: 25, purchaseCny: 10, fbaFeeLocal: 4,
        lengthCm: 10, widthCm: 8, heightCm: 2, unitWeightG: 80 },
      status: "待审核", validation: "通过", submitterId: "operator-2",
      submitterName: "测试运营", group: "一部一组", submittedAt: 1,
      reviewCount: 0, managerComment: "", hasImage: true, imageToken: "img-optional",
      calculation: null, officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
    };
    mockLoad.mockResolvedValueOnce({ reviews: [review], taxes: [], finances: [], rules: [], problems: [] });
    const supervisor = { ...access, role: "运营主管" as const,
      canReview: true, reviewGroups: ["一部一组" as const] };
    render(<MultiSitePanel access={supervisor} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.click(screen.getByText(/补充官方 FBA 报价/));
    expect(screen.getByLabelText("官方 FBA 报价（CAD，选填）")).toHaveValue(null);
    expect(screen.getByLabelText("报价来源（选填）")).toHaveValue("");
    expect(screen.getByLabelText("官方报价凭证（选填）")).toHaveValue("");
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith(
      "review-optional-quote", "approve", "", supervisor,
      { fee: 0, channel: "", evidence: "" }, false, review));
  });
  it("allows a draft while withholding review until finance and required inputs are ready", async () => {
    render(<MultiSitePanel access={access} />);
    await screen.findByText("站点财务参数未启用：可以保存草稿，暂不计算利润或提交审核。");
    expect(screen.getByRole("button", { name: "保存草稿" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "提交审核" })).toBeEnabled();
    expect(screen.queryByText(/待补：/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "MX" } });
    expect(screen.getByText(/站点财务参数未启用或未配齐/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /收起粗算/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("参考竞品 ASIN"), { target: { value: "B0TEST0005" } });
    fireEvent.change(screen.getByLabelText("供应商名称"), { target: { value: "测试供应商" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0]).toMatchObject({
      site: "MX", referenceAsins: "B0TEST0005", supplierName: "测试供应商",
      region: "全站", taxClass: "标准",
    });
    expect(mockSave.mock.calls[0][0].expectedSaleDate).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("切站点重置粗算临时占比并读取对应站点默认值", async () => {
    const ca = { site: "CA", currency: "CAD", category: "通用", approved: true, adRate: .1,
      returnLossRate: .03, effectiveFrom: 0, effectiveUntil: null } as FinanceProfile;
    const uk = { ...ca, site: "UK", currency: "GBP", adRate: .08,
      returnLossRate: .02 } as FinanceProfile;
    mockLoad.mockResolvedValueOnce({ reviews: [], taxes: [], finances: [ca, uk],
      rules: [], problems: [] });
    render(<MultiSitePanel access={access} />);
    await waitFor(() => expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(10));
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(3);
    fireEvent.change(screen.getByLabelText("广告营销占比（%）"), { target: { value: "25" } });
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "UK" } });
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(8);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(2);
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "CA" } });
    expect(screen.getByLabelText("广告营销占比（%）")).toHaveValue(10);
    expect(screen.getByLabelText("售后退货占比（%）")).toHaveValue(3);
  });

  it.each([["SA", "SAR"], ["JP", "JPY"]] as const)("%s 已填参数但未启用时不展示利润，可保存草稿而不能提审", async (site, currency) => {
    const tax = { site, currency, roughConfigured: true, priceIncludesTax: true,
      salesTaxRate: .2, incomeTaxWithholdingRate: 0, effectiveFrom: 0,
      effectiveUntil: null } as TaxProfile;
    const finance = { site, currency, category: "待确认",
      roughConfigured: true, cnyPerCurrency: 10, referralRate: .1,
      headhaulCnyPerKg: 50, volumeDivisorCm3PerKg: 5000,
      referralBasis: "买家标价", approved: false,
      effectiveFrom: 0, effectiveUntil: null } as FinanceProfile;
    mockLoad.mockResolvedValueOnce({ reviews: [], taxes: [tax], finances: [finance],
      rules: [], problems: [] });
    render(<MultiSitePanel access={access} />);
    await screen.findByLabelText("售价（CAD）");
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: site } });
    fireEvent.change(screen.getByLabelText(`售价（${currency}）`), { target: { value: "120" } });
    fireEvent.change(screen.getByLabelText("采购成本（¥）"), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("包装长（cm）"), { target: { value: "20" } });
    fireEvent.change(screen.getByLabelText("包装宽（cm）"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("包装高（cm）"), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("包装重量（g）"), { target: { value: "400" } });
    fireEvent.change(screen.getByLabelText(`FBA 费用（${currency}）`), { target: { value: "5" } });
    fireEvent.change(screen.getByLabelText("广告营销占比（%）"), { target: { value: "8" } });
    fireEvent.change(screen.getByLabelText("售后退货占比（%）"), { target: { value: "2" } });
    const shipping = document.querySelector(".quick-dimension-card")!;
    expect(shipping).toHaveTextContent("头程 待填写");
    expect(screen.queryByText(/粗算利润\/件/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "带入完整表单" }));
    expect(document.querySelector(".site-result-hero")).toHaveTextContent("毛利率 —");
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][0].site).toBe(site);
    fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
    expect(screen.getByRole("dialog", { name: "提交审核前请补齐" })).toHaveTextContent("该站财务参数尚未获确认");
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("点击提交时才弹出校验，并标红可修改字段且不保存未通过的记录", async () => {
    render(<MultiSitePanel access={access} />);
    await screen.findByText("站点财务参数未启用：可以保存草稿，暂不计算利润或提交审核。");
    const sku = screen.getByLabelText("选品SKU");
    expect(sku).toHaveAttribute("aria-invalid", "false");
    fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
    expect(screen.getByRole("dialog", { name: "提交审核前请补齐" })).toHaveTextContent("该站财务参数尚未获确认");
    expect(sku).toHaveAttribute("aria-invalid", "false");
    expect(screen.getByLabelText("产品中文名称 *")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("产品图片预览")).toHaveAttribute("aria-invalid", "true");
    expect(mockSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "返回填写" }));
    fireEvent.change(sku, { target: { value: "DEMO-ITEM-005" } });
    expect(sku).toHaveAttribute("aria-invalid", "false");
  });

  it("粘贴产品截图后预览并随草稿保存", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true,
      value: vi.fn(() => "blob:preview") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true,
      value: vi.fn() });
    render(<MultiSitePanel access={access} />);
    await screen.findByText("站点财务参数未启用：可以保存草稿，暂不计算利润或提交审核。");
    const image = new File(["image"], "candidate.png", { type: "image/png" });
    const panel = screen.getByRole("heading", { name: "候选产品录入" }).closest("section");
    fireEvent.paste(panel!, { clipboardData: { items: [
      { type: "image/png", getAsFile: () => image },
    ] } });
    expect(await screen.findByRole("img", { name: "产品图片预览" })).toHaveAttribute("src", "blob:preview");
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledOnce());
    expect(mockSave.mock.calls[0][3]).toBe(image);
  });

  it("提交阶段失败时在按钮旁和弹窗提示，并保留已选择的产品图片", async () => {
    Object.defineProperty(URL, "createObjectURL", { configurable: true,
      value: vi.fn(() => "blob:submit-preview") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true,
      value: vi.fn() });
    mockLoad.mockResolvedValueOnce({ reviews: [], rules: [], problems: [],
      taxes: [{ site: "CA", currency: "CAD", priceIncludesTax: false,
        salesTaxRate: 0, incomeTaxWithholdingRate: 0, feeTaxRate: 0,
        feeTaxRecoverable: false, incomeTaxWithholdingRecoverable: false,
        collector: "亚马逊代收", version: "test", effectiveFrom: 0,
        effectiveUntil: null } as TaxProfile],
      finances: [{ site: "CA", currency: "CAD", category: "通用",
        cnyPerCurrency: 5, headhaulCnyPerKg: 45, volumeDivisorCm3PerKg: 5000,
        referralRate: .15, referralBasis: "买家标价", referralFeeIncludesTax: false,
        adRate: .1, returnLossRate: .03, storageLossRate: .015,
        promotionRate: .01, settlementLossRate: .005,
        targetContributionRate: .25, approved: true, effectiveFrom: 0,
        effectiveUntil: null, version: "test", source: "test" } as FinanceProfile],
    });
    mockSubmit.mockRejectedValueOnce(new Error("更新记录无权限"));
    const { container } = render(<MultiSitePanel access={access} />);
    await screen.findByText("提审检查");
    expect(screen.getByLabelText("选品SKU")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("选品说明"), { target: { value: "选品依据：测试需求。\n销售策略：小批量测试。" } });
    fireEvent.change(screen.getByLabelText("产品中文名称 *"), { target: { value: "测试产品" } });
    fireEvent.change(screen.getByLabelText("品类 *"), { target: { value: "家居" } });
    fireEvent.change(screen.getByLabelText("1688链接 *"), { target: { value: "https://example.com" } });
    for (const [label, value] of [
      ["售价（CAD） *", "25"], ["采购单价（¥） *", "2"],
      ["包装长（cm） *", "17.5"], ["包装宽（cm） *", "14"],
      ["包装高（cm） *", "0.5"], ["包装重量（g） *", "20"],
      ["FBA 费用（CAD） *", "4.55"],
    ]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
    for (const label of ["商标排查 *", "专利排查 *", "版权排查 *"])
      fireEvent.change(screen.getByLabelText(label), { target: { value: "无风险" } });
    fireEvent.change(screen.getByLabelText("排查依据 *"), { target: { value: "无" } });
    const image = new File(["image"], "candidate.png", { type: "image/png" });
    fireEvent.change(container.querySelector('input[type="file"]')!, { target: { files: [image] } });
    fireEvent.click(screen.getByRole("button", { name: "提交审核" }));
    expect(await screen.findByRole("dialog", { name: "提交审核未完成" })).toHaveTextContent("更新记录无权限");
    expect(screen.getByText("操作未完成").closest('[role="alert"]')).toHaveTextContent("更新记录无权限");
    expect(screen.getByRole("img", { name: "产品图片预览" })).toHaveAttribute("src", "blob:submit-preview");
    expect(mockSave).toHaveBeenCalledOnce();
    expect(mockSave.mock.calls[0][0].entitySku).toBe("");
    expect(mockSave.mock.calls[0][3]).toBe(image);
    expect(mockSubmit).toHaveBeenCalledOnce();
  });
});


describe("按运营账号记忆站点", () => {
  it("没有记忆时必须选择站点，不能默认加拿大保存", async () => {
    mockPreferenceLoad.mockResolvedValueOnce(remembered(null));
    render(<MultiSitePanel access={access} />);
    await screen.findByText(/请先选择本次目标站点/);
    expect(screen.getByLabelText("目标站点")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "保存草稿" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "AU" } });
    expect(screen.getByLabelText("售价（AUD）")).toBeInTheDocument();
    expect(mockPreferenceSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockPreferenceSave).toHaveBeenCalledWith(remembered(null).scope, "AU"));
  });
  it("普通运营新建澳洲草稿后记住，重新打开默认澳洲", async () => {
    const page = render(<MultiSitePanel access={access} />);
    await screen.findByText("提审检查");
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "AU" } });
    fireEvent.change(screen.getByLabelText("产品中文名称 *"), { target: { value: "澳洲产品" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockPreferenceSave).toHaveBeenCalledWith(remembered(null).scope, "AU"));
    page.unmount();
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AU"));
    render(<MultiSitePanel access={access} />);
    expect(await screen.findByLabelText("售价（AUD）")).toBeInTheDocument();
    expect(screen.getByLabelText("目标站点")).toHaveValue("AU");
    expect(screen.getByLabelText("目标站点").closest("label")).toHaveAttribute("title", expect.stringContaining("已沿用你上次使用的站点：澳大利亚"));
  });
  it("保存失败不更新个人站点记忆", async () => {
    mockSave.mockRejectedValueOnce(new Error("记录保存失败"));
    render(<MultiSitePanel access={access} />);
    await screen.findByText("提审检查");
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "AU" } });
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await screen.findByText("记录保存失败");
    expect(mockPreferenceSave).not.toHaveBeenCalled();
  });
  it("慢速偏好返回不会覆盖手动站点或已填写商品", async () => {
    let finish!: (value: ReturnType<typeof remembered>) => void;
    mockPreferenceLoad.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    render(<MultiSitePanel access={access} />);
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "UK" } });
    fireEvent.change(screen.getByLabelText("产品中文名称 *"), { target: { value: "正在填写" } });
    await act(async () => finish(remembered("AU")));
    expect(screen.getByLabelText("目标站点")).toHaveValue("UK");
    expect(screen.getByLabelText("产品中文名称 *")).toHaveValue("正在填写");
  });
  it.each(["草稿", "打回修改"] as const)("打开并保存旧%s保留加拿大，下一次新建仍为澳洲", async status => {
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    mockPreferenceLoad.mockResolvedValueOnce(remembered("AU"));
    const record = { id: "old-ca", number: "OLD", draft: { ...newSiteDraft("CA"), productName: "旧加拿大产品" },
      status, validation: "未提交", submitterId: access.userId, submitterName: "本人", group: "一部一组",
      submittedAt: 0, reviewCount: 0, managerComment: "", hasImage: false, imageToken: "", calculation: null,
      officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "" } as SiteReview;
    mockLoad.mockResolvedValueOnce({ reviews: [record], taxes: [], finances: [], rules: [], problems: [] });
    const { rerender } = render(<MultiSitePanel access={access} view={status === "草稿" ? "multisite" : "returned"} />);
    if (status === "草稿") fireEvent.click(await screen.findByText(/我的草稿 · 从这里继续编辑/));
    fireEvent.click(await screen.findByRole("button", { name: status === "草稿" ? "继续填写" : "修改并重提" }));
    rerender(<MultiSitePanel access={access} />);
    expect(screen.getByLabelText("目标站点")).toHaveValue("CA");
    expect(screen.getByLabelText("目标站点")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存草稿" }));
    await waitFor(() => expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({ site: "CA" }),access,"old-ca",null));
    await waitFor(() => expect(screen.getByRole("button", { name: "新建另一站点" })).toBeEnabled());
    expect(mockPreferenceSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "新建另一站点" }));
    expect(screen.getByLabelText("目标站点")).toHaveValue("AU");
    expect(screen.getByLabelText("产品中文名称 *")).toHaveValue("");
  });
  it("账号切换清空旧账号表单，迟到响应不会串到新账号", async () => {
    let finish!: (value: ReturnType<typeof remembered>) => void;
    mockPreferenceLoad.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
    const { rerender } = render(<MultiSitePanel access={access} />);
    fireEvent.change(screen.getByLabelText("目标站点"), { target: { value: "AU" } });
    fireEvent.change(screen.getByLabelText("产品中文名称 *"), { target: { value: "甲的产品" } });
    mockPreferenceLoad.mockResolvedValueOnce(remembered("UK", "operator-2"));
    rerender(<MultiSitePanel access={{ ...access, userId: "operator-2" }} />);
    await screen.findByLabelText("售价（GBP）");
    await act(async () => finish(remembered("AU")));
    expect(screen.getByLabelText("目标站点")).toHaveValue("UK");
    expect(screen.getByLabelText("产品中文名称 *")).toHaveValue("");
  });
});

const detailFixture = (id: string): SiteReview => ({
  id, number: id, draft: { ...newSiteDraft("AU"), productName: `集中评审产品${id}`, category: "配件",
    entitySku: id, listedPrice: 12.5, purchaseCny: 4.5, processingCny: 0, fbaFeeLocal: 3.64,
    lengthCm: 15, widthCm: 10, heightCm: 1, unitWeightG: 70, sourceUrl: "https://detail.1688.com/offer/123.html",
    trademarkRisk: "无风险", patentRisk: "无风险", copyrightRisk: "无风险", complianceNotes: "已按关键词检索" },
  status: "待审核", validation: "通过", submitterId: "operator-2", submitterName: "测试运营", group: "一部一组",
  submittedAt: 1793758245000, reviewCount: 0, managerComment: "上次核对意见", hasImage: false, imageToken: "", calculation: null,
  officialFbaFee: 0, officialFbaSource: "", officialFbaEvidence: "",
  profit: { contribution: 3.314051528, rate: .265124122, roi: 1.983335562, netRevenue: 12.5,
    acquisition: .939908516, headhaul: .731039957, referral: 1.875, salesTax: 0,
    platformFeeTax: 0, fbaEconomic: 3.64, marketingAndOther: 2, withholding: 0, payoutRate: .3988 },
});
const reviewAccess = { ...access, userId: "reviewer", role: "运营主管" as const, canReview: true,
  reviewGroups: ["一部一组" as const] };
const detailDashboard = (reviews: SiteReview[]): SiteDashboard => ({ reviews, taxes: [], finances: [], rules: [], problems: [] });

describe("提审恢复后的运营与主管队列", () => {
  const recovered = (): SiteReview[] => [
    { ...detailFixture("own-a"), submitterId: "operator-1" },
    { ...detailFixture("own-b"), submitterId: "operator-1" },
    { ...detailFixture("missing-own"), submitterId: "operator-1", status: "草稿", validation: "未提交" },
    detailFixture("team-a"),
    { ...detailFixture("team-b"), submitterId: "operator-3", group: "一部二组" },
    { ...detailFixture("team-c"), submitterId: "operator-4", group: "一部二组" },
    { ...detailFixture("missing-team"), status: "草稿", validation: "未提交" },
  ];
  it("运营看到本人恢复的两个提交，缺说明的原记录保留在草稿", async () => {
    mockLoad.mockResolvedValueOnce(detailDashboard(recovered()));
    const { rerender } = render(<MultiSitePanel access={access} view="submitted" />);
    expect(await screen.findByText("集中评审产品own-a")).toBeInTheDocument();
    expect(screen.getByText("集中评审产品own-b")).toBeInTheDocument();
    expect(screen.queryByText("集中评审产品missing-own")).not.toBeInTheDocument();
    expect(screen.queryByText("集中评审产品team-a")).not.toBeInTheDocument();
    rerender(<MultiSitePanel access={access} view="multisite" />);
    fireEvent.click(await screen.findByText(/我的草稿 · 从这里继续编辑/));
    expect(screen.getByText("集中评审产品missing-own")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "继续填写" })).toHaveLength(1);
  });
  it.each([ ["一部一组", 3, "team-a", "team-b"], ["一部二组", 2, "team-b", "team-a"] ] as const)(
    "%s主管仅看到本组恢复的待审核记录", async (group, count, included, excluded) => {
      mockLoad.mockResolvedValueOnce(detailDashboard(recovered()));
      render(<MultiSitePanel access={{ ...reviewAccess, reviewGroups: [group] }} view="review" />);
      expect(await screen.findByText(`集中评审产品${included}`)).toBeInTheDocument();
      expect(screen.queryByText(`集中评审产品${excluded}`)).not.toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: "查看评审详情" })).toHaveLength(count);
      expect(screen.queryByText("集中评审产品missing-own")).not.toBeInTheDocument();
      expect(screen.queryByText("集中评审产品missing-team")).not.toBeInTheDocument();
    },
  );
});

describe("集中评审完整交互", () => {
  it("列表保留摘要，详情展示收益与原记录，报价折叠且不改成本", async () => {
    const record = detailFixture("one");
    mockLoad.mockResolvedValueOnce(detailDashboard([record]));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    const open = await screen.findByRole("button", { name: "查看评审详情" });
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    fireEvent.click(open);
    const detail = within(screen.getByRole("dialog", { name: "主管评审详情" }));
    expect(detail.getByText("26.51%")).toBeVisible();
    expect(detail.getByText("198.33%")).toBeVisible();
    expect(detail.getByText("39.88%")).toBeVisible();
    expect(detail.getByText("高于25%审核线 1.51 个百分点")).toBeVisible();
    expect(detail.getByLabelText("官方 FBA 报价（AUD，选填）").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(detail.getByRole("button", { name: "打开原记录" }));
    await waitFor(() => expect(mockOpenRecord).toHaveBeenCalledWith("one"));
    fireEvent.click(detail.getByRole("button", { name: "成本明细" }));
    expect(detail.getByText("减：FBA经济成本")).toBeVisible();
    fireEvent.click(detail.getByRole("button", { name: "供应与风险" }));
    expect(detail.getByText("已按关键词检索")).toBeVisible();
    expect(mockDecide).not.toHaveBeenCalled();
    expect(record.draft.fbaFeeLocal).toBe(3.64);
  });
  it("按record_id保留意见和报价，关闭、重开、前后切换不会串单", async () => {
    const one = detailFixture("one"), two = detailFixture("two");
    two.officialFbaFee = 4; two.officialFbaSource = "Fee Preview"; two.officialFbaEvidence = "已有报价依据";
    mockLoad.mockResolvedValueOnce(detailDashboard([one, two]));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    fireEvent.click((await screen.findAllByRole("button", { name: "查看评审详情" }))[0]);
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "第一条的意见" } });
    fireEvent.change(screen.getByLabelText("官方 FBA 报价（AUD，选填）"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "下一条" }));
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("");
    expect(screen.getByLabelText("官方 FBA 报价（AUD，选填）")).toHaveValue(4);
    expect(screen.getByLabelText("官方报价凭证（选填）")).toHaveValue("已有报价依据");
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "第二条的意见" } });
    fireEvent.click(screen.getByRole("button", { name: "上一条" }));
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("第一条的意见");
    expect(screen.getByLabelText("官方 FBA 报价（AUD，选填）")).toHaveValue(5);
    fireEvent.click(screen.getByRole("button", { name: "← 返回列表" }));
    fireEvent.click(screen.getAllByRole("button", { name: "查看评审详情" })[1]);
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("第二条的意见");
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "主管评审详情" })).not.toBeInTheDocument();
    expect(document.body.style.overflow).not.toBe("hidden");
  });
  it("空意见不能打回，成功后返回列表并移除已处理记录", async () => {
    const one = detailFixture("one"), two = detailFixture("two");
    mockLoad.mockResolvedValueOnce(detailDashboard([one, two])).mockResolvedValueOnce(detailDashboard([two]));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    fireEvent.click((await screen.findAllByRole("button", { name: "查看评审详情" }))[0]);
    fireEvent.click(screen.getByRole("button", { name: "打回修改" }));
    expect(screen.getByRole("alert")).toHaveTextContent("必须填写"); expect(mockDecide).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "补充供货信息" } });
    fireEvent.click(screen.getByRole("button", { name: "打回修改" }));
    await waitFor(() => expect(mockDecide).toHaveBeenCalledWith("one", "return", "补充供货信息", reviewAccess, undefined, false, one));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "主管评审详情" })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("button", { name: "查看评审详情" })).toBeEnabled());
    expect(screen.queryByRole("heading", { name: "集中评审产品one" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看评审详情" }));
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("");
    expect(screen.getByRole("button", { name: "审核通过" })).toBeEnabled();
  });
  it("记录变更错误保留草稿，重新读取后以新快照提交", async () => {
    const before = detailFixture("one"), after = { ...before, submittedAt: before.submittedAt + 1000,
      draft: { ...before.draft, listedPrice: 20 } };
    mockLoad.mockResolvedValueOnce(detailDashboard([before])).mockResolvedValueOnce(detailDashboard([after]));
    mockDecide.mockRejectedValueOnce(new Error("提审数据已变化，请重新读取"));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "保留判断" } });
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await screen.findAllByText("提审数据已变化，请重新读取");
    fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
    await screen.findAllByText("已重新读取，请核对最新数据后审核。");
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("保留判断");
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await waitFor(() => expect(mockDecide).toHaveBeenLastCalledWith("one", "approve", "保留判断", reviewAccess, expect.any(Object), false, after));
  });
});


describe("审批完成后自动返回列表", () => {
  it.each([["审核通过", "approve"], ["打回修改", "return"], ["拒绝", "reject"]] as const)("%s 后最后一条清空，保留筛选并提示结果", async (button, action) => {
    const record = detailFixture("last");
    mockLoad.mockResolvedValueOnce(detailDashboard([record])).mockResolvedValueOnce(detailDashboard([]));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    await screen.findByRole("button", { name: "查看评审详情" });
    fireEvent.change(screen.getByLabelText("按运营筛选"), { target: { value: "operator-2" } });
    fireEvent.change(screen.getByLabelText("按站点筛选"), { target: { value: "AU" } });
    fireEvent.click(screen.getByRole("button", { name: "查看评审详情" }));
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "本次主管意见" } });
    fireEvent.click(screen.getByRole("button", { name: button }));
    await screen.findByText("已返回待审列表。");
    expect(screen.queryByRole("dialog", { name: "主管评审详情" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "当前筛选无待审核记录" })).toBeVisible();
    expect(screen.getByLabelText("按运营筛选")).toHaveValue("operator-2");
    expect(screen.getByLabelText("按运营筛选")).toHaveDisplayValue("测试运营");
    expect(screen.getByLabelText("按站点筛选")).toHaveValue("AU");
    expect(mockDecide).toHaveBeenCalledWith("last", action, "本次主管意见", reviewAccess,
      action === "approve" ? expect.any(Object) : undefined, false, record);
    expect(document.body.style.overflow).not.toBe("hidden");
  });
  it("保存完成立即退出详情，不等列表刷新；关闭提示后不重新弹出", async () => {
    let finishRefresh: (value: SiteDashboard) => void = () => {};
    mockLoad.mockResolvedValueOnce(detailDashboard([detailFixture("last")]))
      .mockImplementationOnce(() => new Promise(resolve => { finishRefresh = resolve; }));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await screen.findByText("已返回待审列表，正在刷新…");
    expect(screen.queryByRole("dialog", { name: "主管评审详情" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "当前没有待审核记录" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "关闭审批结果提示" }));
    await act(async () => finishRefresh(detailDashboard([])));
    expect(screen.queryByRole("button", { name: "关闭审批结果提示" })).not.toBeInTheDocument();
  });
  it("审批失败仍停留在详情并保留意见和报价", async () => {
    mockLoad.mockResolvedValueOnce(detailDashboard([detailFixture("failure")]));
    mockDecide.mockRejectedValueOnce(new Error("审批写入失败"));
    render(<MultiSitePanel access={reviewAccess} view="review" />);
    fireEvent.click(await screen.findByRole("button", { name: "查看评审详情" }));
    fireEvent.change(screen.getByRole("textbox", { name: "主管审核意见" }), { target: { value: "保留意见" } });
    fireEvent.change(screen.getByLabelText("官方 FBA 报价（AUD，选填）"), { target: { value: "4.2" } });
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    await screen.findAllByText("审批写入失败");
    expect(screen.getByRole("dialog", { name: "主管评审详情" })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "主管审核意见" })).toHaveValue("保留意见");
    expect(screen.getByLabelText("官方 FBA 报价（AUD，选填）")).toHaveValue(4.2);
    expect(screen.getByRole("button", { name: "审核通过" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "关闭审批结果提示" })).not.toBeInTheDocument();
  });
});
