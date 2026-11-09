import { describe, expect, it } from "vitest";
import { summarizePendingReviews } from "./reviewSummary";

describe("主管待审汇总", () => {
  it("售价按站点分别汇总，采购和利润率按当前筛选记录汇总", () => {
    const result = summarizePendingReviews([
      { site: "AU", listedPrice: 22, purchaseCny: 13, contributionRate: .29 },
      { site: "AU", listedPrice: 30, purchaseCny: 10, contributionRate: .36 },
      { site: "CA", listedPrice: 25, purchaseCny: 2, contributionRate: null },
    ]);
    expect(result.count).toBe(3);
    expect(result.prices.get("AU")).toEqual({ min: 22, max: 30 });
    expect(result.prices.get("CA")).toEqual({ min: 25, max: 25 });
    expect(result.purchase).toEqual({ min: 2, max: 13 });
    expect(result.contributionRate).toEqual({ min: .29, max: .36 });
  });
});
