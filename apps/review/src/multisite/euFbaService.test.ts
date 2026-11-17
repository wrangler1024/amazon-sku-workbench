import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CardRequest } from "./rateCards";

const date = "2026-11-16";
beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.unstubAllGlobals(); });

describe("法国、西班牙 EUR 本土预估", () => {
  for (const [site, total] of [["FR", 2.84], ["ES", 2.93]] as const) {
    const request: CardRequest = { site, category: "普通商品", price: 25,
      dimensionsCm: [20, 15, 1], unitWeightG: 50, saleDate: 0 };
    const value = { marketplace: site, currency: "EUR", saleDate: date, program: "domestic",
      status: "estimated", rateVersion: "2026-11-16.1",
      fee: { total, totalMinorUnits: Math.round(total * 100), includesTax: false },
      classification: { tier: "light-envelope", plan: "regular", shippingWeight: .05, weightUnit: "kg" },
      assumptions: { lowPriceEligibility: "conservative", centralEuropeProgram: null } };
    it(`${site} 用 EUR 与保守低价资格，不能把德国 CEP 选项带入`, async () => {
      vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ results: [{ ok: true, result: value }] }) })));
      const { fbaRequest, fetchFbaEstimates } = await import("./aeFbaService");
      const body = fbaRequest(request, date);
      expect(body.price.currency).toBe("EUR");
      expect(body.options).toEqual({ lowPriceEligibility: "conservative" });
      const [estimate] = await fetchFbaEstimates([request], date, new AbortController().signal);
      expect(estimate).toMatchObject({ fee: total, reference: { plan: "常规", feeIncludesTax: false } });
    });
    it.each([
      { marketplace: "DE" }, { currency: "GBP" }, { program: "efn" },
      { classification: { ...value.classification, plan: "peak" } },
      { assumptions: { lowPriceEligibility: "confirmed_eligible" } },
    ])(`${site} 拒绝串站、跨境或未确认费用计划：%j`, async changes => {
      vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ results: [{ ok: true, result: { ...value, ...changes } }] }) })));
      const { fetchFbaEstimates } = await import("./aeFbaService");
      await expect(fetchFbaEstimates([request], date, new AbortController().signal)).rejects.toThrow();
    });
  }
});
