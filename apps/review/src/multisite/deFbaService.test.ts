import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CardRequest } from "./rateCards";

const request: CardRequest = { site: "DE", category: "普通商品", price: 25,
  dimensionsCm: [20, 10, 7], unitWeightG: 900, saleDate: 0 };
const date = "2026-11-16";
const result = () => ({ marketplace: "DE", currency: "EUR", saleDate: date, program: "domestic",
  status: "estimated", rateVersion: "2026-11-16.1", fee: { total: 3.68, totalMinorUnits: 368, includesTax: false },
  classification: { tier: "small-parcel", plan: "peak", shippingWeight: .9, weightUnit: "kg" },
  assumptions: { centralEuropeProgram: false, lowPriceEligibility: "conservative" } });
beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.unstubAllGlobals(); });
const response = (value: unknown) => ({ ok: true, json: async () => ({ results: [{ ok: true, result: value }] }) });

describe("德国 FBA 参考测算", () => {
  it("使用 DE/EUR、本土配送、保守低价和非 CEP，保留手填金额", async () => {
    const fetch = vi.fn(async () => response(result())); vi.stubGlobal("fetch", fetch);
    const { fetchFbaEstimates } = await import("./aeFbaService");
    const before = structuredClone(request);
    const [estimate] = await fetchFbaEstimates([request], date, new AbortController().signal);
    expect(estimate).toMatchObject({ fee: 3.68, reference: { tier: "小包裹", plan: "旺季", feeIncludesTax: false } });
    const options = (fetch.mock.calls as unknown as [string, RequestInit][])[0][1];
    const input = JSON.parse(options.body as string).items[0];
    expect(input).toMatchObject({ marketplace: "DE", price: { amount: 25, currency: "EUR" }, program: "domestic",
      options: { centralEuropeProgram: false, lowPriceEligibility: "conservative" } });
    expect(request).toEqual(before);
  });
  it.each([
    { marketplace: "FR" }, { currency: "GBP" }, { saleDate: "2026-11-15" }, { program: "efn" },
    { assumptions: { centralEuropeProgram: true, lowPriceEligibility: "conservative" } },
    { assumptions: { centralEuropeProgram: false, lowPriceEligibility: "confirmed_eligible" } },
  ])("不接受其他站点、日期或配送计划：%j", async changes => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...result(), ...changes })));
    const { fetchFbaEstimates } = await import("./aeFbaService");
    await expect(fetchFbaEstimates([request], date, new AbortController().signal)).rejects.toThrow();
  });
  it("官方未覆盖规格仅提示核价，不回退到零费或本地旧表", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...result(), status: "quote_required", fee: null,
      reason: { code: "PACKAGE_OUT_OF_SCOPE" } })));
    const { fetchFbaEstimates } = await import("./aeFbaService");
    expect((await fetchFbaEstimates([request], date, new AbortController().signal))[0])
      .toMatchObject({ status: "quote_required", reason: expect.stringContaining("人工核价") });
  });
});
