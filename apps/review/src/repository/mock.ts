import { calculateSelection } from "../domain/calculator";
import type {
  ActionFeedback,
  CandidateDraft,
  CandidateRecord,
  CalculationResult,
  ReviewAction,
  SelectionRepository,
} from "../domain/types";
import { SAMPLE_INPUT, SAMPLE_RATES, SAMPLE_TIERS } from "../domain/sample";

const now = Date.now();

function buildRecord(
  overrides: Partial<CandidateRecord>,
): CandidateRecord {
  const input = overrides.input ?? SAMPLE_INPUT;
  const rates = overrides.rates ?? SAMPLE_RATES;
  return {
    recordId: "rec-mock-1",
    skuNumber: "JP-MOCK-001",
    submissionId: "mock-submission-1",
    submittedAt: now - 60_000,
    reviewCount: 0,
    status: "待审核",
    validationStatus: "通过",
    reviewPath: "",
    submitterId: "mock-user",
    submitterName: "演示运营",
    productImageToken: null,
    operatingGroup: "一部一组",
    hasImage: true,
    input,
    rates,
    calculation: calculateSelection(input, rates, SAMPLE_TIERS),
    managerComment: "",
    ...overrides,
  };
}

function delayed(feedback: ActionFeedback): Promise<ActionFeedback> {
  return new Promise((resolve) => {
    window.setTimeout(() => resolve(feedback), 250);
  });
}

export function createMockSelectionRepository(): SelectionRepository {
  let pending = [buildRecord({ submitterId: "mock-other-user", submitterName: "演示运营" })];
  let returned = [
    buildRecord({
      recordId: "rec-mock-returned",
      skuNumber: "JP-MOCK-002",
      submissionId: "mock-submission-2",
      status: "打回修改",
      managerComment: "请补充更清晰的专利检索依据。",
    }),
  ];
  let mySubmissions = [
    ...returned,
    buildRecord({
      recordId: "rec-mock-approved",
      skuNumber: "JP-MOCK-003",
      status: "审核通过",
      reviewPath: "主管自提免评审",
      submittedAt: now - 3_600_000,
    }),
  ];

  return {
    async loadSnapshot() {
      return {
        access: {
          userId: "mock-user",
          role: "运营主管",
          submitGroup: "一部一组",
          reviewGroups: ["一部一组"],
          canReviewAll: false,
          canSubmit: true,
          canReview: true,
    tableEditable: true,
        },
        integration: {
          ready: true,
          source: "mock",
          missingTables: [],
          missingFields: [],
          message: "本地模拟数据",
        },
        rates: SAMPLE_RATES,
        fbaTiers: SAMPLE_TIERS,
        returnedRecords: returned,
        mySubmissions,
        pendingReviews: pending,
        excludedPendingCount: 0,
      };
    },

    submitCandidate(
      draft: CandidateDraft,
      calculation: CalculationResult,
    ): Promise<ActionFeedback> {
      mySubmissions = [
        buildRecord({
          recordId: `rec-${draft.submissionId}`,
          skuNumber: `JP-MOCK-${String(pending.length + mySubmissions.length + 3).padStart(3, "0")}`,
          submissionId: draft.submissionId,
          submittedAt: Date.now(),
          status: "审核通过",
          reviewPath: "主管自提免评审",
          input: draft.input,
          rates: draft.rates,
          calculation,
        }),
        ...mySubmissions,
      ];
      return delayed({ ok: true, message: "已按主管自提免评审流转", recordId: mySubmissions[0].recordId });
    },

    withdrawCandidate(record: CandidateRecord): Promise<ActionFeedback> {
      const recalled = { ...record, status: "打回修改", managerComment: "运营主动撤回修改" };
      mySubmissions = mySubmissions.map((item) => item.recordId === record.recordId ? recalled : item);
      returned = [recalled, ...returned.filter((item) => item.recordId !== record.recordId)];
      return delayed({ ok: true, message: "已撤回，可修改后重新提交审核。", recordId: record.recordId });
    },

    resubmitCandidate(
      record: CandidateRecord,
      draft: CandidateDraft,
      calculation: CalculationResult,
    ): Promise<ActionFeedback> {
      returned = returned.filter((item) => item.recordId !== record.recordId);
      mySubmissions = [
        buildRecord({
          ...record,
          status: "审核通过",
          validationStatus: "通过",
          reviewPath: "主管自提免评审",
          submittedAt: Date.now(),
          reviewCount: record.reviewCount + 1,
          input: draft.input,
          rates: draft.rates,
          calculation,
        }),
        ...mySubmissions.filter((item) => item.recordId !== record.recordId),
      ];
      return delayed({ ok: true, message: "已修改并重新提交", recordId: record.recordId });
    },

    reviewCandidate(
      record: CandidateRecord,
      action: ReviewAction,
      comment: string,
    ): Promise<ActionFeedback> {
      pending = pending.filter((item) => item.recordId !== record.recordId);
      if (action === "return") {
        returned = [
          { ...record, status: "打回修改", managerComment: comment },
          ...returned,
        ];
      }
      const message =
        action === "approve"
          ? "审核通过，已进入后续制图和文案流程"
          : action === "return"
            ? "已打回运营修改"
            : "已拒绝该选品";
      return delayed({ ok: true, message, recordId: record.recordId });
    },

    async getProductImageUrl(): Promise<string | null> {
      return null;
    },

    async deleteReturnedRecord(): Promise<ActionFeedback> {
      return { ok: false, message: "演示模式不支持删除。" };
    },

    async openRecord(): Promise<void> {
      return undefined;
    },
  };
}
