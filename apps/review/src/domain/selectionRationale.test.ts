import { describe, expect, it } from "vitest";
import { normalizeSelectionRationale, rationaleSubmissionId, requiresSelectionRationale, selectionRationaleProblem } from "./selectionRationale";

describe("选品说明填写与提交版本", () => {
  it("草稿允许空白，新提审要求正文，不强制差异化或最低字数", () => {
    expect(selectionRationaleProblem(" \n ", false)).toBeNull();
    expect(selectionRationaleProblem(" \n ")).toBe("请填写选品说明");
    expect(selectionRationaleProblem("暂无明确优势，拟小批量测试窄尺寸需求。")).toBeNull();
    expect(normalizeSelectionRationale("  第一段\r\n\r\n第二段  ")).toBe("第一段\n\n第二段");
  });
  it("长度按字符计算并明确拒绝超长正文，不截断", () => {
    expect(selectionRationaleProblem("🛒".repeat(2000))).toBeNull();
    expect(selectionRationaleProblem("品".repeat(2001), false)).toContain("最多 2000");
  });
  it("历史提交兼容，重提得到新版本且保持幂等和时间后缀", () => {
    expect(requiresSelectionRationale("rec-1-1794196800000")).toBe(false);
    const id = rationaleSubmissionId("rec-1-1794196800000");
    expect(requiresSelectionRationale(id)).toBe(true);
    expect(rationaleSubmissionId(id)).toBe(id);
    expect(id.match(/-(\d{13})$/)?.[1]).toBe("1794196800000");
  });
});
