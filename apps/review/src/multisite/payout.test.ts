import { describe, expect, it } from "vitest";
import { estimatedPayoutRate } from "./payout";

describe("预计回款占比", () => {
  it("含税标价只拆一次销售税，扣现金费用但不扣采购和头程", () => {
    expect(estimatedPayoutRate({ listedPrice: 120, netRevenue: 100,
      fbaCharged: 5, referralCharged: 15, withholdingCharged: 0,
      salesDeductions: 10 })).toBeCloseTo(70 / 120);
  });

  it("预提税即使利润可回收也属于本期现金扣款；公式未回算时不猜测", () => {
    expect(estimatedPayoutRate({ listedPrice: 116, netRevenue: 100,
      fbaCharged: 4, referralCharged: 15, withholdingCharged: 20,
      salesDeductions: 10 })).toBeCloseTo(51 / 116);
    expect(estimatedPayoutRate({ listedPrice: 116, netRevenue: null,
      fbaCharged: 4, referralCharged: 15, withholdingCharged: 20,
      salesDeductions: 10 })).toBeNull();
  });
});
