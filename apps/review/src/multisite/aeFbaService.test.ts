import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CardRequest } from "./rateCards";

const request: CardRequest = { site: "AE", category: "普通商品", price: 12,
  dimensionsCm: [12, 5, 1], unitWeightG: 33, saleDate: 0 };
const date = "2026-11-02";
const result = () => ({ marketplace: "AE", currency: "AED", saleDate: date, status: "estimated",
  rateVersion: "2026-10-31.2", fee: { total: 5.5, totalMinorUnits: 550, includesTax: false },
  classification: { tier: "small-envelope", plan: "low_price", shippingWeight: .033, weightUnit: "kg" } });
const response = (value: unknown) => ({ ok: true, json: async () => ({ results: [{ ok: true, result: value }] }) });
beforeEach(() => { vi.resetModules(); });
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("阿联酋预估接口", () => {
  it("映射完整包装、AED与日期，结果与官方小信封案例一致，不发送成本或人员信息", async () => {
    const fetch = vi.fn(async () => response(result())); vi.stubGlobal("fetch", fetch);
    const { fetchAeEstimates } = await import("./aeFbaService");
    const [estimate] = await fetchAeEstimates([request], date, new AbortController().signal);
    expect(estimate).toMatchObject({ fee: 5.5, shippingWeightG: 33, reference: { tier: "小信封", feeIncludesTax: false } });
    const options = (fetch.mock.calls as unknown as [string, RequestInit][])[0][1];
    expect(JSON.parse(options.body as string)).toEqual({ items: [{ marketplace: "AE", price: { amount: 12, currency: "AED" },
      package: { dimensions: { length: 12, width: 5, height: 1, unit: "cm" }, weight: { value: 33, unit: "g" } },
      saleDate: date, category: "general", program: "domestic" }] });
    expect(options.credentials).toBe("omit");
    await fetchAeEstimates([request], date, new AbortController().signal);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
  it("官方冲突保持需报价，不用旧表金额或零元填充", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...result(), status: "quote_required", fee: null,
      reason: { code: "OFFICIAL_SOURCE_CONFLICT" } })));
    const { fetchAeEstimates } = await import("./aeFbaService");
    const [estimate] = await fetchAeEstimates([request], date, new AbortController().signal);
    expect(estimate.status).toBe("quote_required"); expect(estimate.fee).toBeUndefined();
    expect(estimate.reason).toContain("官方费率存在差异");
  });
  it.each([
    { currency: "USD" }, { saleDate: "2026-11-03" },
    { fee: { total: 0, totalMinorUnits: 0, includesTax: false } },
    { fee: { total: 5.5, totalMinorUnits: 600, includesTax: false } },
    { fee: { total: 5.5, totalMinorUnits: 550, includesTax: true } },
  ])("拒绝错误币种、日期、金额或税口径：%j", async changes => {
    vi.stubGlobal("fetch", vi.fn(async () => response({ ...result(), ...changes })));
    const { fetchAeEstimates } = await import("./aeFbaService");
    await expect(fetchAeEstimates([request], date, new AbortController().signal)).rejects.toThrow();
  });
  it("429后允许重新请求，不缓存故障", async () => {
    const fetch = vi.fn().mockResolvedValueOnce({ ok: false, status: 429 }).mockResolvedValueOnce(response(result()));
    vi.stubGlobal("fetch", fetch);
    const { fetchAeEstimates } = await import("./aeFbaService");
    await expect(fetchAeEstimates([request], date, new AbortController().signal)).rejects.toThrow("繁忙");
    expect((await fetchAeEstimates([request], date, new AbortController().signal))[0].fee).toBe(5.5);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
  it("5秒超时中止请求，不无限显示计算中", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal!.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })); vi.stubGlobal("fetch", fetch);
    const { fetchAeEstimates } = await import("./aeFbaService");
    const pending = expect(fetchAeEstimates([request], date, new AbortController().signal)).rejects.toThrow("Aborted");
    await vi.advanceTimersByTimeAsync(5000); await pending;
    expect((fetch.mock.calls[0][1].signal as AbortSignal).aborted).toBe(true);
  });
  it("日期、售价和输入缺失在发请求前检查", async () => {
    const { aeInputProblem } = await import("./aeFbaService");
    expect(aeInputProblem(request, date)).toBeNull();
    expect(aeInputProblem({ ...request, price: 12.001 }, date)).toContain("两位小数");
    expect(aeInputProblem(request, "2026-02-30")).toContain("有效");
    expect(aeInputProblem({ ...request, unitWeightG: 0 }, date)).toContain("待填");
  });
});
