import { requiresSelectionRationale } from "../domain/selectionRationale";
import SelectionRationale from "../components/SelectionRationale";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import ReferenceAsins from "../components/ReferenceAsins";
import { MARKETPLACES } from "./domain";
import type { SiteReview, OfficialFbaQuoteInput } from "./repository";
import type { SmallSizeRules } from "./sizing";
import { smallSizeAdvice } from "./sizing";
import SiteReviewThumb from "./SiteReviewThumb";
import { reviewAttention, reviewDate } from "./reviewPresentation";

interface Props {
  record: SiteReview; viewerId: string; sizeRules: SmallSizeRules; comment: string;
  quote: OfficialFbaQuoteInput; busy: boolean; completed: boolean; message: string;
  previous?: SiteReview; next?: SiteReview; position: number; count: number;
  overrideReasons?: string[];
  onComment(value: string): void; onQuote(value: OfficialFbaQuoteInput): void;
  onNavigate(record: SiteReview): void; onClose(): void; onRefresh(): void; onOriginal(): void;
  onDecide(action: "approve" | "return" | "reject", override?: boolean): void;
  onCancelOverride(): void;
}
const sections = [["overview", "评审总览"], ["cost", "成本明细"], ["evidence", "供应与风险"], ["history", "审核记录"]] as const;
const text = (value: string | null | undefined) => value?.trim() || "未填写 / 不可读取";
const money = (currency: string, value: number | null | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? `${currency} ${value.toFixed(2)}` : "待计算 / 不可读取";
const pct = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value)
  ? `${(value * 100).toFixed(2)}%` : "待计算 / 不可读取";
function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}
function sourceLink(value: string | undefined): string | null {
  try { const url = new URL(value || ""); return ["https:", "http:"].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}

export default function ReviewDetail(p: Props) {
  const [section, setSection] = useState<typeof sections[number][0]>("overview");
  const [inputError, setInputError] = useState("");
  const shell = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const commentInput = useRef<HTMLTextAreaElement>(null);
  const busy = useRef(p.busy); busy.current = p.busy;
  const onClose = useRef(p.onClose); onClose.current = p.onClose;
  useEffect(() => {
    const origin = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    const app = document.getElementById("root"); const inert = app?.inert;
    document.body.style.overflow = "hidden"; if (app) app.inert = true;
    closeButton.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (document.querySelector(".review-image-popover")) return;
      if (event.key === "Escape" && !busy.current) { event.preventDefault(); onClose.current(); }
      if (event.key !== "Tab" || !shell.current) return;
      const all = Array.from(shell.current.querySelectorAll<HTMLElement>("button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary"))
        .filter(element => !element.closest("[hidden]") && !element.closest("details:not([open]) :not(summary)"));
      const first = all[0], last = all[all.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = overflow;
      if (app) app.inert = inert ?? false; if (origin?.isConnected) origin.focus(); };
  }, []);
  useEffect(() => { setSection("overview"); setInputError(""); if (content.current) content.current.scrollTop = 0; }, [p.record.id]);
  const item = p.record, draft = item.draft, currency = MARKETPLACES[draft.site].currency;
  const profit = item.profit, attention = reviewAttention(item), metadata = item.detailMetadata;
  const amount = (value: number | null | undefined) => money(currency, value);
  const readNumber = (name: string, fallback: number) => metadata ? metadata.inputs[name] : fallback;
  const number = (name: string, fallback: number, suffix: string) => {
    const value = readNumber(name, fallback); return value == null ? "未填写 / 不可读取" : `${value}${suffix}`;
  };
  const dimensions = [number("包装长(cm)", draft.lengthCm, ""), number("包装宽(cm)", draft.widthCm, ""), number("包装高(cm)", draft.heightCm, "")].join(" × ");
  const small = smallSizeAdvice(p.sizeRules, draft.site, [draft.lengthCm, draft.widthCm, draft.heightCm], draft.unitWeightG);
  const source = sourceLink(draft.sourceUrl);
  const blocked = p.busy || p.completed || item.submitterId === p.viewerId || item.status !== "待审核" || item.validation !== "通过";
  const decide = (action: "approve" | "return" | "reject") => {
    if (action !== "approve" && !p.comment.trim()) { setInputError("打回或拒绝必须填写主管审核意见"); commentInput.current?.focus(); return; }
    setInputError(""); p.onDecide(action);
  };
  const links = <div className="rd-links"><ReferenceAsins value={draft.referenceAsins} site={draft.site} />
    {source ? <a href={source} target="_blank" rel="noopener noreferrer">1688 货源 ↗</a> : <span className="rd-muted">货源链接未填写或无效</span>}</div>;
  return createPortal(<div className="review-detail" ref={shell} role="dialog" aria-modal="true" aria-labelledby="rd-title">
    <header className="rd-nav"><button ref={closeButton} type="button" className="secondary-button" disabled={p.busy} onClick={p.onClose}>← 返回列表</button>
      <strong id="rd-title">主管评审详情</strong><span className="rd-muted">{p.position} / {p.count}</span>
      <div className="rd-navigation"><button type="button" className="secondary-button" disabled={p.busy || !p.previous} onClick={() => p.previous && p.onNavigate(p.previous)}>上一条</button>
        <button type="button" className="secondary-button" disabled={p.busy || !p.next} onClick={() => p.next && p.onNavigate(p.next)}>下一条</button>
        <button type="button" className="text-button" disabled={p.busy || p.completed} onClick={p.onRefresh}>重新读取</button>
        <button type="button" className="text-button" disabled={p.busy} onClick={p.onOriginal}>打开原记录</button></div></header>
    <div className="rd-content" ref={content}>
      <div className="rd-product"><SiteReviewThumb record={item} viewerId={p.viewerId} /><div>
        <p className="rd-site">{MARKETPLACES[draft.site].label} · {currency} <span>{p.completed ? "已处理" : item.status}</span></p>
        <h2>{draft.productName || "未命名产品"}</h2><p className="rd-muted">{draft.entitySku || item.number} · {item.submitterName || "运营姓名不可读取"} · {item.group} · {draft.category}</p>
        <p className="rd-muted">{reviewDate(item.submittedAt)} 提交 · {item.reviewCount ? `复审 ${item.reviewCount} 次` : "首次评审"}</p></div></div>
      <div className="rd-metrics">
        <div className={`rd-metric rd-key ${profit && profit.rate < .25 ? "rd-low" : ""}`}><span>毛利率</span><strong>{pct(profit?.rate)}</strong><small>{profit && Number.isFinite(profit.rate)
          ? `${profit.rate >= .25 ? "高于" : "低于"}25%审核线 ${Math.abs((profit.rate - .25) * 100).toFixed(2)} 个百分点` : "待公式就绪后审核"}</small></div>
        <div className="rd-metric"><span>利润 / 件</span><strong>{amount(profit?.contribution)}</strong><small>按本次提审数据</small></div>
        <div className="rd-metric"><span>前置资金 ROI</span><strong>{pct(profit?.roi)}</strong><small>利润 ÷ 采购包装及头程</small></div>
        <div className="rd-metric"><span>预计回款占比</span><strong>{pct(profit?.payoutRate)}</strong><small>模型估算，非实际到账</small></div>
      </div>
      {attention.length > 0 && <div className="rd-attention"><strong>需关注</strong><span>{attention.join("；")}</span><button type="button" className="text-button" onClick={() => setSection("evidence")}>查看依据</button></div>}
      <nav className="rd-tabs" aria-label="评审详情分区">{sections.map(([id, label]) => <button key={id} type="button" aria-pressed={section === id} onClick={() => setSection(id)}>{label}</button>)}</nav>
      <div hidden={section !== "overview"}><SelectionRationale value={draft.selectionRationale} emptyText={requiresSelectionRationale(item.submissionId) ? "本次提审的选品说明已缺失，请打回补充。" : "历史提交未填写选品说明，可按原流程审核或打回补充。"} /></div>
      <div className="rd-columns" hidden={section !== "overview"}>
        <section><h3>售价与成本</h3><dl><Fact label="目标售价">{amount(readNumber("目标标价(本币)", draft.listedPrice))}</Fact>
          <Fact label="采购 / 包装加工">{money("¥", readNumber("采购单价(¥)", draft.purchaseCny))} / {money("¥", readNumber("包装加工费(¥/件)", draft.processingCny))}</Fact>
          <Fact label="头程 / FBA费用">{amount(profit?.headhaul)} / {amount(readNumber("FBA费用(本币)", draft.fbaFeeLocal))}</Fact>
          <Fact label="佣金 / 营销售后">{amount(profit?.referral)} / {amount(profit?.marketingAndOther)}</Fact></dl></section>
        <section><h3>实物与选品依据</h3><dl><Fact label="包装尺寸 / 实重">{dimensions} cm / {number("包装重量(g)", draft.unitWeightG, " g")}</Fact>
          <Fact label="尺寸参考">{small.result}</Fact><Fact label="商标 / 专利 / 版权">{[draft.trademarkRisk, draft.patentRisk, draft.copyrightRisk].map(value => value || "待核实").join(" / ")}（运营填报）</Fact>
          <Fact label="供应商 / MOQ">{text(draft.supplierName)} / {draft.moq == null ? "未填写 / 不可读取" : draft.moq}</Fact></dl>{links}</section>
      </div>
      <div className="rd-columns" hidden={section !== "cost"}>
        <section><h3>从售价到每件利润</h3><dl className="rd-costs"><Fact label="目标售价">{amount(readNumber("目标标价(本币)", draft.listedPrice))}</Fact>
          <Fact label="标价内销售税">{amount(profit?.salesTax)}</Fact><Fact label="净销售收入">{amount(profit?.netRevenue)}</Fact>
          <Fact label="减：采购包装成本">{amount(profit?.acquisition)}</Fact><Fact label="减：双清包税头程">{amount(profit?.headhaul)}</Fact>
          <Fact label="减：FBA经济成本">{amount(profit?.fbaEconomic)}</Fact><Fact label="减：平台佣金经济成本">{amount(profit?.referral)}</Fact>
          <Fact label="减：营销售后等预计扣款">{amount(profit?.marketingAndOther)}</Fact><Fact label="减：不可回收所得税预提">{amount(profit?.withholding)}</Fact>
          <Fact label="利润 / 件">{amount(profit?.contribution)}</Fact></dl></section>
        <section><h3>测算依据</h3><dl><Fact label="运营手填 FBA">{amount(readNumber("FBA费用(本币)", draft.fbaFeeLocal))}</Fact>
          <Fact label="汇率快照">{metadata?.exchangeRate != null ? `1 ${currency} = ¥${metadata.exchangeRate.toFixed(4)}` : "未填写 / 不可读取"}</Fact>
          <Fact label="头程计费重量">{metadata?.shippingWeightG != null ? `${metadata.shippingWeightG.toFixed(1)} g` : "待计算 / 不可读取"}</Fact>
          <Fact label="税务 / 财务版本">{text(metadata?.taxVersion)} / {text(metadata?.financeVersion)}</Fact>
          <Fact label="不可抵扣平台收费税">{amount(profit?.platformFeeTax)}（已含在相关费用中）</Fact></dl>
          <p className="rd-note">毛利率以净销售收入为分母；成本按提审快照展示。显示金额取两位小数，税费解释项不重复扣除。</p></section>
      </div>
      <div className="rd-columns" hidden={section !== "evidence"}>
        <section><h3>供应与实物</h3><dl><Fact label="供应商">{text(draft.supplierName)}</Fact><Fact label="MOQ">{draft.moq == null ? "未填写 / 不可读取" : draft.moq}</Fact>
          <Fact label="供应商配合度">{text(draft.supplierCooperation)}</Fact><Fact label="打样及交期">{text(draft.sampleAndLeadTimeNotes)}</Fact>
          <Fact label="阶梯报价">{text(draft.tieredQuotationNotes)}</Fact><Fact label="英文品名">{text(draft.productNameEn)}</Fact><Fact label="预计开售日期">{text(draft.expectedSaleDate)}</Fact></dl>{links}</section>
        <section><h3>侵权排查依据</h3><dl><Fact label="商标排查">{draft.trademarkRisk || "未排查"}</Fact><Fact label="专利排查">{draft.patentRisk || "未排查"}</Fact>
          <Fact label="版权排查">{draft.copyrightRisk || "未排查"}</Fact><Fact label="排查说明">{text(draft.complianceNotes)}</Fact><Fact label="运营备注">{text(draft.notes)}</Fact></dl>
          <p className="rd-note">以上为运营填报状态。供应信息缺项为核对提醒，沿用现有审批规则。</p></section>
      </div>
      <div className="rd-columns" hidden={section !== "history"}>
        <section><h3>本次提审</h3><dl><Fact label="提交时间">{reviewDate(item.submittedAt)}</Fact><Fact label="复审次数">{item.reviewCount}</Fact><Fact label="评审路径">{item.reviewPath || "常规审核"}</Fact></dl></section>
        <section><h3>最近审核记录</h3><dl><Fact label="最近主管审核时间">{reviewDate(metadata?.reviewedAt || 0)}</Fact><Fact label="最近主管审核意见">{item.managerComment || "暂无意见 / 不可读取"}</Fact></dl>
          <p className="rd-note">展示已有的最近审核信息；完整变更记录可从原记录查看。</p></section>
      </div>
      <details className="rd-quote" key={item.id} open={p.quote.fee > 0 || !!p.quote.channel || !!p.quote.evidence || undefined}>
        <summary>补充官方 FBA 报价 <span className="rd-muted">三项均选填</span></summary><div className="site-review-quote">
          <label>官方 FBA 报价（{currency}，选填）<input type="number" min="0" step="any" disabled={p.busy || p.completed} value={p.quote.fee || ""} onChange={event => p.onQuote({ ...p.quote, fee: Number(event.target.value) })} /></label>
          <label>报价来源（选填）<select disabled={p.busy || p.completed} value={p.quote.channel} onChange={event => p.onQuote({ ...p.quote, channel: event.target.value as OfficialFbaQuoteInput["channel"] })}><option value="">未填写</option><option>Revenue Calculator</option><option>Fee Preview</option></select></label>
          <label>官方报价凭证（选填）<input disabled={p.busy || p.completed} value={p.quote.evidence} onChange={event => p.onQuote({ ...p.quote, evidence: event.target.value })} placeholder="报表位置或核对依据" /></label>
        </div></details>
    </div>
    <footer className="rd-footer">
      {p.overrideReasons ? <div role="dialog" aria-label="例外通过确认" className="rd-override"><strong>确认例外通过</strong><p>{p.overrideReasons.join("；")}。请填写原因并确认，审核意见将留在原记录中。</p>
        <label>例外通过原因<textarea aria-label="例外通过原因" disabled={p.busy} value={p.comment} onChange={event => p.onComment(event.target.value)} /></label>
        <div className="rd-actions"><button type="button" className="secondary-button" disabled={p.busy} onClick={p.onCancelOverride}>取消</button><button type="button" className="primary-button" disabled={p.busy || !p.comment.trim()} onClick={() => p.onDecide("approve", true)}>确认例外通过</button></div></div>
        : <><label className="rd-comment">主管审核意见 <span className="rd-muted">打回 / 拒绝必填；切换记录保留当前页内输入</span>
          <textarea ref={commentInput} aria-label="主管审核意见" aria-invalid={!!inputError} rows={1} disabled={p.busy || p.completed} value={p.comment} onChange={event => { setInputError(""); p.onComment(event.target.value); }} placeholder="填写判断依据，或需运营补充的信息" /></label>
          <div className="rd-actions"><div className="rd-feedback">{inputError && <span role="alert">{inputError}</span>}{p.message && <span role="status">{p.message}</span>}{item.submitterId === p.viewerId && <span>本人提交不可自审</span>}</div>
            <button type="button" className="reject-button" disabled={blocked} onClick={() => decide("reject")}>拒绝</button><button type="button" className="return-button" disabled={blocked} onClick={() => decide("return")}>打回修改</button><button type="button" className="approve-button" disabled={blocked} onClick={() => decide("approve")}>{p.busy ? "正在处理…" : "审核通过"}</button></div></>}
      {p.overrideReasons && p.message && <p role="status" className="rd-note">{p.message}</p>}
    </footer>
  </div>, document.body);
}
