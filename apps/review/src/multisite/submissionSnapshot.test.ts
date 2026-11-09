import { describe, expect, it, vi } from "vitest";
import type { IOpenCellValue, IRecord } from "@lark-opdev/block-bitable-api";
import type { FinanceProfile, TaxProfile } from "./domain";
import { submissionSnapshotDifferences } from "./repository";

vi.mock("@lark-opdev/block-bitable-api", () => ({ bitable: {}, IOpenSegmentType: { Text: 1 } }));

describe("站点提审参数回读", () => {
  it("成本快照一致时允许后续 Base 公式稍晚刷新", () => {
    const numbers = { "FBA经济成本(本币)": 4.62, "平台佣金经济成本(本币)": 3.75,
      "广告费率": 0.1, "汇率(CNY/本币)": 4.8137 };
    const cells: Record<string, IOpenCellValue> = { ...numbers,
      "标价含税": false, "预提税可回收": false, "佣金含税": false,
      "税务规则版本": "CA-tax", "财务参数版本": "CA-finance",
      "插件提交ID": "rec-1-123", "提交校验状态": { id: "checking", text: "校验中" },
      "销售税收缴方": { id: "collector", text: "亚马逊代收" },
      "佣金基数类型": { id: "basis", text: "买家标价" },
      "利润/件(本币)": "25", // This formula value lagged in the real CA submission.
    };
    const row: IRecord = { recordId: "rec-1", fields: cells };
    const fields = new Map(Object.keys(cells).map((name) => [name, name]));
    const tax = { priceIncludesTax: false, incomeTaxWithholdingRecoverable: false,
      version: "CA-tax", collector: "亚马逊代收" } as TaxProfile;
    const finance = { referralFeeIncludesTax: false, version: "CA-finance",
      referralBasis: "买家标价" } as FinanceProfile;
    expect(submissionSnapshotDifferences(row, fields, numbers, tax, finance, "rec-1-123"))
      .toEqual([]);
    delete cells["FBA经济成本(本币)"];
    expect(submissionSnapshotDifferences(row, fields, numbers, tax, finance, "rec-1-123"))
      .toContain("FBA经济成本(本币)");
  });
});
