import type { SiteReview, OfficialFbaQuoteInput } from "./repository";

/** Compare the business snapshot the reviewer actually read, excluding resolved display names. */
export function reviewFingerprint(record: SiteReview): string {
  return JSON.stringify([record.id, record.draft, record.status, record.validation,
    record.submitterId, record.group, record.submittedAt, record.submissionId,
    record.reviewCount, record.managerComment, record.hasImage, record.imageToken,
    record.profit, record.detailMetadata, record.officialFbaFee,
    record.officialFbaSource, record.officialFbaEvidence]);
}

export function storedReviewQuote(record: SiteReview): OfficialFbaQuoteInput {
  return { fee: record.officialFbaFee || 0,
    channel: record.officialFbaSource === "Revenue Calculator" || record.officialFbaSource === "Fee Preview"
      ? record.officialFbaSource : "", evidence: record.officialFbaEvidence || "" };
}

export function reviewAttention(record: SiteReview): string[] {
  const items: string[] = [];
  if (!record.profit || !Number.isFinite(record.profit.rate)) items.push("利润指标待计算或不可读取");
  else if (record.profit.rate < .25) items.push(`毛利率 ${(record.profit.rate * 100).toFixed(2)}% 低于25%审核线`);
  if (!record.hasImage) items.push("产品图片缺失");
  const risks = [["商标", record.draft.trademarkRisk], ["专利", record.draft.patentRisk],
    ["版权", record.draft.copyrightRisk]].filter(([, risk]) => risk !== "无风险");
  if (risks.length) items.push(risks.map(([label, risk]) => `${label}${risk || "待核实"}`).join("、"));
  const missing = [!record.draft.supplierName?.trim() && "供应商", record.draft.moq == null && "MOQ",
    !record.draft.sampleAndLeadTimeNotes?.trim() && "交期"].filter(Boolean);
  if (missing.length) items.push(`${missing.join("、")}未填写或不可读取`);
  return items;
}

export function reviewDate(value: number): string {
  return value && Number.isFinite(value) ? new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(value) : "未记录";
}
