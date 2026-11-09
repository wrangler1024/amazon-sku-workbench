export const LATEST_REVIEW_TIME = "最近主管审核时间";

// Resolve the retained field before and after its in-place rename during rollout.
export function latestReviewTimeField(fields: ReadonlyMap<string, string>): string {
  const id = fields.get(LATEST_REVIEW_TIME) ?? fields.get("主管审核时间");
  if (!id) throw new Error(`缺少字段：${LATEST_REVIEW_TIME}`);
  return id;
}
