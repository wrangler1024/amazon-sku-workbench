import { beforeEach, describe, expect, it, vi } from "vitest";
import { estimateFbaCard } from "./rateCards";

const sdk = vi.hoisted(() => ({ calls: [] as string[], missingValue: false, missingPeak: false }));
vi.mock("@lark-opdev/block-bitable-api", () => ({
  IOpenSegmentType: { Text: 1 },
  bitable: { base: { getTableByName: async (name: string) => {
    sdk.calls.push(name);
    if (name === "选品测算参数表") throw new Error("retired table");
    const fields: Record<string, unknown> = name === "加拿大FBA费率表" ? {
      "尺寸分段": "信封Envelope", "重量下限(g)": 0, "重量上限(g)": 500,
      "配送费(CAD)": 4, "FBA附加费率": .035,
      "旺季配送费(CAD)": sdk.missingPeak ? null : 4.5,
      "低价门槛(本币)": 14, "低价折扣(本币)": sdk.missingValue ? null : .8,
      "费率版本": "test", "费率来源": "https://example.com",
    } : {};
    return { getFieldMetaList: async () => Object.keys(fields).map((id) => ({ id, name: id })),
      getRecords: async () => ({ hasMore: false,
        records: Object.keys(fields).length ? [{ recordId: "ca-rate", fields }] : [] }) };
  } } },
}));
import { loadFbaRateCards } from "./repository";

beforeEach(() => { sdk.calls = []; sdk.missingValue = false; sdk.missingPeak = false; });
describe("加拿大配送费配置迁出旧参数表", () => {
  it("旧表不存在仍可预估，常规和低价费率保持原计算结果", async () => {
    const cards = await loadFbaRateCards();
    const request = { site: "CA" as const, category: "普通商品" as const,
      dimensionsCm: [20, 15, 1] as [number, number, number], unitWeightG: 50,
      saleDate: Date.parse("2026-09-30T12:00:00+08:00"), price: 25 };
    expect(estimateFbaCard(cards, request).fee).toBeCloseTo(4 * 1.035);
    expect(estimateFbaCard(cards, { ...request, price: 13.99 }).fee).toBeCloseTo((4 - .8) * 1.035);
    expect(sdk.calls).not.toContain("选品测算参数表");
  });
  it("配置缺失时明确报错，不把空值误当零折扣", async () => {
    sdk.missingValue = true;
    await expect(loadFbaRateCards()).rejects.toThrow("配置未完整填写");
  });
  it("旺季列不可读或为空时明确报错，不静默使用常规价格", async () => {
    sdk.missingPeak = true;
    await expect(loadFbaRateCards()).rejects.toThrow("旺季配置未完整填写");
  });
});
