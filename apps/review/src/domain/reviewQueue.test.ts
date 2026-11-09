import { describe, expect, it } from "vitest";
import { needsPendingValidationWarning } from "./reviewQueue";

const pending = {
  status: "待审核", validationStatus: "", submissionId: "", submittedAt: 0,
  submissionSource: "历史",
};

describe("主管审核队列异常提示", () => {
  it("忽略新版流程前手工标记的历史待审核记录", () => {
    expect(needsPendingValidationWarning(pending)).toBe(false);
  });

  it("继续提示有真实提交却缺少校验结果的记录", () => {
    expect(needsPendingValidationWarning({ ...pending, submissionId: "submit-1" })).toBe(true);
    expect(needsPendingValidationWarning({ ...pending, submittedAt: 1 })).toBe(true);
    expect(needsPendingValidationWarning({ ...pending, validationStatus: "异常" })).toBe(true);
    expect(needsPendingValidationWarning({ ...pending, submissionSource: "插件" })).toBe(true);
  });

  it("正常通过校验和非待审核记录不提示", () => {
    expect(needsPendingValidationWarning({ ...pending, validationStatus: "通过" })).toBe(false);
    expect(needsPendingValidationWarning({ ...pending, status: "草稿" })).toBe(false);
  });
});
