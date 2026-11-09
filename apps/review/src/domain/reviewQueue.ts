import type { CandidateRecord } from "./types";

type PendingRecord = Pick<CandidateRecord,
  "status" | "validationStatus" | "submissionId" | "submissionSource" | "submittedAt">;

/** Legacy imported rows may have a manual pending label without a submission event. */
export function needsPendingValidationWarning(record: PendingRecord): boolean {
  if (record.status !== "待审核" || record.validationStatus === "通过") return false;
  const legacyUnsubmitted = record.submissionSource === "历史" &&
    !record.submissionId && !record.submittedAt && !record.validationStatus;
  return !legacyUnsubmitted;
}
