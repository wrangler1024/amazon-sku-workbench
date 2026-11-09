export const SELECTION_RATIONALE_FIELD = "选品说明";
export const SELECTION_RATIONALE_HINT = "请结合目标站点，简述选品依据、销售策略及竞争优势。暂无明确优势的，请说明拟验证的市场机会。";
export const SELECTION_RATIONALE_LIMIT = 2000;
const SUBMISSION_PREFIX = "selection-v1-";

export function normalizeSelectionRationale(value: string | undefined): string {
  return (value ?? "").replace(/\r\n?/g, "\n").trim();
}
export function selectionRationaleProblem(value: string | undefined, required = true): string | null {
  const text = normalizeSelectionRationale(value);
  if (required && !text) return "请填写选品说明";
  if (Array.from(text).length > SELECTION_RATIONALE_LIMIT) return "选品说明最多 2000 字符，请精简后保存";
  return null;
}
export function rationaleSubmissionId(id: string): string {
  return id.startsWith(SUBMISSION_PREFIX) ? id : `${SUBMISSION_PREFIX}${id}`;
}
export function requiresSelectionRationale(submissionId?: string): boolean {
  return Boolean(submissionId?.startsWith(SUBMISSION_PREFIX));
}
