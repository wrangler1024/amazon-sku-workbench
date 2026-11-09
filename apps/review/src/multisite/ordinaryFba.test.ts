import { describe, expect, it, vi } from "vitest";
import cardData from "../../data/fba-rate-cards-2026.json";
import canadaData from "../../data/canada-fba-rates-2026.json";
import { estimateFbaCard, type CardRequest } from "./rateCards";

vi.mock("@lark-opdev/block-bitable-api", () => ({
  IOpenSegmentType: { Text: 1 },
  bitable: { base: { getTableByName: async (name: string) => {
    const markets: Record<string, string> = { "英国FBA费率表": "UK", "澳大利亚FBA费率表": "AU" };
    const rows: Record<string, unknown>[] = name === "加拿大FBA费率表"
      ? canadaData.rows.map(row => ({ "尺寸分段": row.tier,
        "重量下限(g)": row.weightMinG, "重量上限(g)": row.weightMaxG,
        "配送费(CAD)": row.baseFee, "旺季配送费(CAD)": row.peakFee,
        "FBA附加费率": .035, "低价门槛(本币)": 14, "低价折扣(本币)": .8,
        "费率版本": "2026官方", "费率来源": canadaData.source }))
      : cardData.rows.filter(row => row.site === markets[name]).map(row => ({
        "档位键": row.key, "方案": row.plan, "尺寸分段": row.tier, "适用品类": row.category,
        "标价下限(本币)": row.priceMin, "标价上限(本币)": row.priceMax,
        "最长边上限(cm)": row.maxDimsCm[0], "中间边上限(cm)": row.maxDimsCm[1],
        "最短边上限(cm)": row.maxDimsCm[2], "实重上限(g)": row.maxUnitWeightG,
        "计费重方式": row.shippingMethod === "unit" ? "实重" : "实重与体积重取大",
        "体积重除数(cm³/kg)": row.volumeDivisor,
        "计费重下限(g)": row.weightMinG, "计费重上限(g)": row.weightMaxG,
        "配送费(本币)": row.baseFee, "FBA附加费率": row.surchargeRate,
        "报价已含附加费": row.includesSurcharge, "配送费含税": row.feeIncludesTax,
        "生效日期": row.effectiveFrom, "停用日期": row.effectiveUntil,
        "费率版本": row.version, "官方来源": row.source, "尺寸优先级": row.priority,
      }));
    return { getFieldMetaList: async () => Object.keys(rows[0] ?? {}).map(id => ({ id, name: id })),
      getRecords: async () => ({ hasMore: false,
        records: rows.map((fields, i) => ({ recordId: `${name}-${i}`, fields })) }) };
  } } },
}));
import { loadFbaRateCards } from "./repository";

const request = (site: CardRequest["site"], price = 25, date = "2026-10-31"): CardRequest => ({
  site, category: "普通商品", price, dimensionsCm: [20, 15, 1], unitWeightG: 50,
  saleDate: Date.parse(`${date}T12:00:00+08:00`),
});

describe("三站普通商品官方费率与实际读取路径", () => {
  it.each([[12.99, 6.33], [13, 7.24], [19.99, 7.24], [20, 7.24]])(
    "澳洲售价 %s 读取现行普通类目费用 %s", async (price, fee) => {
      const result = estimateFbaCard(await loadFbaRateCards(), {
        ...request("AU", price), dimensionsCm: [10, 20, 15], unitWeightG: 400,
      });
      expect(result).toMatchObject({ status: "matched", shippingWeightG: 750, fee });
    });

  it("澳洲信封只按实重，超过实重上限才进入包裹", async () => {
    const cards = await loadFbaRateCards();
    const envelope = { ...request("AU"), dimensionsCm: [33, 23, 2.5] as [number, number, number], unitWeightG: 75 };
    expect(estimateFbaCard(cards, envelope)).toMatchObject({ fee: 4.58, shippingWeightG: 75 });
    expect(estimateFbaCard(cards, { ...envelope, unitWeightG: 75.1 })).toMatchObject({ fee: 5.26, shippingWeightG: 76 });
  });

  it.each([[9.99, 1.52], [10, 1.52], [10.001, 1.89], [10.01, 1.89], [15, 1.89], [20, 1.89], [20.01, 1.89]])(
    "英国售价 %s 使用约定的普通商品口径", async (price, baseFee) => {
      const result = estimateFbaCard(await loadFbaRateCards(), request("UK", price));
      expect(result.status).toBe("matched");
      expect(result.fee).toBeCloseTo(baseFee * 1.015);
    });

  it.each([8, 15])("英国售价 %s 超出低价尺寸时回退旺季普通档", async price => {
    const result = estimateFbaCard(await loadFbaRateCards(), {
      ...request("UK", price), dimensionsCm: [40, 30, 10], unitWeightG: 500,
    });
    expect(result.card?.plan).toBe("旺季");
    expect(result.fee).toBeCloseTo(3.62 * 1.015);
  });

  it.each([["2026-10-14", 2.72], ["2026-10-15", 2.79], ["2027-01-14", 2.79], ["2027-01-15", 2.72]])(
    "英国 %s 大信封正确切换季节且旺季结束恢复常规", async (date, baseFee) => {
      const result = estimateFbaCard(await loadFbaRateCards(), {
        ...request("UK", 15, date), dimensionsCm: [30, 20, 3], unitWeightG: 500,
      });
      expect(result.fee).toBeCloseTo(baseFee * 1.015);
    });

  it.each([["2026-10-14", 4.46], ["2026-10-15", 4.73], ["2027-01-14", 4.73], ["2027-01-15", 4.46]])(
    "加拿大 %s 从已有旺季列取价并恢复常规", async (date, fee) => {
      const result = estimateFbaCard(await loadFbaRateCards(), request("CA", 25, date));
      expect(result.fee).toBeCloseTo(fee * 1.035);
    });

  it("加拿大体积重除数5000、100克向上计费与旺季同时生效", async () => {
    const result = estimateFbaCard(await loadFbaRateCards(), {
      ...request("CA"), dimensionsCm: [30, 20, 5], unitWeightG: 200,
    });
    expect(result.shippingWeightG).toBe(600);
    expect(result.fee).toBeCloseTo(7.84 * 1.035);
  });

  it.each([[13.99, 4.73 - .8], [14, 4.73]])("加拿大售价 %s 的折扣与附加费不重复计算", async (price, fee) => {
    expect(estimateFbaCard(await loadFbaRateCards(), request("CA", price)).fee).toBeCloseTo(fee * 1.035);
  });

  it("加拿大1500克以上按每100克递增，旺季用11元起价", async () => {
    const cards = await loadFbaRateCards();
    const item = { ...request("CA"), dimensionsCm: [20, 15, 10] as [number, number, number], unitWeightG: 1500.1 };
    expect(estimateFbaCard(cards, item)).toMatchObject({ shippingWeightG: 1600 });
    expect(estimateFbaCard(cards, item).fee).toBeCloseTo(11.09 * 1.035);
    expect(estimateFbaCard(cards, { ...item, saleDate: request("CA", 25, "2027-01-15").saleDate }).fee)
      .toBeCloseTo(10.41 * 1.035);
  });
});
