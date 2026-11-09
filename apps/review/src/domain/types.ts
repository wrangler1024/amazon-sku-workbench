export const OPERATING_GROUPS = [
  "一部一组",
  "一部二组",
  "二部一组",
  "二部二组",
] as const;

export type OperatingGroup = (typeof OPERATING_GROUPS)[number];

export type PermissionRole = "运营" | "运营主管" | "运营总监" | "管理员" | "未配置";

export type ComplianceStatus = "未排查" | "无风险" | "有风险" | "待确认";
export type ReviewPath = "" | "主管自提免评审";

export interface RateSnapshot {
  cadPerCny: number;
  headhaulCnyPerKg: number;
  referralFeeRate: number;
  adRate: number;
  returnLossRate: number;
  storageRemovalDefectRate: number;
  promotionRate: number;
  fxSettlementLossRate: number;
  targetContributionRate: number;
  lowPriceThresholdCad: number;
  lowPriceDiscountCad: number;
  fbaSurchargeRate: number;
  calculationVersion: string;
}

export interface FbaRateTier {
  sizeTier: "信封Envelope" | "标准Standard";
  lowerWeightG: number;
  upperWeightG: number;
  feeCad: number;
  label: string;
}

export interface CalculatorInput {
  productNameCn: string;
  productNameEn: string;
  category: string;
  sourceUrl: string;
  referenceAsins: string;
  moq: number;
  supplierName: string;
  supplierCooperation: string;
  sampleAndLeadTimeNotes: string;
  tieredQuotationNotes: string;
  notes: string;
  selectionRationale: string;
  purchasePriceCny: number;
  processingCostCny: number;
  lengthCm: number;
  widthCm: number;
  heightCm: number;
  weightG: number;
  targetPriceCad: number;
  trademarkRisk: ComplianceStatus;
  patentRisk: ComplianceStatus;
  copyrightRisk: ComplianceStatus;
  complianceNotes: string;
}

export interface QuickEstimateInput {
  salePriceCad: number | null;
  purchaseCostCny: number | null;
  headhaulCny: number | null;
  fbaFeeCad: number | null;
  marketingAfterSalesRate: number | null;
  cadPerCny: number;
  referralFeeRate: number;
}

export interface QuickEstimateResult {
  salesRevenueCny: number;
  salesCommissionCad: number;
  salesCommissionCny: number;
  fbaFeeCny: number;
  marketingAfterSalesCostCny: number;
  quickProfitCny: number;
  quickMarginRate: number;
  upfrontCapitalRoi: number;
}

export type FbaSizeTier =
  | "信封Envelope"
  | "标准Standard"
  | "不支持测算";

export interface CalculationResult {
  sizeTier: FbaSizeTier;
  volumeWeightKg: number;
  chargeableWeightKg: number;
  fbaBaseFeeCad: number;
  lowPriceDiscountCad: number;
  fbaSurchargeCad: number;
  fbaFinalFeeCad: number;
  procurementAndProcessingCad: number;
  headhaulCad: number;
  estimatedProfitCad: number;
  completeContributionCad: number;
  completeContributionRate: number;
  upfrontCapitalRoi: number;
  fbaValid: boolean;
  fbaRateLabel: string;
}

export interface GateResult {
  passed: boolean;
  items: Array<{ label: string; passed: boolean; detail: string }>;
}

export interface CandidateDraft {
  submissionId: string;
  input: CalculatorInput;
  rates: RateSnapshot;
  imageFile: File | null;
}

export interface CandidateRecord {
  recordId: string;
  skuNumber: string;
  submissionId: string;
  submissionSource?: string;
  submittedAt: number;
  reviewCount: number;
  status: string;
  validationStatus: string;
  reviewPath: ReviewPath;
  submitterId: string;
  submitterName: string;
  operatingGroup: OperatingGroup | "";
  hasImage: boolean;
  productImageToken: string | null;
  input: CalculatorInput;
  rates: RateSnapshot;
  calculation: CalculationResult;
  managerComment: string;
}

export interface UserAccess {
  userId: string;
  role: PermissionRole;
  submitGroup: OperatingGroup | "";
  reviewGroups: OperatingGroup[];
  canReviewAll: boolean;
  canSubmit: boolean;
  canReview: boolean;
  tableEditable: boolean;
}

export interface IntegrationStatus {
  ready: boolean;
  source: "feishu" | "mock";
  missingTables: string[];
  missingFields: string[];
  message: string;
}

export interface SelectionSnapshot {
  access: UserAccess;
  integration: IntegrationStatus;
  rates: RateSnapshot;
  fbaTiers: FbaRateTier[];
  returnedRecords: CandidateRecord[];
  mySubmissions: CandidateRecord[];
  pendingReviews: CandidateRecord[];
  excludedPendingCount: number;
}

export type ReviewAction = "approve" | "return" | "reject";

export interface ActionFeedback {
  ok: boolean;
  message: string;
  recordId?: string;
  validationError?: boolean;
  overrideRequired?: boolean;
  overrideReasons?: string[];
}

export interface SelectionRepository {
  loadSnapshot(): Promise<SelectionSnapshot>;
  submitCandidate(
    draft: CandidateDraft,
    calculation: CalculationResult,
  ): Promise<ActionFeedback>;
  resubmitCandidate(
    record: CandidateRecord,
    draft: CandidateDraft,
    calculation: CalculationResult,
  ): Promise<ActionFeedback>;
  withdrawCandidate(record: CandidateRecord): Promise<ActionFeedback>;
  reviewCandidate(
    record: CandidateRecord,
    action: ReviewAction,
    comment: string,
    allowOverride?: boolean,
  ): Promise<ActionFeedback>;
  openRecord(recordId: string): Promise<void>;
  getProductImageUrl(
    recordId: string,
    fileToken: string,
  ): Promise<string | null>;
  deleteReturnedRecord(recordId: string): Promise<ActionFeedback>;
}
