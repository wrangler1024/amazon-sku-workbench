import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useFbaReference } from "./useFbaReference";
import type { CardEstimate, CardRequest } from "./rateCards";

const fetchEstimates = vi.hoisted(() => vi.fn());
vi.mock("./aeFbaService", async importOriginal => ({
  ...await importOriginal<typeof import("./aeFbaService")>(), fetchAeEstimates: fetchEstimates,
}));
const request: CardRequest = { site: "AE", category: "普通商品", price: 12,
  dimensionsCm: [12, 5, 1], unitWeightG: 33, saleDate: 0 };
const matched = (fee: number): CardEstimate => ({ status: "matched", reason: "test", fee,
  reference: { tier: "小信封", plan: "低价", version: "test", source: "https://sell.amazon.ae/pricing", feeIncludesTax: false } });
const advance = () => act(async () => { await vi.advanceTimersByTimeAsync(350); });
beforeEach(() => { vi.useFakeTimers(); fetchEstimates.mockReset().mockResolvedValue([matched(5.5)]); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("异步FBA参考测算", () => {
  it("350ms防抖；缺少输入和其他站点不请求AE服务", async () => {
    const { result, rerender } = renderHook(({ input }) => useFbaReference([], input, "2026-11-02"),
      { initialProps: { input: { ...request, price: 0 } } });
    await advance(); expect(fetchEstimates).not.toHaveBeenCalled();
    rerender({ input: request }); expect(result.current.loading).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(200); });
    rerender({ input: { ...request, price: 13 } }); await advance();
    expect(fetchEstimates).toHaveBeenCalledTimes(1); expect(result.current.estimate.fee).toBe(5.5);
    rerender({ input: { ...request, site: "UK" } }); await advance();
    expect(fetchEstimates).toHaveBeenCalledTimes(1); expect(result.current.loading).toBe(false);
    expect(result.current.estimate.fee).toBeUndefined();
  });
  it("修改参数立即清旧金额，迟到响应不会覆盖新输入，卸载取消请求", async () => {
    const resolvers: Array<(value: CardEstimate[]) => void> = [];
    fetchEstimates.mockImplementation(() => new Promise(resolve => resolvers.push(resolve)));
    const { result, rerender, unmount } = renderHook(({ price }) => useFbaReference([], { ...request, price }, "2026-11-02"),
      { initialProps: { price: 12 } });
    await advance(); rerender({ price: 30 });
    expect(fetchEstimates.mock.calls[0][2].aborted).toBe(true);
    expect(result.current.estimate.fee).toBeUndefined();
    await advance();
    await act(async () => { resolvers[1]([matched(7.5)]); });
    await act(async () => { resolvers[0]([matched(5.5)]); });
    expect(result.current.estimate.fee).toBe(7.5);
    unmount(); expect(fetchEstimates.mock.calls[1][2].aborted).toBe(true);
  });
  it("原包装和试包共用批量结果，不混用本地旧费率", async () => {
    fetchEstimates.mockResolvedValue([matched(6), matched(5.5)]);
    const input = { ...request, dimensionsCm: [21, 5, 1] as [number, number, number] };
    const rules = { AE: { source: "test", tiers: [{ name: "小信封", dimensions: [20, 15, 1] as [number, number, number], weightG: 100 }] } };
    const { result } = renderHook(() => useFbaReference([], input, "2026-11-02", rules));
    await advance();
    expect(fetchEstimates.mock.calls[0][0]).toHaveLength(2);
    expect(result.current.packing).toMatchObject({ currentFee: 6, trialFee: 5.5, savingPerUnit: .5, trialDimensions: [20, 5, 1] });
  });
  it("故障可重试；官方需报价时不展示零元或试包节省", async () => {
    fetchEstimates.mockRejectedValueOnce(new Error("network"));
    const { result } = renderHook(() => useFbaReference([], request, "2026-11-02"));
    await advance(); expect(result.current.unavailable).toBe(true); expect(result.current.estimate.fee).toBeUndefined();
    fetchEstimates.mockResolvedValue([{ status: "quote_required", reason: "官方冲突" }]);
    act(() => { result.current.retry(); }); await advance();
    expect(result.current.unavailable).toBe(false); expect(result.current.estimate.reason).toBe("官方冲突");
    expect(result.current.packing).toBeNull();
  });
});
