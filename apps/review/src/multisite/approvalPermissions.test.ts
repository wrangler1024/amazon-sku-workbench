import { beforeEach, describe, expect, it, vi } from "vitest";
import { calculateSite, newSiteDraft, type FinanceProfile, type TaxProfile } from "./domain";
import type { UserAccess } from "../domain/types";

const sdk = vi.hoisted(() => ({ tables: new Map<string, Record<string, unknown>>(), writes: [] as string[], beforeDetailRead: null as (() => void) | null }));
vi.mock("@lark-opdev/block-bitable-api", () => ({
  IOpenSegmentType: { Text: 1 },
  bitable: { base: { getTableByName: async (name: string) => {
    if (name === "FBA尺寸档规则") return {
      getFieldMetaList: async () => ["站点", "启用", "排序", "档位名称", "最长边上限(cm)",
        "中间边上限(cm)", "最短边上限(cm)", "实重上限(g)", "规则来源"]
        .map((id) => ({ id, name: id })),
      getRecords: async () => ({ hasMore: false, records: ["CA", "MX", "UK", "US", "AU", "AE", "SA", "JP"]
        .map((site) => ({ recordId: site, fields: { "站点": site, "启用": true,
          "档位名称": "参考档", "最长边上限(cm)": 38, "中间边上限(cm)": 27,
          "最短边上限(cm)": 2, "实重上限(g)": 500 } })) }),
    };
    const values = sdk.tables.get(name);
    if (!values) throw new Error(`table absent: ${name}`);
    return {
      getFieldMetaList: async () => Object.keys(values).map((id) => ({ id, name: id })),
      getRecords: async () => ({ hasMore: false, records: [{ recordId: "review-1", fields: values }] }),
      getRecordById: async () => { sdk.beforeDetailRead?.(); return { fields: values }; },
      getFieldById: async () => ({ getOptions: async () =>
        ["草稿", "待审核", "审核通过", "打回修改", "拒绝", "校验中", "通过", "异常", "待制图",
          "亚马逊代收", "买家标价", "常规审核", "主管自提免评审", "Fee Preview", "Revenue Calculator"]
          .map((name) => ({ id: name, name })) }),
      setRecord: async (_id: string, { fields }: { fields: Record<string, unknown> }) => {
        sdk.writes.push(...Object.keys(fields));
        if (["ERP录入状态", "首次审核通过时间", "审核通过日期", "最近提交审核时间", "最近主管审核时间"]
          .some((name) => name in fields))
          throw new Error("record permission denied error");
        Object.assign(values, fields);
        // Validation and supervisor exemption have completed; ERP automation is still queued.
        if ((fields["提交校验状态"] as { text?: string })?.text === "校验中") {
          values["最近提交审核时间"] = Date.now();
          values["提交校验状态"] = { text: "通过" };
          values["审核状态"] = { text: "审核通过" };
        }
        if (fields["审核状态"]) values["最近主管审核时间"] = Date.now();
      },
    };
  } } },
}));

import { decideSiteReview, submitSiteReview, loadSiteDashboard } from "./repository";

const access = { userId: "supervisor", role: "运营主管", canSubmit: true, canReview: true,
  canReviewAll: false, submitGroup: "一部一组", reviewGroups: ["一部一组"],
  tableEditable: true } as UserAccess;
const tax = { site: "CA", region: "全站", taxClass: "标准", currency: "CAD",
  salesTaxRate: 0, priceIncludesTax: false, collector: "亚马逊代收",
  incomeTaxWithholdingRate: 0, incomeTaxWithholdingRecoverable: false,
  feeTaxRate: 0, feeTaxRecoverable: false, approved: true,
  effectiveFrom: 0, effectiveUntil: null, version: "tax-test" } as TaxProfile;
const finance = { site: "CA", category: "通用", cnyPerCurrency: 5, headhaulCnyPerKg: 45,
  referralRate: .15, referralBasis: "买家标价", referralFeeIncludesTax: false,
  adRate: .1, returnLossRate: .03, storageLossRate: .015, promotionRate: .01,
  settlementLossRate: .005, targetContributionRate: .25, volumeDivisorCm3PerKg: 6000,
  approved: true, effectiveFrom: 0, effectiveUntil: null, version: "finance-test" } as FinanceProfile;

beforeEach(() => {
  sdk.writes = []; sdk.beforeDetailRead = null;
  const draft = { ...newSiteDraft("CA"), productName: "权限回归产品", category: "家居",
    sourceUrl: "https://example.com", listedPrice: 25, purchaseCny: 2,
    lengthCm: 17.5, widthCm: 14, heightCm: .5, unitWeightG: 20, fbaFeeLocal: 4.55,
    trademarkRisk: "无风险" as const, patentRisk: "无风险" as const,
    copyrightRisk: "无风险" as const, complianceNotes: "已检索" };
  const result = calculateSite(draft, tax, finance, [])!;
  const main: Record<string, unknown> = {
    "选品说明": "选品依据：本地需求样例。\n销售策略：小批量验证。",
    "选品SKU": "", "目标站点": "CA", "币种": "CAD", "中文品名": draft.productName,
    "品类": draft.category, "1688链接": draft.sourceUrl, "适用地区": draft.region,
    "商品税类": "标准", "预计开售日期": Date.parse(draft.expectedSaleDate),
    "目标标价(本币)": 25, "采购单价(¥)": 2, "包装加工费(¥/件)": 0,
    "包装长(cm)": 17.5, "包装宽(cm)": 14, "包装高(cm)": .5, "包装重量(g)": 20,
    "FBA费用(本币)": 4.55, "商标侵权排查": "无风险", "专利侵权排查": "无风险",
    "版权侵权排查": "无风险", "侵权排查说明": "已检索",
    "审核状态": "待审核", "提交校验状态": "通过", "提交运营ID": "operator",
    "提交运营": [{ id: "operator" }], "审批小组": "一部一组", "运营小组": "一部一组",
    "产品图片": [{ token: "image" }], "销售净收入(本币)": result.netRevenueLocal,
    "标价内销售税(本币)": result.salesTaxLocal, "结账另收销售税(本币)": result.checkoutTaxLocal,
    "平台所得税预提额(本币)": result.incomeTaxWithholdingLocal,
    "不可回收所得税预提(本币)": result.incomeTaxWithholdingNonrecoverableLocal,
    "利润/件(本币)": result.contributionLocal, "毛利率": result.contributionRate,
    "前置资金ROI": result.upfrontCapitalRoi, "计费重量(g)": result.shippingWeightG,
    "FBA经济成本(本币)": result.fbaFeeEconomicLocal,
    "平台佣金经济成本(本币)": result.referralFeeEconomicLocal,
    "平台收费税总额(本币)": 0, "可抵扣平台收费税(本币)": 0,
    "不可抵扣平台收费税(本币)": 0, "销售税率": 0, "平台所得税预提率": 0,
    "汇率(CNY/本币)": 5, "头程单价(¥/kg)": 45, "平台佣金率": .15,
    "佣金基数(本币)": 25, "广告费率": .1, "退货损失率": .03,
    "仓储损失率": .015, "促销费率": .01, "汇兑损耗率": .005, "目标毛利率": .25,
    "税务规则版本": "tax-test", "财务参数版本": "finance-test",
    "标价含税": false, "预提税可回收": false, "佣金含税": false,
    "销售税收缴方": "亚马逊代收", "佣金基数类型": "买家标价",
    "插件提交ID": "", "最近提交审核时间": 0, "复审次数": 0,
    "评审路径": "", "提审公式核对": 1, "插件预期贡献(本币)": 0,
    "插件预期毛利率": 0, "插件预期ROI": 0,
    "主管审核人ID": "", "最近主管审核时间": 0, "主管审核意见": "", "制图状态": "",
    "FBA官方报价(本币)": 0, "FBA报价来源": "", "FBA报价凭证": "",
    "ERP录入状态": null, "首次审核通过时间": null, "领星SKU": "",
  };
  sdk.tables = new Map([
    ["SKU选品库", main],
    ["站点税务配置", { "站点": "CA", "销售税率": 0, "标价含税": false,
      "适用地区": "全站", "商品税类": "标准", "币种": "CAD", "财务已确认": true,
      "生效日期": 0, "停用日期": null, "规则版本": "tax-test", "平台所得税预提率": 0 }],
    ["站点财务参数", { "站点": "CA", "适用品类": "通用", "汇率(CNY/本币)": 5,
      "头程单价(¥/kg)": 45, "平台佣金率": .15, "佣金计费基数": "买家标价",
      "广告费率": .1, "退货损失率": .03, "仓储损失率": .015, "促销费率": .01,
      "汇兑损耗率": .005, "目标毛利率": .25, "财务已确认": true,
      "生效日期": 0, "停用日期": null, "规则版本": "finance-test",
      "体积重除数(cm³/kg)": 6000, "确认来源": "测试确认" }],
  ]);
});

describe("主管审批与系统维护字段权限", () => {
  it("ERP自动化尚未执行、ERP只读时，主管自提仍正确返回成功", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    main["审核状态"] = "草稿"; main["提交校验状态"] = "未提交";
    main["提交运营ID"] = access.userId;
    await expect(submitSiteReview("review-1", access)).resolves.toBeUndefined();
    expect(main["审核状态"]).toEqual({ text: "审核通过" });
    expect(sdk.writes).not.toContain("ERP录入状态");
  });
  it("审批成功不等待或写入只读ERP字段，重复确认不重复写审批状态", async () => {
    await expect(decideSiteReview("review-1", "approve", "", access)).resolves.toBeUndefined();
    const count = sdk.writes.length;
    await expect(decideSiteReview("review-1", "approve", "", access)).resolves.toBeUndefined();
    expect(sdk.writes).toHaveLength(count);
    expect(sdk.writes).not.toContain("ERP录入状态");
    expect(sdk.tables.get("SKU选品库")!["审核状态"]).toMatchObject({ text: "审核通过" });
  });
  it("审批保留已有制图进度，并由自动化维护制图状态", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    main["制图状态"] = "待开始";
    await expect(decideSiteReview("review-1", "approve", "", access)).resolves.toBeUndefined();
    expect(main["制图状态"]).toBe("待开始");
    expect(sdk.writes).not.toContain("制图状态");
  });
  it("本人记录仍然禁止人工自审", async () => {
    sdk.tables.get("SKU选品库")!["提交运营ID"] = access.userId;
    await expect(decideSiteReview("review-1", "approve", "", access)).rejects.toThrow("本人提交");
    expect(sdk.writes).toHaveLength(0);
  });
});


describe("系统时间只读后的审批写入", () => {
  it.each(["approve", "return", "reject"] as const)("%s 由自动化更新时间，SDK不写只读字段", async (action) => {
    const main = sdk.tables.get("SKU选品库")!;
    const first = Date.parse("2026-10-07T15:29:08+08:00");
    main["首次审核通过时间"] = first;
    const before = Date.now();
    await decideSiteReview("review-1", action, "本次审核意见", access);
    expect(main["最近主管审核时间"]).toBeGreaterThanOrEqual(before);
    expect(main["首次审核通过时间"]).toBe(first);
    expect(sdk.writes).not.toContain("审核通过日期");
    expect(sdk.writes).not.toContain("首次审核通过时间");
    expect(sdk.writes).not.toContain("最近主管审核时间");
  });
  it("审批不再依赖客户端解析或写入时间字段", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    delete main["最近主管审核时间"];
    main["主管审核时间"] = 0;
    await decideSiteReview("review-1", "approve", "", access);
    expect(main["主管审核时间"]).toBe(0);
    expect(sdk.writes).not.toContain("主管审核时间");
    expect(sdk.writes).not.toContain("审核通过日期");
  });
  it("自动化尚未回写时间时，不重复提交同一轮校验", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    main["提交运营ID"] = access.userId;
    main["审核状态"] = "草稿";
    main["提交校验状态"] = "校验中";
    main["最近提交审核时间"] = Date.now() - 86_400_000;
    main["插件提交ID"] = `review-1-${Date.now()}`;
    await expect(submitSiteReview("review-1", access)).rejects.toThrow("无需重复提交");
    expect(sdk.writes).toHaveLength(0);
  });
});

describe("集中评审的已读快照校验", () => {
  it.each(["采购单价(¥)", "供应商名称", "插件提交ID", "主管审核意见"])("%s 在阅读后变更时禁止提交旧判断", async field => {
    const record = (await loadSiteDashboard()).reviews[0];
    sdk.tables.get("SKU选品库")![field] = field === "采购单价(¥)" ? 3 : "changed";
    await expect(decideSiteReview("review-1", "return", "需补充", access, undefined, false, record)).rejects.toThrow("提审数据已变化");
    expect(sdk.writes).toHaveLength(0);
  });
  it("写入前再次校验，拦截审核期间另一人的修改", async () => {
    const record = (await loadSiteDashboard()).reviews[0];
    sdk.beforeDetailRead = () => { sdk.tables.get("SKU选品库")!["主管审核意见"] = "另一人已修改"; };
    await expect(decideSiteReview("review-1", "approve", "通过", access, undefined, false, record)).rejects.toThrow("审核期间发生变化");
    expect(sdk.writes).toHaveLength(0);
  });
  it("快照相同沿用审批写入范围，选填报价不覆盖运营FBA", async () => {
    const record = (await loadSiteDashboard()).reviews[0];
    await decideSiteReview("review-1", "approve", "通过", access, { fee: 9.99, channel: "Fee Preview", evidence: "报价截图" }, false, record);
    expect(sdk.tables.get("SKU选品库")!["FBA费用(本币)"]).toBe(4.55);
    expect(new Set(sdk.writes)).toEqual(new Set(["审核状态", "主管审核人ID", "主管审核意见", "FBA官方报价(本币)", "FBA报价来源", "FBA报价凭证"]));
  });
});


describe("选品说明提审与审批", () => {
  it("主管自提也不能提交空说明", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    main["审核状态"] = "草稿"; main["提交校验状态"] = "未提交";
    main["提交运营ID"] = access.userId; main["选品说明"] = "  ";
    await expect(submitSiteReview("review-1", access)).rejects.toThrow("请填写选品说明");
    expect(sdk.writes).toEqual([]);
  });
  it("旧待审缺说明可处理，新提交被清空后不能通过但可打回", async () => {
    const main = sdk.tables.get("SKU选品库")!;
    main["选品说明"] = ""; main["插件提交ID"] = "selection-v1-review-1-1794196800000";
    await expect(decideSiteReview("review-1", "approve", "", access)).rejects.toThrow("请填写选品说明");
    expect(sdk.writes).toEqual([]);
    await expect(decideSiteReview("review-1", "return", "补充选品说明", access)).resolves.toBeUndefined();
    main["审核状态"] = "待审核"; main["插件提交ID"] = "review-1-1794000000000";
    await expect(decideSiteReview("review-1", "approve", "", access)).resolves.toBeUndefined();
  });
  it("主管已读后正文改变时停止审批", async () => {
    const read = (await loadSiteDashboard()).reviews[0];
    sdk.tables.get("SKU选品库")!["选品说明"] = "修改后的销售策略";
    await expect(decideSiteReview("review-1", "approve", "", access, undefined, false, read)).rejects.toThrow("提审数据已变化");
    expect(sdk.writes).toEqual([]);
  });
});
