import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CardRequest } from "./rateCards";

const date = "2026-11-16";
const jp: CardRequest = { site: "JP", category: "普通商品", price: 2000,
  dimensionsCm: [20, 15, 1], unitWeightG: 50, saleDate: 0 };
const yenResult = { marketplace: "JP", currency: "JPY", saleDate: date, status: "estimated",
  rateVersion: "2026-11-16.1", fee: { total: 288, totalMinorUnits: 288, includesTax: true },
  classification: { tier: "jp-small", plan: "regular", shippingWeight: .05, weightUnit: "kg" } };
const response = (result: unknown) => ({ ok: true, json: async () => ({ results: [{ ok: true, result }] }) });
beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("全站 FBA 接口的币种、含税和计重边界", () => {
  it("日本售价必须是整数日元；结果按日元而非分解析", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response(yenResult)));
    const { fbaInputProblem, fetchFbaEstimates } = await import("./aeFbaService");
    expect(fbaInputProblem({ ...jp, price: 2000.5 }, date)).toContain("整数日元");
    const [estimate] = await fetchFbaEstimates([jp], date, new AbortController().signal);
    expect(estimate).toMatchObject({ fee: 288, shippingWeightG: 50, reference: { feeIncludesTax: true } });
  });
  it.each([
    { total: 288, totalMinorUnits: 28800, includesTax: true },
    { total: 288.5, totalMinorUnits: 288, includesTax: true },
    { total: 288, totalMinorUnits: 288, includesTax: false },
  ])("拒绝日本分/元混用、小数或含税口径错误：%j", async fee => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...yenResult, fee })));
    const { fetchFbaEstimates } = await import("./aeFbaService");
    await expect(fetchFbaEstimates([jp], date, new AbortController().signal)).rejects.toThrow();
  });
  it("美国磅重换算为克，不能将磅当作千克", async () => {
    const us = { ...jp, site: "US" as const, price: 25 };
    const value = { ...yenResult, marketplace: "US", currency: "USD",
      fee: { total: 3.63, totalMinorUnits: 363, includesTax: false },
      classification: { tier: "small-standard", plan: "peak", shippingWeight: 1, weightUnit: "lb" } };
    const fetch = vi.fn().mockResolvedValueOnce(response(value)).mockResolvedValueOnce(response({ ...value,
      classification: { ...value.classification, weightUnit: "kg" } }));
    vi.stubGlobal("fetch", fetch);
    const { fetchFbaEstimates } = await import("./aeFbaService");
    expect((await fetchFbaEstimates([us], date, new AbortController().signal))[0].shippingWeightG).toBeCloseTo(453.59237, 5);
    await expect(fetchFbaEstimates([{ ...us, price: 26 }], date, new AbortController().signal)).rejects.toThrow();
  });
  it("英国固定保守低价资格并拒绝未经确认的扩展低价结果", async () => {
    const uk = { ...jp, site: "UK" as const, price: 15 };
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...yenResult, marketplace: "UK", currency: "GBP", program: "domestic",
      fee: { total: 1.93, totalMinorUnits: 193, includesTax: false },
      classification: { tier: "light-envelope", plan: "low_price", shippingWeight: .05, weightUnit: "kg" },
      assumptions: { lowPriceEligibility: "confirmed_eligible" } })));
    const { fetchFbaEstimates, fbaRequest } = await import("./aeFbaService");
    expect(fbaRequest(uk, date)).toMatchObject({ options: { lowPriceEligibility: "conservative" } });
    await expect(fetchFbaEstimates([uk], date, new AbortController().signal)).rejects.toThrow();
  });
});
