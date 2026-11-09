import { normalizeSelectionRationale, selectionRationaleProblem, rationaleSubmissionId, requiresSelectionRationale } from "../domain/selectionRationale";
import { bitable, IOpenSegmentType, type IOpenCellValue, type IRecord, type ISingleSelectField, type ITable } from "@lark-opdev/block-bitable-api";
import { calculateSite, forecastTimestamp, MARKETPLACES, submissionProblems,
  type FbaRateRule, type FinanceProfile, type Marketplace, type SiteCalculation,
  type SiteDraft, type TaxClass, type TaxProfile } from "./domain";
import type { UserAccess } from "../domain/types";
import { reviewPathForSubmission } from "../domain/permissions";
import { parseFormulaNumber } from "../domain/formulaValue";
import type { FbaCard } from "./rateCards";
import { canadaFbaCards } from "./canadaRateCards";
import type { SmallSizeRules } from "./sizing";
import { firstAttachmentToken } from "./attachment";
import { estimatedPayoutRate } from "./payout";
import { reviewFingerprint } from "./reviewPresentation";

type Fields = Map<string, string>;
type SiteStatus = "草稿" | "待审核" | "审核通过" | "打回修改" | "拒绝";
export interface SiteReview {
  id: string;
  number: string;
  draft: SiteDraft;
  status: SiteStatus;
  validation: string;
  submitterId: string;
  submitterName: string;
  group: string;
  submittedAt: number;
  submissionId?: string;
  detailMetadata?: {
    inputs: Record<string, number | null>;
    exchangeRate: number | null; shippingWeightG: number | null;
    taxVersion: string; financeVersion: string; reviewedAt: number;
  };
  reviewCount: number;
  managerComment: string;
  reviewPath?: string;
  hasImage: boolean;
  imageToken: string;
  calculation: SiteCalculation | null;
  profit?: { contribution: number; rate: number; roi: number; netRevenue: number | null;
    acquisition: number | null; headhaul: number | null; referral: number | null;
    salesTax: number | null; platformFeeTax: number | null; fbaEconomic: number | null;
    marketingAndOther: number | null; withholding: number | null;
    payoutRate: number | null } | null;
  officialFbaFee: number;
  officialFbaSource: string;
  officialFbaEvidence: string;
}
export interface SiteDashboard {
  reviews: SiteReview[];
  taxes: TaxProfile[];
  finances: FinanceProfile[];
  rules: FbaRateRule[];
  sizeRules?: SmallSizeRules;
  problems: string[];
}
export interface OfficialFbaQuoteInput {
  fee: number;
  channel: "" | "Revenue Calculator" | "Fee Preview";
  evidence: string;
}
export class ApprovalOverrideRequiredError extends Error {
  constructor(readonly reasons: string[]) {
    super(`以下指标需主管确认例外通过：${reasons.join("；")}`);
  }
}
export class SubmissionPendingError extends Error {}

function text(value: IOpenCellValue): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) return value.map((part) => text(part as IOpenCellValue)).join("").trim();
  if (value && typeof value === "object") {
    if ("text" in value && typeof value.text === "string") return value.text.trim();
    if ("name" in value && typeof value.name === "string") return value.name.trim();
  }
  return "";
}
export function fullText(value: IOpenCellValue): string {
  if (Array.isArray(value)) return value.map((part) =>
    part && typeof part === "object" && "text" in part && typeof part.text === "string"
      ? part.text : text(part as IOpenCellValue)).join("").trim();
  return text(value);
}
function num(value: IOpenCellValue): number { return parseFormulaNumber(value) ?? 0; }
function bool(value: IOpenCellValue): boolean { return value === true; }
function userId(value: IOpenCellValue): string {
  if (!Array.isArray(value)) return "";
  const first = value[0];
  return first && typeof first === "object" && "id" in first && typeof first.id === "string"
    ? first.id : "";
}
function timestamp(value: IOpenCellValue): number {
  if (typeof value === "number") return value;
  return Date.parse(text(value)) || 0;
}
function cell(record: IRecord, fields: Fields, name: string): IOpenCellValue {
  const id = fields.get(name);
  return id ? record.fields[id] ?? null : null;
}
function required(fields: Fields, name: string): string {
  const id = fields.get(name);
  if (!id) throw new Error(`多站点表缺少字段：${name}`);
  return id;
}
function textCell(value: string): IOpenCellValue {
  return value ? [{ type: IOpenSegmentType.Text, text: value }] : null;
}
async function selectCell(table: ITable, fields: Fields, name: string, choice: string): Promise<IOpenCellValue> {
  const field = await table.getFieldById<ISingleSelectField>(required(fields, name));
  const option = (await field.getOptions()).find((item) => item.name === choice);
  if (!option) throw new Error(`${name}缺少选项${choice}`);
  return { id: option.id, text: option.name };
}
async function table(name: string): Promise<ITable> { return bitable.base.getTableByName(name); }
async function fields(table: ITable): Promise<Fields> {
  return new Map((await table.getFieldMetaList()).map((field) => [field.name, field.id]));
}
async function all(table: ITable): Promise<IRecord[]> {
  const rows: IRecord[] = [];
  let pageToken: string | undefined;
  const seen = new Set<string>();
  do {
    const page = await table.getRecords({ pageSize: 200, pageToken });
    rows.push(...page.records);
    if (!page.hasMore) return rows;
    if (!page.pageToken || seen.has(page.pageToken)) throw new Error("多站点记录分页异常");
    seen.add(page.pageToken);
    pageToken = page.pageToken;
  } while (true);
}
function site(value: string): Marketplace | null {
  return Object.hasOwn(MARKETPLACES, value) ? value as Marketplace : null;
}
function taxClass(value: string): TaxClass {
  return value === "低税率" || value === "零税率" || value === "免税" ? value : "标准";
}
function nullableDate(value: IOpenCellValue): number | null {
  return timestamp(value) || null;
}
function parseTax(row: IRecord, f: Fields): TaxProfile | null {
  const market = site(text(cell(row, f, "站点")));
  if (!market) return null;
  return {
    site: market, assumptionNote: text(cell(row, f, "测算口径说明")),
    roughConfigured: cell(row, f, "销售税率") !== null &&
      cell(row, f, "标价含税") !== null &&
      cell(row, f, "平台所得税预提率") !== null,
    region: text(cell(row, f, "适用地区")),
    taxClass: taxClass(text(cell(row, f, "商品税类"))),
    currency: text(cell(row, f, "币种")),
    salesTaxRate: num(cell(row, f, "销售税率")),
    priceIncludesTax: bool(cell(row, f, "标价含税")),
    collector: text(cell(row, f, "销售税收缴方")) === "卖家申报" ? "卖家申报" : "亚马逊代收",
    sellerIsImporter: bool(cell(row, f, "中国公司为进口商")),
    registrationStatus: text(cell(row, f, "税务登记状态")) === "已登记" ? "已登记"
      : text(cell(row, f, "税务登记状态")) === "未登记" ? "未登记" : "待核验",
    registrationEvidence: text(cell(row, f, "税务登记依据")),
    importerDocumentEvidence: text(cell(row, f, "进口商单证依据")),
    importTaxRecoverable: bool(cell(row, f, "进口税可抵扣")),
    importRecoverabilityEvidence: text(cell(row, f, "进口税抵扣结论依据")),
    feeTaxRecoverable: bool(cell(row, f, "平台收费税可抵扣")),
    feeRecoverabilityEvidence: text(cell(row, f, "平台收费税抵扣结论依据")),
    importDutyRuleEvidence: text(cell(row, f, "进口税及关税规则依据")),
    feeTaxRate: num(cell(row, f, "平台收费税率")),
    incomeTaxWithholdingRate: num(cell(row, f, "平台所得税预提率")),
    incomeTaxWithholdingRecoverable: bool(cell(row, f, "预提税可回收")),
    incomeTaxWithholdingRuleEvidence: text(cell(row, f, "所得税预提规则依据")),
    incomeTaxWithholdingRecoveryEvidence: text(cell(row, f, "预提税回收结论依据")),
    importChargesIncludedInHeadhaul: true,
    approved: bool(cell(row, f, "财务已确认")),
    effectiveFrom: timestamp(cell(row, f, "生效日期")),
    effectiveUntil: nullableDate(cell(row, f, "停用日期")),
    version: text(cell(row, f, "规则版本")),
    source: text(cell(row, f, "官方来源")),
    approvalEvidence: text(cell(row, f, "财务确认依据")),
  };
}
function parseFinance(row: IRecord, f: Fields): FinanceProfile | null {
  const market = site(text(cell(row, f, "站点")));
  if (!market) return null;
  return {
    site: market, category: text(cell(row, f, "适用品类")),
    currency: text(cell(row, f, "币种")),
    roughConfigured: cell(row, f, "汇率(CNY/本币)") !== null &&
      cell(row, f, "平台佣金率") !== null &&
      ["净收入", "买家标价"].includes(text(cell(row, f, "佣金计费基数"))),
    cnyPerCurrency: num(cell(row, f, "汇率(CNY/本币)")),
    headhaulCnyPerKg: num(cell(row, f, "头程单价(¥/kg)")),
    referralRate: num(cell(row, f, "平台佣金率")),
    referralBasis: text(cell(row, f, "佣金计费基数")) === "买家标价" ? "买家标价" : "净收入",
    referralFeeIncludesTax: bool(cell(row, f, "佣金含税")),
    adRate: num(cell(row, f, "广告费率")),
    returnLossRate: num(cell(row, f, "退货损失率")),
    storageLossRate: num(cell(row, f, "仓储损失率")),
    promotionRate: num(cell(row, f, "促销费率")),
    settlementLossRate: num(cell(row, f, "汇兑损耗率")),
    targetContributionRate: num(cell(row, f, "目标毛利率")),
    volumeDivisorCm3PerKg: num(cell(row, f, "体积重除数(cm³/kg)")),
    approved: bool(cell(row, f, "财务已确认")),
    effectiveFrom: timestamp(cell(row, f, "生效日期")),
    effectiveUntil: nullableDate(cell(row, f, "停用日期")),
    version: text(cell(row, f, "规则版本")), source: text(cell(row, f, "确认来源")),
  };
}
function parseCard(row: IRecord, f: Fields, market: Marketplace): FbaCard {
  const method = text(cell(row, f, "计费重方式"));
  return {
    key: text(cell(row, f, "档位键")), site: market,
    plan: text(cell(row, f, "方案")), tier: text(cell(row, f, "尺寸分段")),
    category: text(cell(row, f, "适用品类")),
    priceMin: num(cell(row, f, "标价下限(本币)")),
    priceMax: cell(row, f, "标价上限(本币)") === null ? null
      : num(cell(row, f, "标价上限(本币)")),
    maxDimsCm: [num(cell(row, f, "最长边上限(cm)")),
      num(cell(row, f, "中间边上限(cm)")), num(cell(row, f, "最短边上限(cm)"))],
    maxUnitWeightG: num(cell(row, f, "实重上限(g)")),
    shippingMethod: method === "实重" ? "unit"
      : method === "实重与体积重取大" ? "max_dimensional" : "manual_shipping_weight",
    volumeDivisor: num(cell(row, f, "体积重除数(cm³/kg)")),
    shippingWeightStepG: market === "AU" ? 1 : undefined,
    weightMinG: num(cell(row, f, "计费重下限(g)")),
    weightMaxG: num(cell(row, f, "计费重上限(g)")),
    baseFee: num(cell(row, f, "配送费(本币)")),
    surchargeRate: num(cell(row, f, "FBA附加费率")),
    includesSurcharge: bool(cell(row, f, "报价已含附加费")),
    feeIncludesTax: bool(cell(row, f, "配送费含税")),
    effectiveFrom: timestamp(cell(row, f, "生效日期")),
    effectiveUntil: nullableDate(cell(row, f, "停用日期")),
    version: text(cell(row, f, "费率版本")), source: text(cell(row, f, "官方来源")),
    verification: text(cell(row, f, "核对状态")),
    priority: num(cell(row, f, "尺寸优先级")),
  };
}

export async function loadFbaRateCards(): Promise<FbaCard[]> {
  const names = { MX: "墨西哥FBA费率表", UK: "英国FBA费率表",
    US: "美国FBA费率表", AU: "澳大利亚FBA费率表", AE: "阿联酋FBA费率表",
    SA: "沙特FBA费率表", JP: "日本FBA费率表" } as const;
  const groups = await Promise.all((Object.entries(names) as Array<[Marketplace, string]>)
    .map(async ([market, name]) => {
      try {
        const source = await table(name);
        const [meta, rows] = await Promise.all([fields(source), all(source)]);
        return rows.map((row) => parseCard(row, meta, market));
      } catch (error) {
        // New reference tables may be unavailable during rollout or by role.
        // Keep existing sites available and retain manual official quotes.
        if (["AE", "SA", "JP"].includes(market)) return [];
        throw error;
      }
    }));
  const canada = await table("加拿大FBA费率表");
  const [canadaFields, canadaRows] = await Promise.all([
    fields(canada), all(canada),
  ]);
  const configNames = ["FBA附加费率", "低价门槛(本币)", "低价折扣(本币)", "旺季配送费(CAD)"];
  for (const name of configNames) required(canadaFields, name);
  const caCards: FbaCard[] = canadaRows.flatMap((row) => {
    if (configNames.some((name) => cell(row, canadaFields, name) === null))
      throw new Error("加拿大FBA费率表的附加费、低价或旺季配置未完整填写");
    const tier = text(cell(row, canadaFields, "尺寸分段"));
    const envelope = tier === "信封Envelope";
    return canadaFbaCards({
      key: row.recordId, site: "CA", plan: "常规", tier,
      category: "普通商品", priceMin: 0, priceMax: null,
      maxDimsCm: envelope ? [38, 27, 2] : [45, 35, 20],
      maxUnitWeightG: envelope ? 500 : 9000,
      shippingMethod: "max_dimensional", volumeDivisor: 5000,
      weightMinG: num(cell(row, canadaFields, "重量下限(g)")),
      weightMaxG: num(cell(row, canadaFields, "重量上限(g)")),
      baseFee: num(cell(row, canadaFields, "配送费(CAD)")),
      surchargeRate: num(cell(row, canadaFields, "FBA附加费率")),
      includesSurcharge: false, feeIncludesTax: false,
      effectiveFrom: 0, effectiveUntil: null,
      version: text(cell(row, canadaFields, "费率版本")),
      source: text(cell(row, canadaFields, "费率来源")),
      verification: "加拿大分档参考", priority: envelope ? 1 : 2,
      caLowPriceThreshold: num(cell(row, canadaFields, "低价门槛(本币)")),
      caLowPriceDiscount: num(cell(row, canadaFields, "低价折扣(本币)")),
    }, num(cell(row, canadaFields, "旺季配送费(CAD)")));
    });
  return [...caCards, ...groups.flat()];
}
function parseReview(row: IRecord, f: Fields, memberNames = new Map<string, string>()): SiteReview | null {
  if (!text(cell(row, f, "币种"))) return null;
  const market = site(text(cell(row, f, "目标站点")));
  if (!market) return null;
  const risk = (name: string) => {
    const value = text(cell(row, f, name));
    return value === "无风险" || value === "有风险" || value === "待确认" ? value : "未排查";
  };
  const draft: SiteDraft = {
    entitySku: text(cell(row, f, "选品SKU")), site: market,
    expectedSaleDate: timestamp(cell(row, f, "预计开售日期"))
      ? new Date(timestamp(cell(row, f, "预计开售日期"))).toISOString().slice(0, 10) : "",
    region: text(cell(row, f, "适用地区")), taxClass: taxClass(text(cell(row, f, "商品税类"))),
    productName: text(cell(row, f, "中文品名")),
    productNameEn: text(cell(row, f, "英文品名")),
    category: text(cell(row, f, "品类")), sourceUrl: text(cell(row, f, "1688链接")),
    referenceAsins: text(cell(row, f, "参考竞品ASIN")),
    supplierName: text(cell(row, f, "供应商名称")),
    moq: cell(row, f, "MOQ") === null ? null : num(cell(row, f, "MOQ")),
    supplierCooperation: text(cell(row, f, "供应商配合度")),
    sampleAndLeadTimeNotes: text(cell(row, f, "打样及交期说明")),
    tieredQuotationNotes: text(cell(row, f, "阶梯报价说明")),
    notes: text(cell(row, f, "备注")),
    selectionRationale: normalizeSelectionRationale(fullText(cell(row, f, "选品说明"))),
    customsCode: text(cell(row, f, "海关税则号")),
    importCostEvidence: text(cell(row, f, "进口税费依据")),
    listedPrice: num(cell(row, f, "目标标价(本币)")),
    fbaFeeLocal: num(cell(row, f, "FBA费用(本币)")),
    purchaseCny: num(cell(row, f, "采购单价(¥)")),
    processingCny: num(cell(row, f, "包装加工费(¥/件)")),
    lengthCm: num(cell(row, f, "包装长(cm)")),
    widthCm: num(cell(row, f, "包装宽(cm)")),
    heightCm: num(cell(row, f, "包装高(cm)")),
    unitWeightG: num(cell(row, f, "包装重量(g)")),
    importDutyLocal: num(cell(row, f, "进口关税(本币)")),
    importTaxLocal: num(cell(row, f, "进口税总额(本币)")),
    trademarkRisk: risk("商标侵权排查"), patentRisk: risk("专利侵权排查"),
    copyrightRisk: risk("版权侵权排查"),
    complianceNotes: text(cell(row, f, "侵权排查说明")),
  };
  const status = text(cell(row, f, "审核状态"));
  const formula = (name: string) => parseFormulaNumber(cell(row, f, name));
  const contribution = formula("利润/件(本币)");
  const rate = formula("毛利率");
  const roi = formula("前置资金ROI");
  const netRevenue = formula("销售净收入(本币)");
  const losses = ["广告费率", "退货损失率", "仓储损失率", "促销费率", "汇兑损耗率"];
  const marketingAndOther = netRevenue === null || losses.some((name) => cell(row, f, name) === null)
    ? null : netRevenue * losses.reduce((sum, name) => sum + num(cell(row, f, name)), 0);
  const commissionBase = cell(row, f, "佣金基数(本币)");
  const commissionRate = cell(row, f, "平台佣金率");
  const referralCharged = commissionBase === null || commissionRate === null
    ? null : num(commissionBase) * num(commissionRate);
  const payoutRate = estimatedPayoutRate({ listedPrice: draft.listedPrice,
    netRevenue, fbaCharged: draft.fbaFeeLocal > 0 ? draft.fbaFeeLocal : null,
    referralCharged, withholdingCharged: formula("平台所得税预提额(本币)"),
    salesDeductions: marketingAndOther });
  return {
    id: row.recordId,
    number: draft.entitySku || text(cell(row, f, "记录序号")) || row.recordId,
    draft,
    status: status === "待审核" || status === "审核通过" || status === "打回修改" || status === "拒绝"
      ? status : "草稿",
    validation: text(cell(row, f, "提交校验状态")),
    submitterId: text(cell(row, f, "提交运营ID")),
    submitterName: text(cell(row, f, "提交运营")) ||
      memberNames.get(userId(cell(row, f, "提交运营"))) || "",
    group: text(cell(row, f, "审批小组")) || text(cell(row, f, "运营小组")),
    submittedAt: timestamp(cell(row, f, "最近提交审核时间")),
    submissionId: text(cell(row, f, "插件提交ID")),
    detailMetadata: {
      inputs: Object.fromEntries(["目标标价(本币)", "采购单价(¥)", "包装加工费(¥/件)",
        "FBA费用(本币)", "包装长(cm)", "包装宽(cm)", "包装高(cm)", "包装重量(g)"]
        .map(name => [name, formula(name)])),
      exchangeRate: formula("汇率(CNY/本币)"), shippingWeightG: formula("计费重量(g)"),
      taxVersion: text(cell(row, f, "税务规则版本")), financeVersion: text(cell(row, f, "财务参数版本")),
      reviewedAt: timestamp(cell(row, f, "最近主管审核时间")),
    },
    reviewCount: num(cell(row, f, "复审次数")),
    managerComment: fullText(cell(row, f, "主管审核意见")),
    reviewPath: text(cell(row, f, "评审路径")),
    hasImage: Array.isArray(cell(row, f, "产品图片")) && (cell(row, f, "产品图片") as unknown[]).length > 0,
    imageToken: firstAttachmentToken(cell(row, f, "产品图片")),
    calculation: null,
    profit: contribution === null || rate === null || roi === null ? null : {
      contribution, rate, roi,
      netRevenue,
      acquisition: formula("采购包装成本(本币)"),
      headhaul: formula("头程成本(本币)"),
      referral: formula("平台佣金经济成本(本币)"),
      salesTax: formula("标价内销售税(本币)"),
      platformFeeTax: formula("不可抵扣平台收费税(本币)"),
      fbaEconomic: formula("FBA经济成本(本币)"),
      marketingAndOther,
      withholding: formula("不可回收所得税预提(本币)"),
      payoutRate,
    },
    officialFbaFee: num(cell(row, f, "FBA官方报价(本币)")),
    officialFbaSource: text(cell(row, f, "FBA报价来源")),
    officialFbaEvidence: text(cell(row, f, "FBA报价凭证")),
  };
}
function chooseTax(rows: TaxProfile[], draft: SiteDraft, now: number): TaxProfile | null {
  return rows.filter((row) => row.site === draft.site && row.effectiveFrom <= now &&
    (row.effectiveUntil === null || row.effectiveUntil > now))
    .sort((a, b) => b.effectiveFrom - a.effectiveFrom)[0] ?? null;
}
function chooseFinance(rows: FinanceProfile[], draft: SiteDraft, now: number): FinanceProfile | null {
  return rows.filter((row) => row.site === draft.site &&
    [draft.category, "通用", "全站", "待确认"].includes(row.category) && row.approved &&
    row.effectiveFrom <= now && (row.effectiveUntil === null || row.effectiveUntil > now))
    .sort((a, b) => Number(b.category === draft.category) - Number(a.category === draft.category) ||
      b.effectiveFrom - a.effectiveFrom)[0] ?? null;
}

async function loadMemberNames(): Promise<Map<string, string>> {
  const memberTable = await table("插件人员权限配置");
  const memberFields = await fields(memberTable);
  const names = new Map<string, string>();
  for (const row of await all(memberTable)) {
    const member = cell(row, memberFields, "成员");
    const id = userId(member);
    const name = text(member) || text(cell(row, memberFields, "配置名称")).split("（")[0].trim();
    if (id && name) names.set(id, name);
  }
  return names;
}

export async function loadSiteDashboard(): Promise<SiteDashboard> {
  const names = ["SKU选品库", "站点税务配置", "站点财务参数"];
  const missing: string[] = [];
  const tables = await Promise.all(names.map(async (name) => {
    try { return await table(name); } catch { missing.push(name); return null; }
  }));
  if (missing.length) return { reviews: [], taxes: [], finances: [], rules: [], sizeRules: {},
    problems: [`缺少数据表：${missing.join("、")}`] };
  const [reviewTable, taxTable, financeTable] = tables as ITable[];
  const [reviewFields, taxFields, financeFields] = await Promise.all(
    [reviewTable, taxTable, financeTable].map(fields));
  if (!reviewFields.has("选品说明")) missing.push("选品说明字段缺失或不可读取，请刷新页面或联系管理员检查字段权限");
  const [reviewRows, taxRows, financeRows] = await Promise.all(
    [reviewTable, taxTable, financeTable].map(all));
  let memberNames = new Map<string, string>();
  try {
    memberNames = await loadMemberNames();
  } catch { /* The review remains usable if member directory access is unavailable. */ }
  const sizeRules: SmallSizeRules = {};
  try {
    const sizeTable = await table("FBA尺寸档规则");
    const sizeFields = await fields(sizeTable);
    const ordered = (await all(sizeTable)).filter((row) => bool(cell(row, sizeFields, "启用")))
      .sort((a, b) => num(cell(a, sizeFields, "排序")) - num(cell(b, sizeFields, "排序")));
    for (const row of ordered) {
      const site = text(cell(row, sizeFields, "站点")) as Marketplace;
      if (!MARKETPLACES[site]) continue;
      const dimensions = ["最长边上限(cm)", "中间边上限(cm)", "最短边上限(cm)"]
        .map((name) => num(cell(row, sizeFields, name))) as [number, number, number];
      if (dimensions.some((value) => value <= 0)) continue;
      const weight = cell(row, sizeFields, "实重上限(g)");
      const rules = sizeRules[site] ?? { source: text(cell(row, sizeFields, "规则来源")), tiers: [] };
      rules.tiers.push({ name: text(cell(row, sizeFields, "档位名称")), dimensions,
        weightG: weight === null ? null : num(weight) });
      sizeRules[site] = rules;
    }
    for (const site of Object.keys(MARKETPLACES) as Marketplace[])
      if (!sizeRules[site]?.tiers.length) throw new Error(`${site} 规则为空`);
  } catch (error) {
    missing.push(`FBA尺寸档规则：${error instanceof Error ? error.message : String(error)}`);
  }
  return {
    reviews: reviewRows.map((row) => parseReview(row, reviewFields, memberNames)).filter((v): v is SiteReview => Boolean(v)),
    taxes: taxRows.map((row) => parseTax(row, taxFields)).filter((v): v is TaxProfile => Boolean(v)),
    finances: financeRows.map((row) => parseFinance(row, financeFields)).filter((v): v is FinanceProfile => Boolean(v)),
    rules: [],
    sizeRules,
    problems: missing,
  };
}

let siteImageFieldPromise: Promise<{ target: ITable; fieldId: string }> | null = null;
export async function getSiteImageUrl(recordId: string, token: string): Promise<string | null> {
  if (!token) return null;
  try {
    siteImageFieldPromise ??= (async () => {
      const target = await table("SKU选品库");
      return { target, fieldId: required(await fields(target), "产品图片") };
    })();
    const { target, fieldId } = await siteImageFieldPromise;
    return await target.getAttachmentUrl(token, fieldId, recordId);
  } catch { siteImageFieldPromise = null; return null; }
}

export async function openSiteRecord(recordId: string): Promise<void> {
  const target = await table("SKU选品库");
  await bitable.ui.showRecordDetailDialog({ tableId: target.id, recordId });
}

async function draftValues(target: ITable, f: Fields, draft: SiteDraft): Promise<Record<string, IOpenCellValue>> {
  const values: Record<string, IOpenCellValue> = {};
  const put = (name: string, value: IOpenCellValue) => { values[required(f, name)] = value; };
  put("选品SKU", textCell(draft.entitySku.trim()));
  put("预计开售日期", Number.isFinite(forecastTimestamp(draft.expectedSaleDate))
    ? forecastTimestamp(draft.expectedSaleDate) : null);
  put("目标站点", await selectCell(target, f, "目标站点", draft.site));
  put("适用地区", textCell(draft.region));
  put("商品税类", await selectCell(target, f, "商品税类", draft.taxClass));
  put("币种", textCell(MARKETPLACES[draft.site].currency));
  for (const [field, value] of Object.entries({
    "中文品名": draft.productName, "品类": draft.category,
    "英文品名": draft.productNameEn,
    "参考竞品ASIN": draft.referenceAsins,
    "1688链接": draft.sourceUrl,
    "供应商名称": draft.supplierName,
    "打样及交期说明": draft.sampleAndLeadTimeNotes,
    "阶梯报价说明": draft.tieredQuotationNotes,
    "备注": draft.notes,
    "选品说明": normalizeSelectionRationale(draft.selectionRationale),
    "侵权排查说明": draft.complianceNotes,
  })) put(field, textCell(value));
  put("MOQ", draft.moq);
  put("供应商配合度", draft.supplierCooperation
    ? await selectCell(target, f, "供应商配合度", draft.supplierCooperation) : null);
  for (const [field, value] of Object.entries({
    "目标标价(本币)": draft.listedPrice, "FBA费用(本币)": draft.fbaFeeLocal,
    "采购单价(¥)": draft.purchaseCny,
    "包装加工费(¥/件)": draft.processingCny, "包装长(cm)": draft.lengthCm,
    "包装宽(cm)": draft.widthCm, "包装高(cm)": draft.heightCm,
    "包装重量(g)": draft.unitWeightG,
  })) put(field, value);
  for (const [field, value] of Object.entries({
    "商标侵权排查": draft.trademarkRisk, "专利侵权排查": draft.patentRisk,
    "版权侵权排查": draft.copyrightRisk,
  })) put(field, await selectCell(target, f, field, value));
  return values;
}

export async function saveSiteDraft(
  draft: SiteDraft, access: UserAccess, existingId: string | null, imageFile?: File | null,
): Promise<string> {
  if (!access.canSubmit || !access.submitGroup) throw new Error("当前账号没有提交选品评审的运营小组权限");
  const target = await table("SKU选品库");
  const f = await fields(target);
  const rows = await all(target);
  const existingRow = existingId ? rows.find((item) => item.recordId === existingId) : undefined;
  if (existingId) {
    const review = existingRow && parseReview(existingRow, f);
    if (!review || review.submitterId !== access.userId ||
        !["草稿", "打回修改"].includes(review.status))
      throw new Error("仅可修改本人草稿或打回记录");
  }
  const rationaleProblem = selectionRationaleProblem(draft.selectionRationale, false);
  if (rationaleProblem) throw new Error(rationaleProblem);
  const values = await draftValues(target, f, draft);
  if (existingId) {
    await target.setRecord(existingId, { fields: values });
  } else {
    const submitterName = (await loadMemberNames()).get(access.userId);
    if (!submitterName) throw new Error("未读取到当前运营姓名，请刷新后重试或联系管理员检查人员配置");
    values[required(f, "审核状态")] = await selectCell(target, f, "审核状态", "草稿");
    values[required(f, "提交校验状态")] = await selectCell(target, f, "提交校验状态", "未提交");
    values[required(f, "提交运营ID")] = textCell(access.userId);
    values[required(f, "提交运营")] = [{ id: access.userId, name: submitterName }];
    values[required(f, "运营小组")] = await selectCell(target, f, "运营小组", access.submitGroup);
    values[required(f, "审批小组")] = await selectCell(target, f, "审批小组", access.submitGroup);
    existingId = await target.addRecord({ fields: values });
  }
  let attachment: IOpenCellValue = existingRow ? cell(existingRow, f, "产品图片") : null;
  if (imageFile) {
    const [token] = await bitable.base.batchUploadFile([imageFile]);
    if (!token) throw new Error("图片上传失败，草稿已保存，请重试上传");
    attachment = [{
      name: imageFile.name, size: imageFile.size,
      type: imageFile.type || "application/octet-stream",
      token, timeStamp: Date.now(),
    }];
    await target.setCellValue(required(f, "产品图片"), existingId, attachment);
  }
  let persisted = false;
  for (let attempt = 0; attempt < 10; attempt++) {
    const row = (await all(target)).find((item) => item.recordId === existingId);
    const loaded = row && parseReview(row, f);
    if (loaded && loaded.draft.entitySku === draft.entitySku.trim() &&
        loaded.draft.productName === draft.productName.trim() &&
        loaded.draft.selectionRationale === normalizeSelectionRationale(draft.selectionRationale) &&
        loaded.draft.listedPrice === draft.listedPrice &&
        loaded.draft.fbaFeeLocal === draft.fbaFeeLocal &&
        loaded.draft.purchaseCny === draft.purchaseCny &&
        loaded.draft.lengthCm === draft.lengthCm &&
        loaded.draft.widthCm === draft.widthCm && loaded.draft.heightCm === draft.heightCm &&
        loaded.draft.unitWeightG === draft.unitWeightG &&
        (!imageFile || loaded.hasImage)) { persisted = true; break; }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  if (!persisted) throw new Error("草稿已写入，但回读尚未确认；请刷新后核对再提交");
  return existingId;
}

export async function withdrawSiteReview(id: string, access: UserAccess, submittedAt?: number): Promise<void> {
  if (!access.canSubmit) throw new Error("无编辑权限");
  const target = await table("SKU选品库");
  const f = await fields(target);
  const row = (await all(target)).find((item) => item.recordId === id);
  const review = row && parseReview(row, f);
  if (!review || review.submitterId !== access.userId || review.status !== "待审核" ||
      review.validation !== "通过") throw new Error("仅可撤回本人待审核记录");
  const { requestWithdrawal } = await import("./withdrawal");
  await requestWithdrawal(target, f, id, { userId: access.userId, submittedAt });
}

interface FormulaDifference { name: string; actual: number | null; expected: number }
function formulaDifferences(row: IRecord, f: Fields, result: SiteCalculation): FormulaDifference[] {
  const expected: Array<[string, number, number]> = [
    ["销售净收入(本币)", result.netRevenueLocal, 0.011],
    ["标价内销售税(本币)", result.salesTaxLocal, 0.011],
    ["结账另收销售税(本币)", result.checkoutTaxLocal, 0.011],
    ["平台所得税预提额(本币)", result.incomeTaxWithholdingLocal, 0.011],
    ["不可回收所得税预提(本币)", result.incomeTaxWithholdingNonrecoverableLocal, 0.011],
    ["利润/件(本币)", result.contributionLocal, 0.011],
    ["毛利率", result.contributionRate, 0.00011],
    ["前置资金ROI", result.upfrontCapitalRoi, 0.00011],
  ];
  return expected.flatMap(([name, amount, tolerance]) => {
    const actual = parseFormulaNumber(cell(row, f, name));
    return actual !== null && Math.abs(actual - amount) <= tolerance ? []
      : [{ name, actual, expected: amount }];
  });
}
function compareFormula(row: IRecord, f: Fields, result: SiteCalculation): boolean {
  return formulaDifferences(row, f, result).length === 0;
}

export function submissionSnapshotDifferences(row: IRecord, f: Fields,
  numbers: Record<string, number>, tax: TaxProfile, finance: FinanceProfile,
  submitId: string, expectedReviewPath = "", expectedRationale?: string): string[] {
  const differences = Object.entries(numbers).flatMap(([name, expected]) => {
    const actual = parseFormulaNumber(cell(row, f, name));
    return actual !== null && Math.abs(actual - expected) <= 0.000001 ? [] : [name];
  });
  for (const [name, expected] of [
    ["标价含税", tax.priceIncludesTax],
    ["预提税可回收", tax.incomeTaxWithholdingRecoverable],
    ["佣金含税", finance.referralFeeIncludesTax],
  ] as const) if (cell(row, f, name) !== expected) differences.push(name);
  for (const [name, expected] of [
    ["税务规则版本", tax.version],
    ["财务参数版本", finance.version],
    ["插件提交ID", submitId],
    ["销售税收缴方", tax.collector],
    ["佣金基数类型", finance.referralBasis],
  ] as const) if (text(cell(row, f, name)) !== expected) differences.push(name);
  if (expectedReviewPath && text(cell(row, f, "评审路径")) !== expectedReviewPath)
    differences.push("评审路径");
  if (expectedRationale !== undefined && normalizeSelectionRationale(fullText(cell(row, f, "选品说明"))) !== normalizeSelectionRationale(expectedRationale)) differences.push("选品说明");
  return differences;
}

export async function submitSiteReview(id: string, access: UserAccess): Promise<void> {
  if (!access.canSubmit || !access.submitGroup) throw new Error("无提交权限或运营小组未配置");
  const dashboard = await loadSiteDashboard();
  if (dashboard.problems.length) throw new Error(dashboard.problems.join("；"));
  const review = dashboard.reviews.find((item) => item.id === id);
  if (!review || review.submitterId !== access.userId || !["草稿", "打回修改"].includes(review.status))
    throw new Error("仅可提交本人草稿或打回记录");
  if (review.validation === "校验中") {
    const pendingTable = await table("SKU选品库");
    const pendingFields = await fields(pendingTable);
    const pendingRow = await pendingTable.getRecordById(id);
    const version = text(pendingRow.fields[required(pendingFields, "插件提交ID")]);
    // The system timestamp can arrive after the submission request. Keep the
    // current attempt protected while that asynchronous write is still pending.
    const attemptTime = Number(version.match(/-(\d{13})$/)?.[1]) || review.submittedAt;
    if (!attemptTime || Date.now() - attemptTime < 120_000)
      throw new SubmissionPendingError("这条记录正在等待 Base 公式回算，系统会自动完成提审；请稍后刷新，无需重复提交。");
  }
  const now = Date.now();
  const forecast = forecastTimestamp(review.draft.expectedSaleDate);
  const tax = chooseTax(dashboard.taxes, review.draft, forecast);
  const finance = chooseFinance(dashboard.finances, review.draft, forecast);
  const result = tax && finance ? calculateSite(review.draft, tax, finance, dashboard.rules, forecast) : null;
  const problems = submissionProblems(review.draft, tax, finance, result, forecast);
  const rationaleProblem = selectionRationaleProblem(review.draft.selectionRationale);
  if (rationaleProblem) problems.push(rationaleProblem);
  if (problems.length) throw new Error(problems.join("；"));
  if (!tax || !finance || !result) throw new Error("测算数据不完整");
  const target = await table("SKU选品库");
  const f = await fields(target);
  const beforeSubmit = (await all(target)).find((item) => item.recordId === id);
  if (!beforeSubmit) throw new Error("选品记录不存在，请刷新");
  const latestRow = (await all(target)).find((row) => row.recordId === id);
  if (!latestRow || parseReview(latestRow, f)?.status !== review.status)
    throw new Error("记录状态已变化，请刷新");
  const latestReview = parseReview(latestRow, f);
  if (!latestReview || latestReview.draft.selectionRationale !== review.draft.selectionRationale)
    throw new Error("选品说明在提审前发生变化，请刷新核对后重新提交");
  const images = cell(latestRow, f, "产品图片");
  if (!Array.isArray(images) || images.length === 0) throw new Error("提审前需上传本站点产品图片");
  const values: Record<string, IOpenCellValue> = {};
  const put = (name: string, value: IOpenCellValue) => { values[required(f, name)] = value; };
  const numbers: Record<string, number> = {
    "计费重量(g)": result.shippingWeightG,
    "FBA经济成本(本币)": result.fbaFeeEconomicLocal,
    "平台佣金经济成本(本币)": result.referralFeeEconomicLocal,
    "平台收费税总额(本币)": result.platformFeeTaxLocal,
    "可抵扣平台收费税(本币)": result.platformFeeTaxRecoverableLocal,
    "不可抵扣平台收费税(本币)": result.platformFeeTaxNonrecoverableLocal,
    "销售税率": tax.salesTaxRate,
    "平台所得税预提率": tax.incomeTaxWithholdingRate,
    "汇率(CNY/本币)": finance.cnyPerCurrency,
    "头程单价(¥/kg)": finance.headhaulCnyPerKg,
    "平台佣金率": finance.referralRate,
    "佣金基数(本币)": finance.referralBasis === "买家标价"
      ? review.draft.listedPrice : result.netRevenueLocal,
    "广告费率": finance.adRate, "退货损失率": finance.returnLossRate,
    "仓储损失率": finance.storageLossRate, "促销费率": finance.promotionRate,
    "汇兑损耗率": finance.settlementLossRate,
    "目标毛利率": finance.targetContributionRate,
    "插件预期贡献(本币)": result.contributionLocal,
    "插件预期毛利率": result.contributionRate,
    "插件预期ROI": result.upfrontCapitalRoi,
  };
  for (const [name, amount] of Object.entries(numbers)) put(name, amount);
  put("税务规则版本", textCell(tax.version));
  put("财务参数版本", textCell(finance.version));
  put("标价含税", tax.priceIncludesTax);
  put("预提税可回收", tax.incomeTaxWithholdingRecoverable);
  put("佣金含税", finance.referralFeeIncludesTax);
  put("销售税收缴方", await selectCell(target, f, "销售税收缴方", tax.collector));
  put("佣金基数类型", await selectCell(target, f, "佣金基数类型", finance.referralBasis));
  const submitId = rationaleSubmissionId(`${id}-${now}`);
  put("插件提交ID", textCell(submitId));
  put("复审次数", review.status === "打回修改" ? review.reviewCount + 1 : review.reviewCount);
  const selfExempt = reviewPathForSubmission(access, access.submitGroup) === "主管自提免评审";
  if (selfExempt)
    put("评审路径", await selectCell(target, f, "评审路径", "主管自提免评审"));
  await target.setRecord(id, { fields: values });
  let snapshotDifferences: string[] = [];
  for (let attempt = 0; attempt < 6; attempt++) {
    const current = await target.getRecordById(id);
    snapshotDifferences = submissionSnapshotDifferences(
      { recordId: id, fields: current.fields }, f, numbers, tax, finance, submitId,
      selfExempt ? "主管自提免评审" : "", review.draft.selectionRationale);
    if (!snapshotDifferences.length) break;
    if (attempt < 5) await new Promise((resolve) => setTimeout(resolve, 350));
  }
  if (snapshotDifferences.length) {
    await target.setRecord(id, { fields: {
      [required(f, "提交校验状态")]: await selectCell(target, f, "提交校验状态", "异常"),
    } });
    throw new Error(`提审参数回读未通过，记录已标记提交异常：${snapshotDifferences.slice(0, 5).join("、")}`);
  }
  // Only mark 校验中 after the direct input snapshot has passed readback. A Base workflow
  // completes validation when its formulas are ready, including after this page closes.
  await target.setRecord(id, { fields: {
    [required(f, "提交校验状态")]: await selectCell(target, f, "提交校验状态", "校验中"),
  } });
  const expectedStatus = selfExempt ? "审核通过" : "待审核";
  for (let attempt = 0; attempt < 30; attempt++) {
    const row = (await all(target)).find((item) => item.recordId === id);
    const validation = row ? text(cell(row, f, "提交校验状态")) : "";
    const formulaMatched = row && parseFormulaNumber(cell(row, f, "提审公式核对")) === 1;
    if (formulaMatched && validation === "校验中" && attempt >= 4)
      await target.setRecord(id, { fields: {
        [required(f, "提交校验状态")]: await selectCell(target, f, "提交校验状态", "通过"),
      } });
    const submittedAt = row ? timestamp(cell(row, f, "最近提交审核时间")) : 0;
    if (row && text(cell(row, f, "审核状态")) === expectedStatus &&
        validation === "通过" && submittedAt > 0 && submittedAt !== review.submittedAt) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new SubmissionPendingError("资料已保存，Base 公式正在回算；系统会在结果就绪后自动完成提审，无需重复提交。请稍后刷新查看状态。");
}

export async function decideSiteReview(
  id: string, action: "approve" | "return" | "reject", comment: string,
  access: UserAccess, quote?: OfficialFbaQuoteInput, allowOverride = false,
  expectedReview?: SiteReview,
): Promise<void> {
  if (!access.canReview) throw new Error("无审批权限");
  if (action !== "approve" && !comment.trim()) throw new Error("打回或拒绝必须填写意见");
  const target = await table("SKU选品库");
  const f = await fields(target);
  const row = (await all(target)).find((item) => item.recordId === id);
  if (!row) throw new Error("记录不存在");
  const review = parseReview(row, f);
  if (action === "approve" && review?.status === "审核通过" &&
      text(cell(row, f, "主管审核人ID")) === access.userId) {
    return;
  }
  if (!review || review.status !== "待审核" || review.validation !== "通过" ||
      review.submitterId === access.userId ||
      (!access.canReviewAll && !access.reviewGroups.includes(review.group as never)))
    throw new Error("该记录已变化、超出审批范围或属于本人提交");
  if (expectedReview && reviewFingerprint(review) !== reviewFingerprint(expectedReview))
    throw new Error("这条记录的提审数据已变化，请点击「重新读取」核对后再审核。已填意见和报价仍保留。");
  const contribution = parseFormulaNumber(cell(row, f, "毛利率"));
  if (action === "approve" && contribution === null)
    throw new Error("毛利率仍待计算，请运营补齐测算并重新提交");
  const overrideReasons = action === "approve" ? [
    ...(contribution !== null && contribution + 1e-9 < 0.25
      ? [`毛利率 ${(contribution * 100).toFixed(2)}% 低于 25%`] : []),
    ...([review.draft.trademarkRisk, review.draft.patentRisk, review.draft.copyrightRisk]
      .some((risk) => risk !== "无风险") ? ["侵权排查未全部标记无风险"] : []),
  ] : [];
  if (action === "approve") {
    const rationaleProblem = selectionRationaleProblem(review.draft.selectionRationale, requiresSelectionRationale(review.submissionId));
    if (rationaleProblem) throw new Error(rationaleProblem);
    if (!review.hasImage) throw new Error("产品图片已缺失，不能审核通过");
    const dashboard = await loadSiteDashboard();
    const taxVersion = text(cell(row, f, "税务规则版本"));
    const financeVersion = text(cell(row, f, "财务参数版本"));
    const tax = dashboard.taxes.find((item) => item.site === review.draft.site &&
      item.version === taxVersion);
    const finance = dashboard.finances.find((item) => item.site === review.draft.site &&
      [review.draft.category, "通用", "全站", "待确认"].includes(item.category) &&
      item.version === financeVersion && item.approved);
    const calculated = tax && finance
      ? calculateSite(review.draft, tax, finance, [],
        forecastTimestamp(review.draft.expectedSaleDate))
      : null;
    const blockingProblems = submissionProblems(review.draft, tax ?? null, finance ?? null,
      calculated, forecastTimestamp(review.draft.expectedSaleDate))
      .filter((problem) => problem !== "毛利率低于25%" &&
        problem !== "站点侵权排查尚未完成");
    if (blockingProblems.length || !calculated ||
        !compareFormula(row, f, calculated) ||
        Math.abs(num(cell(row, f, "FBA费用(本币)")) - review.draft.fbaFeeLocal) > 0.011 ||
        Math.abs(num(cell(row, f, "FBA经济成本(本币)")) - calculated.fbaFeeEconomicLocal) > 0.011 ||
        Math.abs(num(cell(row, f, "平台佣金经济成本(本币)")) - calculated.referralFeeEconomicLocal) > 0.011 ||
        Math.abs(num(cell(row, f, "平台收费税总额(本币)")) - calculated.platformFeeTaxLocal) > 0.011 ||
        Math.abs(num(cell(row, f, "可抵扣平台收费税(本币)")) - calculated.platformFeeTaxRecoverableLocal) > 0.011 ||
        Math.abs(num(cell(row, f, "不可抵扣平台收费税(本币)")) - calculated.platformFeeTaxNonrecoverableLocal) > 0.011 ||
        bool(cell(row, f, "佣金含税")) !== finance!.referralFeeIncludesTax ||
        Math.abs(num(cell(row, f, "销售税率")) - tax!.salesTaxRate) > 0.000001 ||
        Math.abs(num(cell(row, f, "汇率(CNY/本币)")) - finance!.cnyPerCurrency) > 0.000001)
      throw new Error("参数快照或Base公式与已确认规则不一致，不能审核通过");
    if (overrideReasons.length && !allowOverride)
      throw new ApprovalOverrideRequiredError(overrideReasons);
    if (overrideReasons.length && !comment.trim())
      throw new Error("例外通过必须填写主管审核意见");
  }
  const status = action === "approve" ? "审核通过" : action === "return" ? "打回修改" : "拒绝";
  const values: Record<string, IOpenCellValue> = {
    [required(f, "审核状态")]: await selectCell(target, f, "审核状态", status),
    [required(f, "主管审核人ID")]: textCell(access.userId),
    [required(f, "主管审核意见")]: textCell(comment.trim()),
  };
  // 制图初始化和重审进度由 Base 自动化维护，审批不直接重置制图状态。
  if (action === "approve" && quote) {
    if (Number.isFinite(quote.fee) && quote.fee > 0)
      values[required(f, "FBA官方报价(本币)")] = quote.fee;
    if (quote.channel) {
      values[required(f, "FBA报价来源")] = await selectCell(target, f, "FBA报价来源", quote.channel);
    }
    if (quote.evidence.trim())
      values[required(f, "FBA报价凭证")] = textCell(quote.evidence.trim());
  }
  if (expectedReview) {
    // The formula/permission checks above may await network work. Recheck just before writing.
    const latest = await target.getRecordById(id);
    const current = latest && parseReview({ ...latest, recordId: id }, f);
    if (!current || reviewFingerprint(current) !== reviewFingerprint(review))
      throw new Error("这条记录在审核期间发生变化，请重新读取后核对；本次未写入审批结果。");
  }
  await target.setRecord(id, { fields: values });
}
