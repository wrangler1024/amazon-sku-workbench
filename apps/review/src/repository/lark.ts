import { normalizeSelectionRationale, rationaleSubmissionId, requiresSelectionRationale } from "../domain/selectionRationale";
import {
  bitable,
  FieldType,
  IOpenSegmentType,
  OperationType,
  PermissionEntity,
  ToastType,
  type IOpenAttachment,
  type IOpenCellValue,
  type IFieldMeta,
  type IRecord,
  type IRecordValue,
  type ISingleSelectField,
  type ITable,
} from "@lark-opdev/block-bitable-api";

import { calculateSelection, calculationMatches } from "../domain/calculator";
import { displayedNumberTolerance, parseFormulaNumber } from "../domain/formulaValue";
import { evaluateSubmissionGate } from "../domain/gates";
import { needsPendingValidationWarning } from "../domain/reviewQueue";
import {
  canReviewOperatingGroup,
  mergeRoleRows,
  normalizeGroup,
  reviewPathForSubmission,
} from "../domain/permissions";
import type {
  ActionFeedback,
  CandidateDraft,
  CandidateRecord,
  CalculationResult,
  CalculatorInput,
  ComplianceStatus,
  FbaRateTier,
  OperatingGroup,
  PermissionRole,
  RateSnapshot,
  ReviewPath,
  ReviewAction,
  SelectionRepository,
  SelectionSnapshot,
  UserAccess,
} from "../domain/types";
import { OPERATING_GROUPS } from "../domain/types";

interface TableLocator {
  tableId?: string;
  name: string;
}

const SKU_TABLE: TableLocator = {
  tableId: import.meta.env.VITE_REVIEW_TABLE_ID || undefined,
  name: "SKU选品库",
};
const ROLE_TABLE: TableLocator = { name: "插件人员权限配置" };

const FIELD = {
  skuNumber: "记录序号",
  productNameCn: "中文品名",
  productNameEn: "英文品名",
  category: "历史品类",
  productImage: "产品图片",
  sourceUrl: "1688链接",
  referenceAsins: "参考竞品ASIN",
  moq: "MOQ",
  supplierName: "供应商名称",
  supplierCooperation: "供应商配合度",
  sampleAndLeadTimeNotes: "打样及交期说明",
  tieredQuotationNotes: "阶梯报价说明",
  notes: "备注",
  selectionRationale: "选品说明",
  purchasePriceCny: "采购单价(¥)",
  processingCostCny: "包装加工费(¥/件)",
  lengthCm: "包装长(cm)",
  widthCm: "包装宽(cm)",
  heightCm: "包装高(cm)",
  weightG: "包装重量(g)",
  targetPriceCad: "目标售价(CAD)",
  targetSite: "历史目标站点",
  trademarkRisk: "商标侵权排查",
  patentRisk: "专利侵权排查",
  copyrightRisk: "版权侵权排查",
  complianceNotes: "侵权排查说明",
  sizeTier: "FBA尺寸分段",
  volumeWeightKg: "体积重(kg)",
  chargeableWeightKg: "计费重量(kg)",
  fbaBaseFeeCad: "FBA基础配送费(CAD)",
  lowPriceDiscountCad: "低价配送费折扣(CAD)",
  fbaSurchargeCad: "FBA附加费(CAD)",
  fbaFinalFeeCad: "FBA配送费(CAD)",
  headhaulCad: "头程运费CAD(自动)",
  estimatedProfitCad: "预估毛利/件(CAD)",
  completeContributionCad: "完整成本贡献/件(CAD)",
  completeContributionRate: "毛利率",
  roi: "ROI",
  cadPerCny: "汇率(¥→CAD)",
  headhaulCnyPerKg: "头程单价(¥/kg)",
  referralFeeRate: "平台佣金率",
  adRate: "预设广告费率",
  returnLossRate: "退货净损失率",
  storageRemovalDefectRate: "仓储/移除/残次损失率",
  promotionRate: "促销折扣率",
  fxSettlementLossRate: "汇兑/回款损耗率",
  targetContributionRate: "目标毛利率",
  lowPriceThresholdCad: "低价配送门槛(CAD)",
  lowPriceDiscountParameterCad: "低价配送费折扣参数(CAD)",
  fbaSurchargeRate: "FBA附加费率",
  submissionSource: "提交来源",
  submissionId: "插件提交ID",
  submitter: "提交运营",
  operatingGroup: "运营小组",
  submittedAt: "最近提交审核时间",
  reviewCount: "复审次数",
  calculationVersion: "计算口径版本",
  validationStatus: "提交校验状态",
  reviewPath: "评审路径",
  reviewStatus: "审核状态",
  manager: "主管审核人",
  managerComment: "主管审核意见",
} as const;

const FORMULA_POLL_MS = 500;
const FORMULA_TIMEOUT_MS = 12_000;
const LEGACY_NEW_SUBMISSIONS_CLOSED = true;

type FieldMap = Map<string, string>;

const NUMERIC_TYPES = [FieldType.Number, FieldType.Currency] as const;
const TEXT_TYPES = [FieldType.Text, FieldType.Url] as const;
const SKU_FIELD_TYPES: Record<string, readonly FieldType[]> = {
  "记录序号": [FieldType.AutoNumber],
  "币种": [FieldType.Text],
};

const ROLE_FIELD_TYPES: Record<string, readonly FieldType[]> = {
  "成员": [FieldType.User],
  "角色": [FieldType.SingleSelect],
  "启用状态": [FieldType.Checkbox],
  "生效日期": [FieldType.DateTime],
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function browserRequestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function asText(value: IOpenCellValue): string {
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (!item || typeof item !== "object") return "";
        if ("text" in item && typeof item.text === "string") return item.text;
        if ("name" in item && typeof item.name === "string") return item.name;
        return "";
      })
      .join("")
      .trim();
  }
  if (value && typeof value === "object") {
    if ("text" in value && typeof value.text === "string") return value.text;
    if ("name" in value && typeof value.name === "string") return value.name;
  }
  return "";
}

function asNumber(value: IOpenCellValue): number {
  return parseFormulaNumber(value) ?? 0;
}

function asTimestamp(value: IOpenCellValue): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const raw = asText(value);
  if (!raw) return 0;
  const numeric = Number(raw);
  if (Number.isFinite(numeric)) return numeric;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

function asBoolean(value: IOpenCellValue): boolean {
  return value === true;
}

function asUser(value: IOpenCellValue): { id: string; name: string } {
  if (Array.isArray(value)) {
    for (const item of value) {
      if (item && typeof item === "object" && "id" in item && typeof item.id === "string") {
        return {
          id: item.id,
          name:
            "name" in item && typeof item.name === "string" ? item.name : "",
        };
      }
    }
  }
  return { id: "", name: "" };
}

function asOperatingGroup(value: IOpenCellValue): OperatingGroup | "" {
  return normalizeGroup(asText(value));
}

function noAccess(userId: string): UserAccess {
  return {
    userId,
    role: "未配置",
    submitGroup: "",
    reviewGroups: [],
    tableEditable: false,
    canReviewAll: false,
    canSubmit: false,
    canReview: false,
  };
}

function textCell(text: string): IOpenCellValue {
  return text ? [{ type: IOpenSegmentType.Text, text }] : null;
}

function urlCell(url: string): IOpenCellValue {
  return url
    ? [{ type: IOpenSegmentType.Url, text: url, link: url }]
    : null;
}

function recordValue(
  record: Pick<IRecord, "fields">,
  fieldIds: FieldMap,
  fieldName: string,
): IOpenCellValue {
  const id = fieldIds.get(fieldName);
  return id ? (record.fields[id] ?? null) : null;
}

async function resolveTable(locator: TableLocator): Promise<ITable> {
  if (locator.tableId) {
    try {
      return await bitable.base.getTableById(locator.tableId);
    } catch {
      // 复制到测试 Base 后表 ID 会变化，继续按名称解析。
    }
  }
  return bitable.base.getTableByName(locator.name);
}

async function fieldMap(table: ITable): Promise<FieldMap> {
  const fields = await table.getFieldMetaList();
  return new Map(fields.map((field) => [field.name, field.id]));
}

function schemaIssues(
  tableName: string,
  fields: IFieldMeta[],
  expected: Record<string, readonly FieldType[]>,
): string[] {
  const byName = new Map<string, IFieldMeta[]>();
  for (const field of fields) {
    const matches = byName.get(field.name) ?? [];
    matches.push(field);
    byName.set(field.name, matches);
  }
  const issues: string[] = [];
  for (const [name, acceptedTypes] of Object.entries(expected)) {
    const matches = byName.get(name) ?? [];
    if (matches.length === 0) {
      issues.push(`${tableName}.${name}(缺失)`);
    } else if (matches.length > 1) {
      issues.push(`${tableName}.${name}(字段重名)`);
    } else if (!acceptedTypes.includes(matches[0].type)) {
      issues.push(`${tableName}.${name}(字段类型错误)`);
    }
  }
  return issues;
}

async function readAllRecords(table: ITable): Promise<IRecord[]> {
  const records: IRecord[] = [];
  const seenTokens = new Set<string>();
  let pageToken: string | undefined;
  while (true) {
    const page = await table.getRecords({ pageSize: 200, pageToken });
    records.push(...page.records);
    if (!page.hasMore) return records;
    if (!page.pageToken || seenTokens.has(page.pageToken)) {
      throw new Error("读取数据表分页失败");
    }
    seenTokens.add(page.pageToken);
    pageToken = page.pageToken;
  }
}

async function selectCell(
  table: ITable,
  fieldId: string,
  optionName: string,
  multiple = false,
): Promise<IOpenCellValue> {
  const field = await table.getFieldById<ISingleSelectField>(fieldId);
  const options = await field.getOptions();
  const option = options.find((item) => item.name === optionName);
  if (!option) {
    throw new Error(`字段 ${fieldId} 缺少选项“${optionName}”`);
  }
  const value = { id: option.id, text: option.name };
  return multiple ? [value] : value;
}

function requireField(fieldIds: FieldMap, name: string): string {
  const id = fieldIds.get(name);
  if (!id) throw new Error(`缺少字段：${name}`);
  return id;
}

const EMPTY_RATES: RateSnapshot = {
  cadPerCny: 0,
  headhaulCnyPerKg: 0,
  referralFeeRate: 0,
  adRate: 0,
  returnLossRate: 0,
  storageRemovalDefectRate: 0,
  promotionRate: 0,
  fxSettlementLossRate: 0,
  targetContributionRate: 0,
  lowPriceThresholdCad: 0,
  lowPriceDiscountCad: 0,
  fbaSurchargeRate: 0,
  calculationVersion: "",
};

async function loadAccess(
  roleTable: ITable,
  userId: string,
): Promise<{ access: UserAccess; memberNames: Map<string, string> }> {
  const roleFields = await fieldMap(roleTable);
  const requiredRoleFields = ["成员", "角色", "启用状态", "生效日期"];
  if (requiredRoleFields.some((name) => !roleFields.has(name))) {
    return { access: noAccess(userId), memberNames: new Map() };
  }
  const now = Date.now();
  const roleRecords = await readAllRecords(roleTable);
  const memberNames = new Map<string, string>();
  for (const record of roleRecords) {
    const user = asUser(recordValue(record, roleFields, "成员"));
    if (!user.id || memberNames.has(user.id)) continue;
    const label = asText(recordValue(record, roleFields, "配置名称")).trim();
    const parsed = label ? label.split("（")[0].split("(")[0].trim() : "";
    memberNames.set(user.id, user.name || parsed || "运营");
  }
  const myRows = roleRecords.filter((record) => {
    const user = asUser(recordValue(record, roleFields, "成员"));
    const starts = asNumber(recordValue(record, roleFields, "生效日期"));
    return (
      user.id === userId &&
      asBoolean(recordValue(record, roleFields, "启用状态")) &&
      (!starts || starts <= now)
    );
  });
  const merged = mergeRoleRows(
    myRows.map((record) => ({
      text: asText(recordValue(record, roleFields, "角色")),
      legacyGroup: asText(recordValue(record, roleFields, "所属小组")),
    })),
  );
  const access: UserAccess = {
    userId,
    role: merged.role,
    submitGroup: merged.submitGroup,
    tableEditable: false,
    reviewGroups: merged.reviewGroups,
    canReviewAll: merged.canReviewAll,
    canSubmit: merged.canSubmit,
    canReview: merged.canReview,
  };
  return { access, memberNames };
}

function ratesFromRecord(record: IRecord, fields: FieldMap): RateSnapshot {
  return {
    cadPerCny: asNumber(recordValue(record, fields, FIELD.cadPerCny)),
    headhaulCnyPerKg: asNumber(recordValue(record, fields, FIELD.headhaulCnyPerKg)),
    referralFeeRate: asNumber(recordValue(record, fields, FIELD.referralFeeRate)),
    adRate: asNumber(recordValue(record, fields, FIELD.adRate)),
    returnLossRate: asNumber(recordValue(record, fields, FIELD.returnLossRate)),
    storageRemovalDefectRate: asNumber(recordValue(record, fields, FIELD.storageRemovalDefectRate)),
    promotionRate: asNumber(recordValue(record, fields, FIELD.promotionRate)),
    fxSettlementLossRate: asNumber(recordValue(record, fields, FIELD.fxSettlementLossRate)),
    targetContributionRate: asNumber(recordValue(record, fields, FIELD.targetContributionRate)),
    lowPriceThresholdCad: asNumber(recordValue(record, fields, FIELD.lowPriceThresholdCad)),
    lowPriceDiscountCad: asNumber(recordValue(record, fields, FIELD.lowPriceDiscountParameterCad)),
    fbaSurchargeRate: asNumber(recordValue(record, fields, FIELD.fbaSurchargeRate)),
    calculationVersion: asText(recordValue(record, fields, FIELD.calculationVersion)),
  };
}

function inputFromRecord(record: IRecord, fields: FieldMap): CalculatorInput {
  const compliance = (name: string): ComplianceStatus => {
    const value = asText(recordValue(record, fields, name));
    return value === "无风险" || value === "有风险" || value === "待确认"
      ? value
      : "未排查";
  };
  return {
    productNameCn: asText(recordValue(record, fields, FIELD.productNameCn)),
    productNameEn: asText(recordValue(record, fields, FIELD.productNameEn)),
    category: asText(recordValue(record, fields, FIELD.category)),
    sourceUrl: asText(recordValue(record, fields, FIELD.sourceUrl)),
    referenceAsins: asText(recordValue(record, fields, FIELD.referenceAsins)),
    moq: asNumber(recordValue(record, fields, FIELD.moq)),
    supplierName: asText(recordValue(record, fields, FIELD.supplierName)),
    supplierCooperation: asText(recordValue(record, fields, FIELD.supplierCooperation)),
    sampleAndLeadTimeNotes: asText(recordValue(record, fields, FIELD.sampleAndLeadTimeNotes)),
    tieredQuotationNotes: asText(recordValue(record, fields, FIELD.tieredQuotationNotes)),
    notes: asText(recordValue(record, fields, FIELD.notes)),
    selectionRationale: normalizeSelectionRationale(asText(recordValue(record, fields, FIELD.selectionRationale))),
    purchasePriceCny: asNumber(recordValue(record, fields, FIELD.purchasePriceCny)),
    processingCostCny: asNumber(recordValue(record, fields, FIELD.processingCostCny)),
    lengthCm: asNumber(recordValue(record, fields, FIELD.lengthCm)),
    widthCm: asNumber(recordValue(record, fields, FIELD.widthCm)),
    heightCm: asNumber(recordValue(record, fields, FIELD.heightCm)),
    weightG: asNumber(recordValue(record, fields, FIELD.weightG)),
    targetPriceCad: asNumber(recordValue(record, fields, FIELD.targetPriceCad)),
    trademarkRisk: compliance(FIELD.trademarkRisk),
    patentRisk: compliance(FIELD.patentRisk),
    copyrightRisk: compliance(FIELD.copyrightRisk),
    complianceNotes: asText(recordValue(record, fields, FIELD.complianceNotes)),
  };
}

function calculationFromRecord(
  record: IRecord,
  fields: FieldMap,
  input: CalculatorInput,
  rates: RateSnapshot,
): CalculationResult {
  const sizeTier = asText(recordValue(record, fields, FIELD.sizeTier));
  const fbaBase = asNumber(recordValue(record, fields, FIELD.fbaBaseFeeCad));
  return {
    sizeTier:
      sizeTier === "信封Envelope" || sizeTier === "标准Standard"
        ? sizeTier
        : "不支持测算",
    volumeWeightKg: asNumber(recordValue(record, fields, FIELD.volumeWeightKg)),
    chargeableWeightKg: asNumber(recordValue(record, fields, FIELD.chargeableWeightKg)),
    fbaBaseFeeCad: fbaBase,
    lowPriceDiscountCad: asNumber(recordValue(record, fields, FIELD.lowPriceDiscountCad)),
    fbaSurchargeCad: asNumber(recordValue(record, fields, FIELD.fbaSurchargeCad)),
    fbaFinalFeeCad: asNumber(recordValue(record, fields, FIELD.fbaFinalFeeCad)),
    procurementAndProcessingCad:
      (input.purchasePriceCny + input.processingCostCny) * rates.cadPerCny,
    headhaulCad: asNumber(recordValue(record, fields, FIELD.headhaulCad)),
    estimatedProfitCad: asNumber(recordValue(record, fields, FIELD.estimatedProfitCad)),
    completeContributionCad: asNumber(recordValue(record, fields, FIELD.completeContributionCad)),
    completeContributionRate: asNumber(recordValue(record, fields, FIELD.completeContributionRate)),
    upfrontCapitalRoi: asNumber(recordValue(record, fields, FIELD.roi)),
    fbaValid: fbaBase > 0 && sizeTier !== "不支持测算",
    fbaRateLabel: sizeTier,
  };
}

function candidateFromRecord(record: IRecord, fields: FieldMap): CandidateRecord {
  const input = inputFromRecord(record, fields);
  const rates = ratesFromRecord(record, fields);
  const submitter = asUser(recordValue(record, fields, FIELD.submitter));
  const images = recordValue(record, fields, FIELD.productImage);
  const firstImage = Array.isArray(images) && images[0] ? images[0] : null;
  const imageToken = firstImage && typeof firstImage === "object"
    ? "token" in firstImage ? String(firstImage.token)
      : "file_token" in firstImage ? String(firstImage.file_token) : null
    : null;
  return {
    recordId: record.recordId,
    skuNumber: asText(recordValue(record, fields, FIELD.skuNumber)),
    submissionId: asText(recordValue(record, fields, FIELD.submissionId)),
    submissionSource: asText(recordValue(record, fields, FIELD.submissionSource)),
    submittedAt: asTimestamp(recordValue(record, fields, FIELD.submittedAt)),
    reviewCount: asNumber(recordValue(record, fields, FIELD.reviewCount)),
    status: asText(recordValue(record, fields, FIELD.reviewStatus)),
    validationStatus: asText(recordValue(record, fields, FIELD.validationStatus)),
    reviewPath: asText(recordValue(record, fields, FIELD.reviewPath)) === "主管自提免评审"
      ? "主管自提免评审"
      : "",
    submitterId: submitter.id,
    submitterName: submitter.name,
    operatingGroup: asOperatingGroup(
      recordValue(record, fields, FIELD.operatingGroup),
    ),
    hasImage: Array.isArray(images) && images.length > 0,
    productImageToken: imageToken,
    input,
    rates,
    calculation: calculationFromRecord(record, fields, input, rates),
    managerComment: asText(recordValue(record, fields, FIELD.managerComment)),
  };
}

interface Runtime {
  skuTable: ITable;
  skuFields: FieldMap;
  memberNames: Map<string, string>;
  rates: RateSnapshot;
  tiers: FbaRateTier[];
  access: UserAccess;
  missingTables: string[];
  missingFields: string[];
}

async function loadRuntime(): Promise<Runtime> {
  const missingTables: string[] = [];
  const missingFields: string[] = [];
  const resolve = async (locator: TableLocator): Promise<ITable | null> => {
    try {
      return await resolveTable(locator);
    } catch {
      missingTables.push(locator.name);
      return null;
    }
  };
  const [skuTable, roleTable] = await Promise.all([
    resolve(SKU_TABLE),
    resolve(ROLE_TABLE),
  ]);
  if (!skuTable) {
    throw new Error("找不到SKU选品库");
  }
  const skuMetas = await skuTable.getFieldMetaList();
  const skuFields = new Map(skuMetas.map((field) => [field.name, field.id]));
  missingFields.push(...schemaIssues(SKU_TABLE.name, skuMetas, SKU_FIELD_TYPES));
  if (roleTable) {
    missingFields.push(
      ...schemaIssues(
        ROLE_TABLE.name,
        await roleTable.getFieldMetaList(),
        ROLE_FIELD_TYPES,
      ),
    );
  }
  const userId = await bitable.bridge.getUserId();
  const { access, memberNames } = roleTable
    ? await loadAccess(roleTable, userId)
    : { access: noAccess(userId), memberNames: new Map<string, string>() };
  return {
    skuTable,
    skuFields,
    memberNames,
    rates: EMPTY_RATES,
    tiers: [],
    access,
    missingTables,
    missingFields,
  };
}

async function editable(table: ITable): Promise<boolean> {
  try {
    return await bitable.base.getPermission({
      entity: PermissionEntity.Table,
      param: { tableId: table.id },
      type: OperationType.Editable,
    });
  } catch {
    return false;
  }
}

async function draftFields(
  table: ITable,
  fields: FieldMap,
  draft: CandidateDraft,
  userId: string,
  operatingGroup: OperatingGroup,
  reviewCount: number,
  reviewPath: ReviewPath,
): Promise<Record<string, IOpenCellValue>> {
  const input = draft.input;
  const rates = draft.rates;
  const values: Record<string, IOpenCellValue> = {};
  const set = (name: string, value: IOpenCellValue) => {
    values[requireField(fields, name)] = value;
  };
  set(FIELD.productNameCn, textCell(input.productNameCn.trim()));
  set(FIELD.productNameEn, textCell(input.productNameEn.trim()));
  set(FIELD.category, await selectCell(table, requireField(fields, FIELD.category), input.category));
  set(FIELD.sourceUrl, urlCell(input.sourceUrl.trim()));
  set(FIELD.referenceAsins, textCell(input.referenceAsins.trim()));
  set(FIELD.moq, input.moq || null);
  set(FIELD.supplierName, textCell(input.supplierName.trim()));
  set(
    FIELD.supplierCooperation,
    input.supplierCooperation
      ? await selectCell(
          table,
          requireField(fields, FIELD.supplierCooperation),
          input.supplierCooperation,
        )
      : null,
  );
  set(FIELD.sampleAndLeadTimeNotes, textCell(input.sampleAndLeadTimeNotes.trim()));
  set(FIELD.tieredQuotationNotes, textCell(input.tieredQuotationNotes.trim()));
  set(FIELD.notes, textCell(input.notes.trim()));
  set(FIELD.selectionRationale, textCell(normalizeSelectionRationale(input.selectionRationale)));
  set(FIELD.purchasePriceCny, input.purchasePriceCny);
  set(FIELD.processingCostCny, input.processingCostCny);
  set(FIELD.lengthCm, input.lengthCm);
  set(FIELD.widthCm, input.widthCm);
  set(FIELD.heightCm, input.heightCm);
  set(FIELD.weightG, input.weightG);
  set(FIELD.targetPriceCad, input.targetPriceCad);
  set(
    FIELD.targetSite,
    await selectCell(table, requireField(fields, FIELD.targetSite), "加拿大", true),
  );
  set(FIELD.trademarkRisk, await selectCell(table, requireField(fields, FIELD.trademarkRisk), input.trademarkRisk));
  set(FIELD.patentRisk, await selectCell(table, requireField(fields, FIELD.patentRisk), input.patentRisk));
  set(FIELD.copyrightRisk, await selectCell(table, requireField(fields, FIELD.copyrightRisk), input.copyrightRisk));
  set(FIELD.complianceNotes, textCell(input.complianceNotes.trim()));
  set(FIELD.cadPerCny, rates.cadPerCny);
  set(FIELD.headhaulCnyPerKg, rates.headhaulCnyPerKg);
  set(FIELD.referralFeeRate, rates.referralFeeRate);
  set(FIELD.adRate, rates.adRate);
  set(FIELD.returnLossRate, rates.returnLossRate);
  set(FIELD.storageRemovalDefectRate, rates.storageRemovalDefectRate);
  set(FIELD.promotionRate, rates.promotionRate);
  set(FIELD.fxSettlementLossRate, rates.fxSettlementLossRate);
  set(FIELD.targetContributionRate, rates.targetContributionRate);
  set(FIELD.lowPriceThresholdCad, rates.lowPriceThresholdCad);
  set(FIELD.lowPriceDiscountParameterCad, rates.lowPriceDiscountCad);
  set(FIELD.fbaSurchargeRate, rates.fbaSurchargeRate);
  set(FIELD.submissionSource, await selectCell(table, requireField(fields, FIELD.submissionSource), "插件"));
  set(FIELD.submissionId, textCell(rationaleSubmissionId(draft.submissionId)));
  set(FIELD.submitter, [{ id: userId }]);
  set(
    FIELD.operatingGroup,
    await selectCell(
      table,
      requireField(fields, FIELD.operatingGroup),
      operatingGroup,
    ),
  );
  set(FIELD.reviewCount, reviewCount);
  set(FIELD.calculationVersion, textCell(rates.calculationVersion));
  if (reviewPath) {
    set(
      FIELD.reviewPath,
      await selectCell(table, requireField(fields, FIELD.reviewPath), reviewPath),
    );
  }
  set(FIELD.validationStatus, await selectCell(table, requireField(fields, FIELD.validationStatus), "校验中"));
  return values;
}

async function uploadImage(
  table: ITable,
  fields: FieldMap,
  recordId: string,
  file: File,
): Promise<void> {
  const [token] = await bitable.base.batchUploadFile([file]);
  if (!token) throw new Error("产品图片上传失败");
  const attachment: IOpenAttachment = {
    name: file.name,
    size: file.size,
    type: file.type || "application/octet-stream",
    token,
    timeStamp: Date.now(),
  };
  await table.setCellValue(
    requireField(fields, FIELD.productImage),
    recordId,
    [attachment],
  );
}

async function findSubmission(
  table: ITable,
  fields: FieldMap,
  submissionId: string,
): Promise<IRecord | undefined> {
  return (await readAllRecords(table)).find(
    (record) =>
      asText(recordValue(record, fields, FIELD.submissionId)) === submissionId,
  );
}

async function waitForFormulaMatch(
  table: ITable,
  fields: FieldMap,
  recordId: string,
  preview: CalculationResult,
  expectedRationale?: string,
): Promise<boolean> {
  const deadline = Date.now() + FORMULA_TIMEOUT_MS;
  while (Date.now() < deadline) {
    // Read one record snapshot so all formula fields come from the same revision.
    const record = await currentRecord(table, recordId);
    if ([FIELD.completeContributionCad, FIELD.completeContributionRate, FIELD.roi]
      .some((field) => parseFormulaNumber(recordValue(record, fields, field)) === null)) {
      await sleep(FORMULA_POLL_MS);
      continue;
    }
    const input = inputFromRecord(record, fields);
    if (expectedRationale !== undefined && input.selectionRationale !== normalizeSelectionRationale(expectedRationale)) { await sleep(FORMULA_POLL_MS); continue; }
    const rates = ratesFromRecord(record, fields);
    const actual = calculationFromRecord(record, fields, input, rates);
    if (calculationMatches(preview, actual, formulaRateTolerances(record, fields))) return true;
    await sleep(FORMULA_POLL_MS);
  }
  return false;
}

function formulaRateTolerances(record: IRecord, fields: FieldMap) {
  return {
    completeContributionRate: displayedNumberTolerance(
      recordValue(record, fields, FIELD.completeContributionRate),
      0.00010001,
    ),
    upfrontCapitalRoi: displayedNumberTolerance(
      recordValue(record, fields, FIELD.roi),
      0.00010001,
    ),
  };
}

async function finalizeValidation(
  table: ITable,
  fields: FieldMap,
  recordId: string,
  passed: boolean,
): Promise<void> {
  const validation = await selectCell(
    table,
    requireField(fields, FIELD.validationStatus),
    passed ? "通过" : "异常",
  );
  const values: Record<string, IOpenCellValue> = {
    [requireField(fields, FIELD.validationStatus)]: validation,
  };
  await table.setRecord(recordId, { fields: values });
}

async function currentRecord(table: ITable, recordId: string): Promise<IRecord> {
  const value = await table.getRecordById(recordId);
  return { recordId, fields: value.fields };
}

let productImageFieldPromise: Promise<{ table: ITable; fieldId: string }> | null = null;

export function createLarkSelectionRepository(): SelectionRepository {
  return {
    async loadSnapshot(): Promise<SelectionSnapshot> {
      const runtime = await loadRuntime();
      const isEditable = await editable(runtime.skuTable);
      // The former Canada queue is retired. All active selections use MultiSitePanel.
      const legacyRecords: IRecord[] = [];
      const candidates = legacyRecords.map((record) =>
        candidateFromRecord(record, runtime.skuFields),
      );
      for (const candidate of candidates) {
        if (!candidate.submitterName) {
          candidate.submitterName =
            runtime.memberNames.get(candidate.submitterId) ?? "";
        }
      }
      return {
        access: {
          ...runtime.access,
          tableEditable: isEditable,
        },
        integration: {
          ready:
            runtime.missingTables.length === 0 &&
            runtime.missingFields.length === 0,
          source: "feishu",
          missingTables: runtime.missingTables,
          missingFields: runtime.missingFields,
          message: isEditable
            ? "已连接亚马逊SKU登记表"
            : "当前账号没有SKU选品库编辑权限",
        },
        rates: runtime.rates,
        fbaTiers: runtime.tiers,
        returnedRecords: candidates.filter(
          (record) =>
            record.status === "打回修改" &&
            (runtime.access.role === "管理员" || record.submitterId === runtime.access.userId),
        ),
        mySubmissions: candidates
          .filter((record) =>
            (runtime.access.role === "管理员" || record.submitterId === runtime.access.userId) &&
            (Boolean(record.submissionId) || record.status === "打回修改"),
          )
          .sort((a, b) => b.submittedAt - a.submittedAt),
        pendingReviews: runtime.access.canReview
          ? candidates.filter(
              (record) =>
                record.status === "待审核" &&
                record.validationStatus === "通过" &&
                record.reviewPath !== "主管自提免评审" &&
                (runtime.access.role === "管理员" || record.submitterId !== runtime.access.userId) &&
                canReviewOperatingGroup(runtime.access, record.operatingGroup),
            )
          : [],
        excludedPendingCount: runtime.access.canReview
          ? candidates.filter(
              (record) =>
                needsPendingValidationWarning(record) &&
                (runtime.access.role === "管理员" || record.submitterId !== runtime.access.userId) &&
                canReviewOperatingGroup(runtime.access, record.operatingGroup),
            ).length
          : 0,
      };
    },

    async submitCandidate(
      draft: CandidateDraft,
      calculation: CalculationResult,
    ): Promise<ActionFeedback> {
      if (LEGACY_NEW_SUBMISSIONS_CLOSED) {
        return { ok: false, message: "加拿大旧口径已停止新建提审，请在「选品录入与评审」保存新选品草稿。" };
      }
      draft = { ...draft, submissionId: rationaleSubmissionId(draft.submissionId) };
      const runtime = await loadRuntime();
      if (!runtime.access.canSubmit || !runtime.access.submitGroup) {
        return {
          ok: false,
          message: "当前人员未配置可提交的运营角色和所属小组。",
        };
      }
      if (runtime.missingTables.length || runtime.missingFields.length) {
        return { ok: false, message: "多维表格配置不完整，已停止写入。" };
      }
      const gate = evaluateSubmissionGate(
        draft.input,
        calculation,
        draft.rates,
        Boolean(draft.imageFile),
      );
      if (!gate.passed) {
        return { ok: false, validationError: true, message: "提交硬门槛未通过。" };
      }
      const reviewPath = reviewPathForSubmission(runtime.access, runtime.access.submitGroup);
      const duplicate = await findSubmission(
        runtime.skuTable,
        runtime.skuFields,
        draft.submissionId,
      );
      if (duplicate) {
        const recovered = candidateFromRecord(duplicate, runtime.skuFields);
        if (recovered.validationStatus === "通过") {
          return {
            ok: true,
            message: `已恢复相同提交ID的记录，当前状态为${recovered.status || "已校验"}。`,
            recordId: duplicate.recordId,
          };
        }
        if (recovered.validationStatus === "异常") {
          return {
            ok: false,
            validationError: true,
            message: "已找到相同提交ID的异常记录，未重复创建；请在「提交异常」视图中核对。",
            recordId: duplicate.recordId,
          };
        }
        try {
          if (!recovered.hasImage) {
            if (!draft.imageFile) throw new Error("已恢复的记录缺少产品图片");
            await uploadImage(
              runtime.skuTable,
              runtime.skuFields,
              duplicate.recordId,
              draft.imageFile,
            );
          }
          const matched = await waitForFormulaMatch(
            runtime.skuTable,
            runtime.skuFields,
            duplicate.recordId,
            calculation,
          );
          await finalizeValidation(
            runtime.skuTable,
            runtime.skuFields,
            duplicate.recordId,
            matched,
          );
          return matched
            ? {
                ok: true,
                message: recovered.reviewPath
                  ? "已恢复上次提交，校验通过后按主管自提免评审流转，未重复创建。"
                  : "已恢复上次结果并提交主管审核，未重复创建。",
                recordId: duplicate.recordId,
              }
            : {
                ok: false,
                validationError: true,
                message: "已恢复原记录，但公式校验仍不一致，未重复创建。",
                recordId: duplicate.recordId,
              };
        } catch (reason) {
          try {
            await finalizeValidation(
              runtime.skuTable,
              runtime.skuFields,
              duplicate.recordId,
              false,
            );
          } catch {
            // 保留原始恢复错误。
          }
          return {
            ok: false,
            message: reason instanceof Error ? reason.message : String(reason),
            recordId: duplicate.recordId,
          };
        }
      }
      let recordId = "";
      try {
        const fields = await draftFields(
          runtime.skuTable,
          runtime.skuFields,
          draft,
          runtime.access.userId,
          runtime.access.submitGroup,
          0,
          reviewPath,
        );
        recordId = await runtime.skuTable.addRecord({ fields });
        if (!draft.imageFile) throw new Error("缺少产品图片");
        await uploadImage(
          runtime.skuTable,
          runtime.skuFields,
          recordId,
          draft.imageFile,
        );
        const matched = await waitForFormulaMatch(
          runtime.skuTable,
          runtime.skuFields,
          recordId,
          calculation,
        );
        await finalizeValidation(
          runtime.skuTable,
          runtime.skuFields,
          recordId,
          matched,
        );
        return matched
          ? {
              ok: true,
              message: reviewPath
                ? "已入库并校验通过，正在按主管自提免评审流转。"
                : "已入库并提交主管审核。",
              recordId,
            }
          : {
              ok: false,
              validationError: true,
              message: "已入库，但插件预览与表格公式不一致，记录已转入提交异常视图。",
              recordId,
            };
      } catch (reason) {
        if (!recordId) {
          const recovered = await findSubmission(
            runtime.skuTable,
            runtime.skuFields,
            draft.submissionId,
          );
          if (recovered) recordId = recovered.recordId;
        }
        if (recordId) {
          try {
            await finalizeValidation(
              runtime.skuTable,
              runtime.skuFields,
              recordId,
              false,
            );
          } catch {
            // 保留原始异常，记录留在校验中时由管理员核对。
          }
        }
        return {
          ok: false,
          message: reason instanceof Error ? reason.message : String(reason),
          recordId: recordId || undefined,
        };
      }
    },

    async withdrawCandidate(record: CandidateRecord): Promise<ActionFeedback> {
      const runtime = await loadRuntime();
      if (!runtime.access.canSubmit || record.submitterId !== runtime.access.userId)
        return { ok: false, message: "仅可撤回本人待审核产品。" };
      const latest = candidateFromRecord(
        await currentRecord(runtime.skuTable, record.recordId), runtime.skuFields);
      if (latest.status !== "待审核" || latest.validationStatus !== "通过" ||
          latest.submittedAt !== record.submittedAt)
        return { ok: false, message: "记录已变化，请刷新后重试。" };
      await runtime.skuTable.setRecord(record.recordId, { fields: {
        [requireField(runtime.skuFields, FIELD.reviewStatus)]: await selectCell(
          runtime.skuTable, requireField(runtime.skuFields, FIELD.reviewStatus), "打回修改"),
        [requireField(runtime.skuFields, FIELD.validationStatus)]: null,
        [requireField(runtime.skuFields, FIELD.managerComment)]: textCell("运营主动撤回修改"),
      } });
      return { ok: true, message: "已撤回，可修改后重新提交审核。", recordId: record.recordId };
    },

    async resubmitCandidate(
      record: CandidateRecord,
      draft: CandidateDraft,
      calculation: CalculationResult,
    ): Promise<ActionFeedback> {
      draft = { ...draft, submissionId: rationaleSubmissionId(draft.submissionId) };
      const runtime = await loadRuntime();
      if (
        !runtime.access.canSubmit ||
        !runtime.access.submitGroup ||
        record.submitterId !== runtime.access.userId
      ) {
        return { ok: false, message: "只能修改并重提本人被打回的记录。" };
      }
      const latest = candidateFromRecord(
        await currentRecord(runtime.skuTable, record.recordId),
        runtime.skuFields,
      );
      if (
        latest.status !== "打回修改" ||
        latest.submittedAt !== record.submittedAt
      ) {
        return { ok: false, message: "记录已变化，请刷新后重试。" };
      }
      const hasImage = Boolean(draft.imageFile) || record.hasImage;
      const gate = evaluateSubmissionGate(
        draft.input,
        calculation,
        draft.rates,
        hasImage,
      );
      if (!gate.passed) {
        return { ok: false, validationError: true, message: "提交硬门槛未通过。" };
      }
      const operatingGroup = latest.operatingGroup || runtime.access.submitGroup;
      const reviewPath = reviewPathForSubmission(runtime.access, operatingGroup);
      try {
        const fields = await draftFields(
          runtime.skuTable,
          runtime.skuFields,
          draft,
          runtime.access.userId,
          operatingGroup,
          record.reviewCount + 1,
          reviewPath,
        );
        await runtime.skuTable.setRecord(record.recordId, { fields });
        if (draft.imageFile) {
          await uploadImage(
            runtime.skuTable,
            runtime.skuFields,
            record.recordId,
            draft.imageFile,
          );
        }
        const matched = await waitForFormulaMatch(
          runtime.skuTable,
          runtime.skuFields,
          record.recordId,
          calculation,
          draft.input.selectionRationale,
        );
        await finalizeValidation(
          runtime.skuTable,
          runtime.skuFields,
          record.recordId,
          matched,
        );
        return matched
          ? {
              ok: true,
              message: reviewPath
                ? "已修改并校验通过，正在按主管自提免评审流转。"
                : "已修改并重新提交主管审核。",
              recordId: record.recordId,
            }
          : {
              ok: false,
              validationError: true,
              message: "重新提交后公式校验异常，请由管理员核对。",
              recordId: record.recordId,
            };
      } catch (reason) {
        try {
          await finalizeValidation(
            runtime.skuTable,
            runtime.skuFields,
            record.recordId,
            false,
          );
        } catch {
          // 保留现场。
        }
        return {
          ok: false,
          message: reason instanceof Error ? reason.message : String(reason),
          recordId: record.recordId,
        };
      }
    },

    async reviewCandidate(
      record: CandidateRecord,
      action: ReviewAction,
      comment: string,
      allowOverride = false,
    ): Promise<ActionFeedback> {
      const runtime = await loadRuntime();
      if (!runtime.access.canReview) {
        return { ok: false, message: "当前人员没有审批权限。" };
      }
      if (record.submitterId === runtime.access.userId) {
        return {
          ok: false,
          message: "不能审核自己提交的选品，请由上级或管理员审批。",
        };
      }
      if ((action === "return" || action === "reject") && !comment.trim()) {
        return { ok: false, message: "打回或拒绝必须填写审核意见。" };
      }
      const latestRecord = await currentRecord(runtime.skuTable, record.recordId);
      const latest = candidateFromRecord(latestRecord, runtime.skuFields);
      if (latest.reviewPath === "主管自提免评审") {
        return { ok: false, message: "该记录走主管自提免评审流程，无需人工审核。" };
      }
      if (!canReviewOperatingGroup(runtime.access, latest.operatingGroup)) {
        return { ok: false, message: "当前人员无权审批该运营小组的记录。" };
      }
      if (
        latest.status !== "待审核" ||
        latest.validationStatus !== "通过" ||
        latest.submittedAt !== record.submittedAt
      ) {
        return { ok: false, message: "该记录已被处理或已重新提交，请刷新后再审核。" };
      }
      if (normalizeSelectionRationale(latest.input.selectionRationale) !== normalizeSelectionRationale(record.input.selectionRationale))
        return { ok: false, message: "选品说明已变化，请重新读取后再审核。" };
      if (action === "approve") {
        if ([FIELD.completeContributionCad, FIELD.completeContributionRate, FIELD.roi]
          .some((field) => parseFormulaNumber(recordValue(latestRecord, runtime.skuFields, field)) === null))
          return { ok: false, validationError: true,
            message: "毛利率仍待计算，应由运营重新提交后再审核。" };
        const recalculated = calculateSelection(
          latest.input,
          latest.rates,
          runtime.tiers,
        );
        const gate = evaluateSubmissionGate(
          latest.input,
          recalculated,
          latest.rates,
          latest.hasImage,
          requiresSelectionRationale(latest.submissionId),
        );
        if (!calculationMatches(
            recalculated,
            latest.calculation,
            formulaRateTolerances(latestRecord, runtime.skuFields),
          )) {
          return {
            ok: false,
            validationError: true,
            message: "Base公式与最新测算不一致，不能审核通过。",
          };
        }
        const hardProblems = gate.items.filter((item) => !item.passed &&
          item.label !== "侵权排查通过" && item.label !== "毛利率达标");
        if (hardProblems.length) return { ok: false, validationError: true,
          message: `最新数据未满足审核条件：${hardProblems.map((item) => item.label).join("、")}` };
        const overrideReasons = gate.items.filter((item) => !item.passed)
          .map((item) => item.label);
        if (overrideReasons.length && !allowOverride) return { ok: false,
          overrideRequired: true, overrideReasons,
          message: `以下指标需主管确认例外通过：${overrideReasons.join("、")}` };
        if (overrideReasons.length && !comment.trim()) return { ok: false,
          message: "例外通过必须填写主管审核意见。" };
      }
      const status =
        action === "approve"
          ? "审核通过"
          : action === "return"
            ? "打回修改"
            : "拒绝";
      const fields: Record<string, IOpenCellValue> = {
        [requireField(runtime.skuFields, FIELD.reviewStatus)]: await selectCell(
          runtime.skuTable,
          requireField(runtime.skuFields, FIELD.reviewStatus),
          status,
        ),
        [requireField(runtime.skuFields, FIELD.manager)]: [
          { id: runtime.access.userId },
        ],
        [requireField(runtime.skuFields, FIELD.managerComment)]: textCell(
          comment.trim(),
        ),
      };
      if (action === "approve") {
        const erpField = runtime.skuFields.get("ERP录入状态");
        if (erpField) {
          const lingxingField = runtime.skuFields.get("领星SKU");
          const hasLingxingSku = lingxingField &&
            Boolean(asText(latestRecord.fields[lingxingField]));
          fields[erpField] = await selectCell(runtime.skuTable, erpField,
            hasLingxingSku ? "已录入" : "待录入");
        }
      }
      await runtime.skuTable.setRecord(record.recordId, { fields });
      await bitable.ui.showToast({
        toastType: ToastType.success,
        message: `已${status}`,
      });
      return {
        ok: true,
        message:
          action === "approve"
            ? "审核通过，已进入后续制图和文案流程。"
            : action === "return"
              ? "已打回运营修改。"
              : "已拒绝该选品。",
        recordId: record.recordId,
      };
    },

    async openRecord(recordId: string): Promise<void> {
      const table = await resolveTable(SKU_TABLE);
      await bitable.ui.showRecordDetailDialog({ tableId: table.id, recordId });
    },

    async getProductImageUrl(
      recordId: string,
      fileToken: string,
    ): Promise<string | null> {
      try {
        productImageFieldPromise ??= (async () => {
          const table = await resolveTable(SKU_TABLE);
          const fields = new Map((await table.getFieldMetaList()).map((field) => [field.name, field.id]));
          return { table, fieldId: requireField(fields, FIELD.productImage) };
        })();
        const { table, fieldId } = await productImageFieldPromise;
        return await table.getAttachmentUrl(fileToken, fieldId, recordId);
      } catch {
        productImageFieldPromise = null;
        return null;
      }
    },

    async deleteReturnedRecord(recordId: string): Promise<ActionFeedback> {
      const runtime = await loadRuntime();
      if (!runtime.skuTable) {
        return { ok: false, message: "多维表格配置不完整，无法删除。" };
      }
      const latest = candidateFromRecord(
        await currentRecord(runtime.skuTable, recordId),
        runtime.skuFields,
      );
      if (latest.status !== "打回修改") {
        return { ok: false, message: "仅打回状态的记录可以删除，请刷新后重试。" };
      }
      if (latest.submitterId !== runtime.access.userId) {
        return { ok: false, message: "只能删除本人提交的记录。" };
      }
      try {
        await runtime.skuTable.deleteRecord(recordId);
        await bitable.ui.showToast({
          toastType: ToastType.success,
          message: "已删除该打回记录。",
        });
        return {
          ok: true,
          message: "已删除该打回记录；如需恢复请联系管理员。",
        };
      } catch {
        return {
          ok: false,
          message: "删除失败：当前账号可能没有删除权限，请联系管理员。",
        };
      }
    },
  };
}

export { browserRequestId };
