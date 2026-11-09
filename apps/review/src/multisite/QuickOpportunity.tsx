import { useState } from "react";
import { currentShanghaiDate, MARKETPLACES, type FinanceProfile, type Marketplace, type TaxProfile } from "./domain";
import { calculateRoughOpportunity, type RoughInput } from "./rateCards";
import type { FbaCard } from "./rateCards";
import { useFbaReference } from "./useFbaReference";
import { estimateHeadhaul } from "./headhaul";

type NumberValue = number | null;
const format = (value: number) => value.toFixed(2);
export interface QuickCarryValues {
  listedPrice: number;
  purchaseCny: number;
  fbaFeeLocal?: number;
  lengthCm?: number;
  widthCm?: number;
  heightCm?: number;
  unitWeightG?: number;
}

export default function QuickOpportunity({ site, onCarry, tax, finance,
  purchaseCny, onPurchaseChange, disabled, sitePicker, cards }: {
  site: Marketplace;
  onCarry: (values: QuickCarryValues) => void;
  tax: TaxProfile | null;
  finance: FinanceProfile | null;
  purchaseCny: NumberValue;
  onPurchaseChange: (value: NumberValue) => void;
  disabled: boolean;
  sitePicker: React.ReactNode;
  cards: FbaCard[];
}) {
  const [expanded, setExpanded] = useState(true);
  const [price, setPrice] = useState<NumberValue>(null);
  const [lengthCm, setLengthCm] = useState<NumberValue>(null);
  const [widthCm, setWidthCm] = useState<NumberValue>(null);
  const [heightCm, setHeightCm] = useState<NumberValue>(null);
  const [weightG, setWeightG] = useState<NumberValue>(null);
  const [manualFba, setManualFba] = useState<NumberValue>(null);
  // Undefined follows configuration; null preserves an intentionally cleared input.
  const [marketingOverride, setMarketingOverride] = useState<NumberValue | undefined>(undefined);
  const [returnsOverride, setReturnsOverride] = useState<NumberValue | undefined>(undefined);
  const currency = MARKETPLACES[site].currency;
  const configuredPercent = (rate: number | undefined): NumberValue =>
    finance?.approved === true && finance.site === site && finance.currency === currency &&
    typeof rate === "number" && Number.isFinite(rate) && rate >= 0 && rate < 1
      ? Number((rate * 100).toFixed(6)) : null;
  const marketingPercent = marketingOverride === undefined
    ? configuredPercent(finance?.adRate) : marketingOverride;
  const returnsPercent = returnsOverride === undefined
    ? configuredPercent(finance?.returnLossRate) : returnsOverride;
  const taxReady = tax?.site === site && tax.currency === currency && tax.roughConfigured === true &&
    Number.isFinite(tax.salesTaxRate) && Number.isFinite(tax.incomeTaxWithholdingRate);
  const financeReady = finance?.approved === true && finance.site === site && finance.currency === currency &&
    finance.roughConfigured === true && finance.cnyPerCurrency > 0 &&
    finance.referralRate >= 0 && finance.referralRate < 1 &&
    finance.headhaulCnyPerKg > 0 && finance.volumeDivisorCm3PerKg > 0;
  const headhaul = finance ? estimateHeadhaul(lengthCm ?? 0, widthCm ?? 0,
    heightCm ?? 0, weightG ?? 0, finance.volumeDivisorCm3PerKg, finance.headhaulCnyPerKg) : null;
  const sizeReference = useFbaReference(cards, { site, category: "普通商品", price: price ?? 0,
    dimensionsCm: [lengthCm ?? 0, widthCm ?? 0, heightCm ?? 0], unitWeightG: weightG ?? 0,
    saleDate: Date.now() }, currentShanghaiDate());
  const sizeEstimate = sizeReference.estimate;
  const ready = price !== null && price > 0 && purchaseCny !== null && purchaseCny >= 0 &&
    headhaul !== null && manualFba !== null && manualFba > 0 &&
    marketingPercent !== null && marketingPercent >= 0 && returnsPercent !== null && returnsPercent >= 0 &&
    marketingPercent + returnsPercent <= 100;
  const input: RoughInput | null = taxReady && financeReady && ready ? {
    listedPrice: price, priceIncludesSalesTax: tax.priceIncludesTax,
    salesTaxRate: tax.salesTaxRate, cnyPerLocal: finance.cnyPerCurrency,
    purchaseCny, headhaulCny: headhaul!.headhaulCny, fbaFeeLocal: manualFba,
    fbaFeeIncludesTax: false, referralRate: finance.referralRate,
    referralBasis: finance.referralBasis,
    marketingLossRate: (marketingPercent + returnsPercent) / 100,
    importDutyLocal: 0, importTaxLocal: 0, platformFeeTaxRate: 0,
    incomeTaxWithholdingRate: tax.incomeTaxWithholdingRate,
    importChargesIncludedInHeadhaul: true,
  } : null;
  const result = input ? calculateRoughOpportunity(input) : null;
  const field = (label: string, value: NumberValue, change: (value: NumberValue) => void) =>
    <label>{label}<input type="number" min="0" step="any" value={value ?? ""}
      onChange={(event) => change(event.target.value === "" ? null : Number(event.target.value))} /></label>;

  return <section className={`quick-card multisite-quick ${expanded ? "" : "collapsed"}`} aria-label="快速机会粗算">
    <div className="panel-heading quick-card-head"><div className="panel-heading-title">
      <span className="panel-number">01</span><h2>快速机会粗算</h2>
      <span className="quick-tag">仅供初选 · 不保存</span>
    </div>
      <div className="multisite-head-actions">{sitePicker}
        <button className="text-button" aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}>{expanded ? "收起粗算 ↑" : "展开粗算 ↓"}</button>
      </div>
    </div>
    {expanded && <>
      <div className="quick-grid">
        {field(`售价（${currency}）`, price, setPrice)}
        {field("采购成本（¥）", purchaseCny, onPurchaseChange)}
        {field("包装长（cm）", lengthCm, setLengthCm)}
        {field("包装宽（cm）", widthCm, setWidthCm)}
        {field("包装高（cm）", heightCm, setHeightCm)}
        {field("包装重量（g）", weightG, setWeightG)}
        {field(`FBA 费用（${currency}）`, manualFba, setManualFba)}
        {field("广告营销占比（%）", marketingPercent, setMarketingOverride)}
        {field("售后退货占比（%）", returnsPercent, setReturnsOverride)}
        <div className="quick-dimension-card" role="status" aria-label="包装与配送尺寸参考"
          title="配送尺寸档只供初选参考，实际费用以逐 SKU 官方报价为准">
          <span>体积重 <b>{headhaul && financeReady ? `${format(headhaul.volumeKg)} kg` : "待填写"}</b></span>
          <span className="quick-weight-headhaul"><span>计费重 <b>{headhaul && financeReady ? `${format(headhaul.billingKg)} kg` : "待填写"}</b></span>
            <span>头程 <b>{headhaul && financeReady ? `¥${format(headhaul.headhaulCny)}` : "待填写"}</b></span></span>
          <span>FBA 档 <b>{sizeEstimate.status === "matched" ? (sizeEstimate.reference ?? sizeEstimate.card)?.tier
            : sizeReference.loading ? "预估计算中…" : sizeReference.unavailable ? "预估暂不可用" : "需逐 SKU 核对"}</b></span>
        </div>
      </div>
      {!taxReady || !financeReady
        ? <p role="status" className="alert warning">站点财务参数未启用或未配齐；暂不计算利润，仍可带入已填信息保存草稿。</p>
        : <div className="quick-strip" aria-live="polite">
          {result ? <>
            <span>广告营销 <b>{currency} {format(result.revenue * marketingPercent! / 100)}</b></span>
            <span>售后退货 <b>{currency} {format(result.revenue * returnsPercent! / 100)}</b></span>
            <span>粗算利润/件 <b>{currency} {format(result.contribution)}</b></span>
            <span>粗算利润率 <b>{format(result.contributionRate * 100)}%</b></span>
            <span>前置资金 ROI <b>{result.upfrontRoi === null ? "—" : `${format(result.upfrontRoi * 100)}%`}</b></span>
          </> : <span>填写售价、成本、包装、FBA 与损耗后显示粗算结果</span>}
          <span className="quick-verdict">仅供初选 · 不可提审</span>
        </div>}
      <div className="quick-footer"><p>头程、税率、汇率和佣金按站点配置；广告和退货占比默认读取配置，可临时调整。FBA 手填，粗算不保存；正式提审仍用站点配置。</p>
        <button className="secondary-button" disabled={disabled || price === null || price <= 0 ||
          purchaseCny === null || purchaseCny < 0}
          onClick={() => onCarry({ listedPrice: price!, purchaseCny: purchaseCny!,
            ...(manualFba !== null && manualFba > 0 ? { fbaFeeLocal: manualFba } : {}),
            ...(lengthCm !== null && lengthCm > 0 ? { lengthCm } : {}),
            ...(widthCm !== null && widthCm > 0 ? { widthCm } : {}),
            ...(heightCm !== null && heightCm > 0 ? { heightCm } : {}),
            ...(weightG !== null && weightG > 0 ? { unitWeightG: weightG } : {}),
          })}>带入完整表单</button>
      </div>
    </>}
  </section>;
}
