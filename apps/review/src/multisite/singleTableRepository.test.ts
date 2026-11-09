import { beforeEach, describe, expect, it, vi } from "vitest";
import { newSiteDraft } from "./domain";
import type { UserAccess } from "../domain/types";

const store = vi.hoisted(() => ({
  rows: [] as Array<{ recordId: string; fields: Record<string, unknown> }>,
  memberRows: [] as Array<{ recordId: string; fields: Record<string, unknown> }>,
  directoryAvailable: true,
}));
const fieldNames = ["选品SKU", "目标站点", "币种", "中文品名", "英文品名", "品类",
  "预计开售日期", "适用地区", "商品税类", "1688链接", "参考竞品ASIN",
  "供应商名称", "MOQ", "供应商配合度", "打样及交期说明", "阶梯报价说明",
  "备注", "选品说明", "侵权排查说明", "目标标价(本币)", "FBA费用(本币)", "采购单价(¥)",
  "包装加工费(¥/件)", "包装长(cm)", "包装宽(cm)", "包装高(cm)",
  "包装重量(g)", "商标侵权排查", "专利侵权排查", "版权侵权排查",
  "审核状态", "提交校验状态", "提交运营ID", "提交运营", "运营小组",
  "审批小组", "产品图片", "记录序号", "利润/件(本币)", "毛利率",
  "前置资金ROI"];
let schemaFields = [...fieldNames];
const main = {
  id: "table-main",
  getFieldMetaList: async () => schemaFields.map((name) => ({ name, id: name })),
  getRecords: async () => ({ records: store.rows, hasMore: false }),
  getFieldById: async () => ({ getOptions: async () =>
    ["CA", "AU", "AE", "SA", "JP", "标准", "未排查", "无风险", "草稿", "未提交", "一部一组", "高"]
      .map((name) => ({ name, id: name })) }),
  addRecord: async ({ fields }: { fields: Record<string, unknown> }) => {
    const recordId = `record-${store.rows.length + 1}`;
    store.rows.push({ recordId, fields });
    return recordId;
  },
  setRecord: async (recordId: string, payload: { fields: Record<string, unknown> }) => {
    Object.assign(store.rows.find((row) => row.recordId === recordId)!.fields, payload.fields);
  },
};
vi.mock("@lark-opdev/block-bitable-api", () => ({
  IOpenSegmentType: { Text: 1 },
  bitable: { base: { getTableByName: async (name: string) => {
    if (name === "SKU选品库") return main;
    if (name === "插件人员权限配置" && store.directoryAvailable) return {
      getFieldMetaList: async () => ["成员", "配置名称"].map((field) => ({ name: field, id: field })),
      getRecords: async () => ({ records: store.memberRows, hasMore: false }),
    };
    if (name === "FBA尺寸档规则") return {
      getFieldMetaList: async () => ["站点", "档位名称", "最长边上限(cm)",
        "中间边上限(cm)", "最短边上限(cm)", "实重上限(g)", "规则来源",
        "排序", "启用"].map((field) => ({ name: field, id: field })),
      getRecords: async () => ({ hasMore: false, records: ["CA", "MX", "UK", "US", "AU", "AE", "SA", "JP"]
        .map((site) => ({ recordId: `size-${site}`, fields: {
          "站点": { id: site, text: site }, "档位名称": "信封档",
          "最长边上限(cm)": 38, "中间边上限(cm)": 27,
          "最短边上限(cm)": 2, "实重上限(g)": 500,
          "规则来源": "https://example.com", "排序": 1, "启用": true,
        } })) }),
    };
    if (name === "站点税务配置" || name === "站点财务参数")
      return { getFieldMetaList: async () => [],
        getRecords: async () => ({ records: [], hasMore: false }) };
    throw new Error(`unexpected table ${name}`);
  } } },
}));

import { loadSiteDashboard, saveSiteDraft } from "./repository";

const rich = (value: string) => [{ type: 1, text: value }];
const selected = (value: string) => ({ id: value, text: value });
const access = { canSubmit: true, submitGroup: "一部一组", userId: "user-1" } as UserAccess;

beforeEach(() => {
  schemaFields = [...fieldNames];
  store.directoryAvailable = true;
  store.memberRows = [{ recordId: "member-1", fields: {
    "成员": [{ id: "user-1", name: "示例运营丙" }], "配置名称": rich("示例运营丙（一部一组运营）"),
  } }];
  store.rows = [
    { recordId: "legacy-ca", fields: { "目标站点": selected("CA"), "中文品名": rich("旧加拿大记录") } },
    { recordId: "ca", fields: { "选品SKU": rich("SAME-CODE"), "目标站点": selected("CA"),
      "币种": rich("CAD"), "中文品名": rich("加拿大产品"), "审核状态": selected("待审核") } },
    { recordId: "au", fields: { "选品SKU": rich("SAME-CODE"), "目标站点": selected("AU"),
      "币种": rich("AUD"), "中文品名": rich("澳洲产品"), "审核状态": selected("草稿") } },
  ];
});

describe("单表选品评审", () => {
  it.each([["SA", "SAR"], ["JP", "JPY"]] as const)("%s 草稿在财务缺失时仍以本币保存并回读", async (site, currency) => {
    const id = await saveSiteDraft({ ...newSiteDraft(site), productName: "扩站验收草稿",
      referenceAsins: "B0TEST0005", listedPrice: 1200, fbaFeeLocal: 252 }, access, null);
    const loaded = await loadSiteDashboard();
    expect(loaded.reviews.find(row => row.id === id)?.draft).toMatchObject({ site, listedPrice: 1200, fbaFeeLocal: 252 });
    expect(store.rows.find(row => row.recordId === id)?.fields["币种"]).toEqual(rich(currency));
    expect(loaded.reviews.find(row => row.id === "ca")?.draft.site).toBe("CA");
  });
  it("AE 草稿保存到统一选品库，使用 AED 并保留提交人姓名", async () => {
    const id = await saveSiteDraft({ ...newSiteDraft("AE"), productName: "阿联酋验收样例",
      listedPrice: 105, fbaFeeLocal: 7.5 }, access, null);
    const loaded = await loadSiteDashboard();
    const saved = loaded.reviews.find((row) => row.id === id)!;
    expect(saved.draft.site).toBe("AE");
    expect(saved.draft.fbaFeeLocal).toBe(7.5);
    expect(saved.submitterName).toBe("示例运营丙");
    expect(store.rows.find((row) => row.recordId === id)?.fields["币种"]).toEqual(rich("AED"));
  });
  it("同一选品SKU的不同站点各自成行，历史加拿大记录不进入新评审队列", async () => {
    const dashboard = await loadSiteDashboard();
    expect(dashboard.problems).toEqual([]);
    expect(dashboard.sizeRules?.CA?.tiers[0]).toMatchObject({
      name: "信封档", dimensions: [38, 27, 2], weightG: 500,
    });
    expect(dashboard.reviews.map((review) => [review.id, review.draft.site, review.draft.entitySku]))
      .toEqual([["ca", "CA", "SAME-CODE"], ["au", "AU", "SAME-CODE"]]);
  });

  it("同码新草稿直接写入选品库，不查询独立站点评审表", async () => {
    const draft = newSiteDraft("CA");
    draft.entitySku = "SAME-CODE";
    draft.productName = "同码新机会";
    const id = await saveSiteDraft(draft, access, null);
    expect(id).toBe("record-4");
    expect(store.rows).toHaveLength(4);
    const dashboard = await loadSiteDashboard();
    expect(dashboard.reviews.find((review) => review.id === id)?.draft.productName)
      .toBe("同码新机会");
  });
  it("选品SKU留空的草稿仍按记录ID保存和回读", async () => {
    const draft = newSiteDraft("CA");
    draft.productName = "未编码的选品";
    const id = await saveSiteDraft(draft, access, null);
    expect(store.rows.find((row) => row.recordId === id)?.fields["选品SKU"]).toBeNull();
    const review = (await loadSiteDashboard()).reviews.find((item) => item.id === id);
    expect(review?.draft.entitySku).toBe("");
    expect(review?.number).toBe(id);
  });
  it("新草稿持久化运营姓名，后续读不到人员配置时仍可显示姓名", async () => {
    store.memberRows.unshift({ recordId: "other-member", fields: {
      "成员": [{ id: "user-2", name: "其他运营" }], "配置名称": rich("其他运营（一部一组运营）"),
    } });
    const draft = newSiteDraft("CA");
    draft.productName = "带运营姓名的新草稿";
    const id = await saveSiteDraft(draft, access, null);
    expect(store.rows.find((row) => row.recordId === id)?.fields["提交运营"])
      .toEqual([{ id: "user-1", name: "示例运营丙" }]);
    store.directoryAvailable = false;
    const review = (await loadSiteDashboard()).reviews.find((item) => item.id === id);
    expect(review?.submitterId).toBe("user-1");
    expect(review?.submitterName).toBe("示例运营丙");
  });
  it("人员单元格未返回姓名时使用对应配置名称，保留同一个人员ID", async () => {
    store.memberRows[0].fields["成员"] = [{ id: "user-1" }];
    const draft = newSiteDraft("AU");
    draft.productName = "使用人员配置姓名";
    const id = await saveSiteDraft(draft, access, null);
    expect(store.rows.find((row) => row.recordId === id)?.fields["提交运营"])
      .toEqual([{ id: "user-1", name: "示例运营丙" }]);
  });
  it("无法解析当前运营姓名时不创建无名记录", async () => {
    store.memberRows = [];
    const draft = newSiteDraft("CA");
    draft.productName = "不应创建";
    await expect(saveSiteDraft(draft, access, null)).rejects.toThrow("未读取到当前运营姓名");
    expect(store.rows).toHaveLength(3);
  });
  it("统一利润字段能读取每件利润", async () => {
    store.rows[1].fields["利润/件(本币)"] = 9;
    store.rows[1].fields["毛利率"] = .36;
    store.rows[1].fields["前置资金ROI"] = 1.5;
    expect((await loadSiteDashboard()).reviews.find((item) => item.id === "ca")?.profit?.contribution)
      .toBe(9);
  });
});


describe("选品说明持久化", () => {
  it("多段正文保存回填，空白草稿仍可保存", async () => {
    const draft = { ...newSiteDraft("CA"), productName: "说明验收", selectionRationale: "依据：窄尺寸需求。\n\n策略：测试单件组合。" };
    const id = await saveSiteDraft(draft, access, null);
    const loaded = (await loadSiteDashboard()).reviews.find(item => item.id === id)!;
    expect(loaded.draft.selectionRationale).toBe(draft.selectionRationale);
    await saveSiteDraft({ ...loaded.draft, selectionRationale: "" }, access, id);
    expect((await loadSiteDashboard()).reviews.find(item => item.id === id)!.draft.selectionRationale).toBe("");
  });
  it("缺少字段权限时明确报错，避免保存成功假象", async () => {
    schemaFields = schemaFields.filter(name => name !== "选品说明");
    expect((await loadSiteDashboard()).problems.join()).toContain("选品说明字段缺失或不可读取");
    await expect(saveSiteDraft(newSiteDraft(), access, null)).rejects.toThrow("缺少字段：选品说明");
  });
  it("超长内容不写入记录", async () => {
    const before = structuredClone(store.rows);
    await expect(saveSiteDraft({ ...newSiteDraft(), selectionRationale: "品".repeat(2001) }, access, null)).rejects.toThrow("最多 2000");
    expect(store.rows).toEqual(before);
  });
});
