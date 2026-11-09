import { describe, expect, it } from "vitest";
import { estimateHeadhaul } from "./headhaul";

describe("机会粗算头程", () => {
  it("按实重与体积重较大者计费", () => {
    expect(estimateHeadhaul(20, 10, 5, 400, 5000, 50)).toEqual({
      volumeKg: .2, billingKg: .4, headhaulCny: 20,
    });
    expect(estimateHeadhaul(40, 25, 10, 100, 5000, 50)).toEqual({
      volumeKg: 2, billingKg: 2, headhaulCny: 100,
    });
  });
  it("参数或包装值缺失时不猜测头程", () => {
    expect(estimateHeadhaul(20, 10, 5, 400, 0, 50)).toBeNull();
    expect(estimateHeadhaul(20, 10, 0, 400, 5000, 50)).toBeNull();
  });
});
