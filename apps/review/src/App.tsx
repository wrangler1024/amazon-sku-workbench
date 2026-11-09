import { rationaleSubmissionId } from "./domain/selectionRationale";
import SelectionRationale, { SelectionRationaleInput } from "./components/SelectionRationale";
import ReferenceAsins from "./components/ReferenceAsins";
import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent } from "react";
import { createPortal } from "react-dom";

import { calculateSelection } from "./domain/calculator";
import { intellectualPropertyPreview } from "./domain/riskPreview";
import { cachedImageUrl, invalidateImageUrl, resolveImageUrl } from "./domain/imageUrlCache";
import MultiSitePanel, { type LegacyQueues, type QueueEntry } from "./multisite/MultiSitePanel";
import { estimatedPayoutRate } from "./multisite/payout";
import { evaluateSubmissionGate } from "./domain/gates";
import { readSavedTheme, saveTheme, THEMES, type ThemeId } from "./domain/themes";
import { accessScopeLabel, reviewPathForSubmission } from "./domain/permissions";
import {
  calculateQuickEstimate,
  meetsQuickScreeningRate,
  quickEstimateError,
  quickInputFromRates,
  quickScreeningRate,
} from "./domain/quickEstimate";
import type {
  CalculatorInput,
  CandidateRecord,
  ComplianceStatus,
  QuickEstimateInput,
  RateSnapshot,
  ReviewAction,
  SelectionRepository,
  SelectionSnapshot,
} from "./domain/types";

type Tab = "submitted" | "returned" | "review" | "multisite";
const LEGACY_NEW_SUBMISSIONS_CLOSED = true;

const EMPTY_INPUT: CalculatorInput = {
  productNameCn: "",
  productNameEn: "",
  category: "",
  sourceUrl: "",
  referenceAsins: "",
  moq: 0,
  supplierName: "",
  supplierCooperation: "",
  sampleAndLeadTimeNotes: "",
  tieredQuotationNotes: "",
  notes: "",
  selectionRationale: "",
  purchasePriceCny: 0,
  processingCostCny: 0,
  lengthCm: 0,
  widthCm: 0,
  heightCm: 0,
  weightG: 0,
  targetPriceCad: 0,
  trademarkRisk: "未排查",
  patentRisk: "未排查",
  copyrightRisk: "未排查",
  complianceNotes: "",
};

function requestId(): string {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function money(value: number): string {
  return Number.isFinite(value) ? `CAD ${value.toFixed(2)}` : "—";
}

function PhotoPlaceholderIcon() {
  return (
    <svg className="pv-placeholder-icon" viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <path className="pv-icon-shadow" d="M10 11.5h24a4 4 0 0 1 4 4v23a3 3 0 0 1-3 3H13a3 3 0 0 1-3-3v-27Z" />
      <rect className="pv-icon-frame" x="7" y="6" width="32" height="33" rx="4" />
      <rect className="pv-icon-paper" x="10.5" y="9.5" width="25" height="26" rx="1.5" />
      <circle className="pv-icon-sun" cx="28.5" cy="16" r="2.5" />
      <path className="pv-icon-hill-back" d="m12 31 7-8 5 5 4-4 6 7" />
      <path className="pv-icon-hill-front" d="m12 32 9-10 5 5 3-3 5 8" />
    </svg>
  );
}

function cny(value: number): string {
  return Number.isFinite(value) ? `¥${value.toFixed(2)}` : "—";
}

function percent(value: number): string {
  return Number.isFinite(value) ? `${(value * 100).toFixed(2)}%` : "—";
}

function statusTone(value: number, target: number): string {
  return value >= target ? "positive" : value >= target - 0.05 ? "warning" : "negative";
}

function submissionStatus(record: CandidateRecord): string {
  if (record.validationStatus === "异常") return "提交异常";
  if (record.status) return record.status;
  if (record.validationStatus === "通过") return "校验通过，状态同步中";
  return "校验中";
}

interface AppProps {
  repository: SelectionRepository;
}

function ProductThumb({
  repository,
  record,
  viewerId,
  className,
  hoverPreview = false,
}: {
  repository: SelectionRepository;
  record: CandidateRecord;
  viewerId: string;
  className?: string;
  hoverPreview?: boolean;
}) {
  const [url, setUrl] = useState<string | null>(() =>
    cachedImageUrl(viewerId, "SKU选品库", record.recordId, record.productImageToken));
  const [failed, setFailed] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const [previewPinned, setPreviewPinned] = useState(false);
  const [previewPosition, setPreviewPosition] = useState({ left: 8, top: 8 });
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const hoverTimer = useRef<number | null>(null);
  const retried = useRef(false);

  const clearHoverTimer = () => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  const positionPreview = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const gap = 12;
    const width = Math.min(300, window.innerWidth - 16);
    const height = Math.min(308, window.innerHeight - 16);
    const left = rect.left >= width + gap + 8
      ? rect.left - width - gap
      : Math.min(rect.right + gap, window.innerWidth - width - 8);
    setPreviewPosition({
      left: Math.max(8, left),
      top: Math.max(8, Math.min(rect.top - 8, window.innerHeight - height - 8)),
    });
  }, []);
  const showPreview = () => {
    if (!url) return;
    positionPreview();
    setPreviewVisible(true);
  };
  const closePreview = () => {
    clearHoverTimer();
    setPreviewPinned(false);
    setPreviewVisible(false);
  };

  useEffect(() => {
    let alive = true;
    setUrl(cachedImageUrl(viewerId, "SKU选品库", record.recordId, record.productImageToken));
    setFailed(false);
    setPreviewVisible(false);
    setPreviewPinned(false);
    retried.current = false;
    const token = record.productImageToken;
    if (!token) {
      setFailed(true);
      return;
    }
    void resolveImageUrl(viewerId, "SKU选品库", record.recordId, token,
      () => repository.getProductImageUrl(record.recordId, token))
      .then((resolved) => {
        if (!alive) return;
        if (resolved) setUrl(resolved);
        else setFailed(true);
      })
      .catch(() => { if (alive) setFailed(true); });
    return () => {
      alive = false;
    };
  }, [repository, viewerId, record.recordId, record.productImageToken]);
  const imageFailed = () => {
    closePreview();
    const token = record.productImageToken;
    if (!token) { setFailed(true); setUrl(null); return; }
    invalidateImageUrl(viewerId, "SKU选品库", record.recordId, token);
    if (retried.current) { setFailed(true); setUrl(null); return; }
    retried.current = true;
    setUrl(null);
    void resolveImageUrl(viewerId, "SKU选品库", record.recordId, token,
      () => repository.getProductImageUrl(record.recordId, token), true)
      .then((fresh) => { if (fresh && fresh !== url) setUrl(fresh); else setFailed(true); })
      .catch(() => setFailed(true));
  };
  useEffect(() => () => clearHoverTimer(), []);
  useEffect(() => {
    if (!previewVisible) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") closePreview();
    };
    window.addEventListener("resize", positionPreview);
    window.addEventListener("scroll", positionPreview, true);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", positionPreview);
      window.removeEventListener("scroll", positionPreview, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [previewVisible, positionPreview]);
  const base = className ?? "review-thumb";
  const thumbnail = url ? (
    <img className={base} src={url} alt={record.input.productNameCn} loading="lazy" decoding="async" onError={imageFailed} />
  ) : (
    <div className={record.productImageToken ? `${base} loading` : `${base} empty`}>
      {record.productImageToken ? (failed ? "加载失败" : "加载中…") : "无图片"}
    </div>
  );
  if (!hoverPreview || !url) return thumbnail;
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="review-image-trigger"
        aria-label={`预览产品图片：${record.input.productNameCn}`}
        aria-haspopup="dialog"
        aria-expanded={previewVisible}
        title="悬停放大图片，点击固定预览"
        onPointerEnter={(event) => {
          if (event.pointerType === "touch" || previewPinned) return;
          clearHoverTimer();
          hoverTimer.current = window.setTimeout(showPreview, 150);
        }}
        onPointerLeave={() => {
          clearHoverTimer();
          if (!previewPinned) setPreviewVisible(false);
        }}
        onFocus={showPreview}
        onBlur={() => { if (!previewPinned) setPreviewVisible(false); }}
        onClick={() => {
          clearHoverTimer();
          if (previewPinned) closePreview();
          else { showPreview(); setPreviewPinned(true); }
        }}
      >
        {thumbnail}
      </button>
      {previewVisible && createPortal(
        <>
          {previewPinned && <div className="review-image-backdrop" onClick={closePreview} />}
          <div
            className={`review-image-popover${previewPinned ? " pinned" : ""}`}
            style={previewPosition}
            role="dialog"
            aria-label={`产品图片预览：${record.input.productNameCn}`}
          >
            <img src={url} alt={record.input.productNameCn} />
            <div className="review-image-caption">
              <span>{record.input.productNameCn}</span>
              {previewPinned && <button type="button" onClick={closePreview} aria-label="关闭产品图片预览">✕</button>}
            </div>
          </div>
        </>,
        document.body,
      )}
    </>
  );
}

function allNoRisk(record: CandidateRecord): boolean {
  return (
    record.input.trademarkRisk === "无风险" &&
    record.input.patentRisk === "无风险" &&
    record.input.copyrightRisk === "无风险"
  );
}

function yuan(value: number): string {
  return `¥${Number(value.toFixed(2))}`;
}

function RecordDrawer({
  repository,
  record,
  viewerId,
  onClose,
}: {
  repository: SelectionRepository;
  record: CandidateRecord;
  viewerId: string;
  onClose: () => void;
}) {
  const inp = record.input;
  const calc = record.calculation;
  const rates = record.rates;
  const purchase = inp.purchasePriceCny + inp.processingCostCny;
  return (
    <div className="overlay open" onClick={onClose}>
      <aside className="drawer open" onClick={(event) => event.stopPropagation()}>
        <div className="d-head">
          <h3>{inp.productNameCn} — 完整记录</h3>
          <span className="d-sub">提交 {record.submittedAt ? new Date(record.submittedAt).toLocaleString("zh-CN", { hour12: false }) : "—"} · 复审 {record.reviewCount} 次</span>
          <button className="d-close" onClick={onClose}>关闭 ✕</button>
        </div>
        <div className="d-body">
          <div className="d-section d-img">
            <ProductThumb repository={repository} record={record} viewerId={viewerId} className="d-thumb" />
            <div className="idblock">
              <b>{inp.productNameCn}</b><span className="cat">{inp.category || "未分类"}</span><br />
              {inp.productNameEn || "—"}<br />
              <span className="dim">{record.operatingGroup || "历史未分组"} · <b>{record.submitterName || "运营"}</b> · 加拿大</span>
            </div>
          </div>

          <div className="d-section hero-nums">
            <div className="s-title"><span className="ord">1</span>评审结论<span className="why">先看结论</span></div>
            <div className="big-nums">
              <span className={`bn ${calc.completeContributionRate + 1e-9 >= rates.targetContributionRate ? "g" : "r"}`}><b>{percent(calc.completeContributionRate)}</b><small>毛利率（线 {percent(rates.targetContributionRate)}）</small></span>
              <span className="bn"><b>{percent(calc.upfrontCapitalRoi)}</b><small>前置资金 ROI</small></span>
              <span className="bn"><b>{money(calc.estimatedProfitCad)}</b><small>初步利润/件</small></span>
              <span className="bn"><b>{money(calc.fbaFinalFeeCad)}</b><small>FBA 配送费</small></span>
            </div>
          </div>

          <div className="d-section">
            <div className="s-title"><span className="ord">2</span>合规排查（一票否决）<span className="why">结论之后先排雷</span></div>
            <div className="kv">
              <span className="k">商标</span><span className={`v ${inp.trademarkRisk === "无风险" ? "good" : "bad"}`}>{inp.trademarkRisk || "—"}</span>
              <span className="k">专利</span><span className={`v ${inp.patentRisk === "无风险" ? "good" : "bad"}`}>{inp.patentRisk || "—"}</span>
              <span className="k">版权</span><span className={`v ${inp.copyrightRisk === "无风险" ? "good" : "bad"}`}>{inp.copyrightRisk || "—"}</span>
              <span className="k">排查说明</span><span className="v full">{inp.complianceNotes || "—"}</span>
            </div>
          </div>

          <div className="d-section">
            <div className="s-title"><span className="ord">3</span>成本与测算明细<span className="why">质疑数字时下钻</span></div>
            <div className="kv">
              <span className="k">目标售价</span><span className="v">{money(inp.targetPriceCad)}</span>
              <span className="k">采购 + 包装加工</span><span className="v">{yuan(purchase)}</span>
              <span className="k">包装尺寸/重量</span><span className="v">{inp.lengthCm}×{inp.widthCm}×{inp.heightCm}cm · {inp.weightG}g</span>
              <span className="k">尺寸分段 · 计费重</span><span className="v">{calc.sizeTier} · {calc.chargeableWeightKg.toFixed(3)}kg</span>
              <span className="k">头程</span><span className="v">{money(calc.headhaulCad)}</span>
              <span className="k">FBA 基础−折扣+附加</span><span className="v">{money(calc.fbaBaseFeeCad)} − {money(calc.lowPriceDiscountCad)} + {money(calc.fbaSurchargeCad)} = {money(calc.fbaFinalFeeCad)}</span>
              <span className="k">参数快照（固化）</span><span className="v">佣金{percent(rates.referralFeeRate)} · 广告{percent(rates.adRate)} · {rates.calculationVersion || "—"}</span>
            </div>
          </div>

          <div className="d-section">
            <div className="s-title"><span className="ord">4</span>供应商与资料<span className="why">通过后采购要用</span></div>
            <div className="kv">
              <span className="k">供应商</span><span className="v">{inp.supplierName || "—"}{inp.supplierCooperation ? `（${inp.supplierCooperation}）` : ""}</span>
              <span className="k">MOQ</span><span className="v">{inp.moq || "—"}</span>
              <span className="k">1688 链接</span><span className="v full link">{inp.sourceUrl || "—"}</span>
              <span className="k">打样/交期</span><span className="v">{inp.sampleAndLeadTimeNotes || "—"}</span>
              <SelectionRationale value={inp.selectionRationale} />
              <span className="k">阶梯报价 · 备注</span><span className="v">{[inp.tieredQuotationNotes, inp.notes].filter(Boolean).join(" · ") || "—"}</span>
            </div>
          </div>

          <div className="d-section">
            <div className="s-title"><span className="ord">5</span>流程状态<span className="why">当前卡在哪一步</span></div>
            <div className="kv">
              <span className="k">提交校验</span><span className="v">{record.validationStatus || "—"}</span>
              <span className="k">审核状态</span><span className="v">{submissionStatus(record)}</span>
              <span className="k">评审路径</span><span className="v">{record.reviewPath || "主管审核"}</span>
              <span className="k">制图任务</span><span className="v">通过后自动流转</span>
            </div>
          </div>

          <div className="d-section">
            <div className="s-title"><span className="ord">6</span>审核轨迹<span className="why">留痕追责</span></div>
            <div className="kv">
              <span className="k">主管审核意见</span><span className="v">{record.managerComment || "—"}</span>
            </div>
          </div>

          <button className="text-button full" onClick={() => void repository.openRecord(record.recordId)}>在表格中打开原始记录</button>
        </div>
      </aside>
    </div>
  );
}

export default function App({ repository }: AppProps) {
  const [snapshot, setSnapshot] = useState<SelectionSnapshot | null>(null);
  const [themeChoice, setThemeChoice] = useState<{ userId: string; theme: ThemeId } | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<Tab>("multisite");
  const [siteCounts, setSiteCounts] = useState({ submitted: 0, returned: 0, review: 0 });
  const [input, setInput] = useState<CalculatorInput>(EMPTY_INPUT);
  const [formRates, setFormRates] = useState<RateSnapshot | null>(null);
  const [quickInput, setQuickInput] = useState<QuickEstimateInput>(() =>
    quickInputFromRates(null),
  );
  const [quickInitialized, setQuickInitialized] = useState(false);
  const [quickExpanded, setQuickExpanded] = useState(false);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [drawerRecord, setDrawerRecord] = useState<CandidateRecord | null>(null);
  const handleImagePaste = useCallback((event: ClipboardEvent<HTMLDivElement>) => {
    const files = Array.from(event.clipboardData?.files ?? []);
    const image = files.find((file) => file.type.startsWith("image/"));
    if (!image) return;
    event.preventDefault();
    setImageFile(image);
    setFeedback({
      ok: true,
      message: `已粘贴剪贴板图片${image.name ? `：${image.name}` : "（截图）"}，提交时将作为产品图片上传。`,
    });
  }, []);
  const imagePreviewUrl = useMemo(
    () =>
      imageFile && typeof URL !== "undefined" && typeof URL.createObjectURL === "function"
        ? URL.createObjectURL(imageFile)
        : null,
    [imageFile],
  );
  useEffect(() => {
    return () => {
      if (imagePreviewUrl) URL.revokeObjectURL(imagePreviewUrl);
    };
  }, [imagePreviewUrl]);
  const [editing, setEditing] = useState<CandidateRecord | null>(null);
  const [pendingSubmissionId, setPendingSubmissionId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null);
  const [reviewComments, setReviewComments] = useState<Record<string, string>>({});
  const [legacyOverride, setLegacyOverride] = useState<{ record: CandidateRecord; reasons: string[] } | null>(null);
  const [lastSubmissionId, setLastSubmissionId] = useState<string | null>(null);
  const [siteRefreshVersion, setSiteRefreshVersion] = useState(0);
  const fullFormRef = useRef<HTMLElement | null>(null);
  const tabInitialized = useRef(false);
  const themeUserId = snapshot?.access.userId ?? "";
  const theme = themeChoice?.userId === themeUserId ? themeChoice.theme : readSavedTheme(themeUserId);

  useEffect(() => {
    document.documentElement.dataset.amazonTheme = theme;
    return () => { delete document.documentElement.dataset.amazonTheme; };
  }, [theme]);

  const changeTheme = (nextTheme: ThemeId) => {
    setThemeChoice({ userId: themeUserId, theme: nextTheme });
    saveTheme(themeUserId, nextTheme);
  };

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const next = await repository.loadSnapshot();
      setSnapshot(next);
      setFormRates(next.rates);
      if (!tabInitialized.current) {
        setActiveTab("multisite");
        tabInitialized.current = true;
      }
    } catch (reason) {
      setFeedback({
        ok: false,
        message: reason instanceof Error ? reason.message : String(reason),
      });
    } finally {
      setLoading(false);
    }
  }, [repository]);

  const navigateTo = (tab: Tab) => {
    tabInitialized.current = true;
    setActiveTab(tab);
    setFeedback(null);
    setLastSubmissionId(null);
  };

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!snapshot || quickInitialized) return;
    setQuickInput(quickInputFromRates(snapshot.rates));
    setQuickInitialized(true);
  }, [quickInitialized, snapshot]);

  const calculation = useMemo(() => {
    if (!snapshot || !formRates) return null;
    return calculateSelection(input, formRates, snapshot.fbaTiers);
  }, [formRates, input, snapshot]);

  const hasImage = Boolean(imageFile) || Boolean(editing?.hasImage);
  const gate = useMemo(() => {
    if (!calculation || !formRates) return null;
    return evaluateSubmissionGate(input, calculation, formRates, hasImage);
  }, [calculation, formRates, hasImage, input]);
  const selfExempt = Boolean(
    snapshot?.access.submitGroup &&
    reviewPathForSubmission(snapshot.access, snapshot.access.submitGroup),
  );

  const quickError = useMemo(
    () => quickEstimateError(quickInput),
    [quickInput],
  );
  const quickResult = useMemo(
    () => calculateQuickEstimate(quickInput),
    [quickInput],
  );
  const quickTarget = useMemo(
    () => (snapshot ? quickScreeningRate(snapshot.rates) : 0),
    [snapshot],
  );
  const quickPassed = Boolean(
    quickResult && meetsQuickScreeningRate(quickResult.quickMarginRate, quickTarget),
  );

  const updateInput = <K extends keyof CalculatorInput>(
    key: K,
    value: CalculatorInput[K],
  ) => setInput((current) => ({ ...current, [key]: value }));

  const updateQuickInput = <K extends keyof QuickEstimateInput>(
    key: K,
    value: QuickEstimateInput[K],
  ) => setQuickInput((current) => ({ ...current, [key]: value }));

  const updateRate = (
    key: "cadPerCny" | "headhaulCnyPerKg" | "referralFeeRate" | "adRate",
    value: number,
  ) => setFormRates((current) => (current ? { ...current, [key]: value } : current));

  const resetForm = (rates = snapshot?.rates ?? null) => {
    setInput(EMPTY_INPUT);
    setFormRates(rates);
    setQuickInput(quickInputFromRates(rates));
    setQuickInitialized(Boolean(rates));
    setQuickExpanded(false);
    setImageFile(null);
    setEditing(null);
    setPendingSubmissionId(null);
  };

  const editReturned = (record: CandidateRecord) => {
    if (!snapshot) return;
    setInput(record.input);
    setFormRates(snapshot.rates);
    setQuickInput(quickInputFromRates(snapshot.rates));
    setQuickInitialized(true);
    setQuickExpanded(false);
    setImageFile(null);
    setEditing(record);
    setPendingSubmissionId(record.submissionId || requestId());
    navigateTo("multisite");
    setFeedback({ ok: true, message: `正在修改 ${record.input.productNameCn || "被打回记录"}` });
  };

  const withdrawPending = async (record: CandidateRecord) => {
    setBusy(true);
    try {
      const result = await repository.withdrawCandidate(record);
      setFeedback({ ok: result.ok, message: result.message });
      if (result.ok) {
        editReturned({ ...record, status: "打回修改", managerComment: "运营主动撤回修改" });
        await refresh();
      }
    } catch (reason) {
      setFeedback({ ok: false, message: reason instanceof Error ? reason.message : String(reason) });
    } finally { setBusy(false); }
  };

  const goToFullForm = (carryQuickValues: boolean) => {
    if (carryQuickValues && quickResult) {
      setInput((current) => ({
        ...current,
        targetPriceCad: quickInput.salePriceCad as number,
        purchasePriceCny: quickInput.purchaseCostCny as number,
      }));
      setQuickExpanded(false);
    }
    window.setTimeout(() => {
      fullFormRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 0);
  };

  const submit = async () => {
    if (!snapshot || !formRates || !calculation || !gate?.passed) return;
    const submissionId = pendingSubmissionId ?? rationaleSubmissionId(requestId());
    setPendingSubmissionId(submissionId);
    setBusy(true);
    setFeedback(null);
    const draft = { submissionId, input, rates: formRates, imageFile };
    try {
      const result = editing
        ? await repository.resubmitCandidate(editing, draft, calculation)
        : await repository.submitCandidate(draft, calculation);
      setFeedback({ ok: result.ok, message: result.message });
      if (result.ok) {
        resetForm(snapshot.rates);
        await refresh();
        setLastSubmissionId(result.recordId ?? null);
        setActiveTab("submitted");
      }
    } catch (reason) {
      setFeedback({ ok: false, message: reason instanceof Error ? reason.message : String(reason) });
    } finally {
      setBusy(false);
    }
  };

  const review = async (
    record: CandidateRecord,
    action: ReviewAction,
    allowOverride = false,
  ) => {
    const comment = reviewComments[record.recordId] ?? "";
    setBusy(true);
    try {
      const result = await repository.reviewCandidate(record, action, comment, allowOverride);
      setFeedback({ ok: result.ok, message: result.message });
      if (result.overrideRequired) setLegacyOverride({ record, reasons: result.overrideReasons ?? [] });
      if (result.ok) {
        setLegacyOverride(null);
        setReviewComments((current) => ({ ...current, [record.recordId]: "" }));
        await refresh();
        setActiveTab("review");
      }
    } catch (reason) {
      setFeedback({ ok: false, message: reason instanceof Error ? reason.message : String(reason) });
    } finally {
      setBusy(false);
    }
  };

  if (loading && !snapshot) {
    return <main className="loading-screen">正在连接亚马逊SKU登记表…</main>;
  }

  if (!snapshot) {
    return (
      <main className="loading-screen">
        <h1>亚马逊选品评审台</h1>
        <p>{feedback?.message ?? "无法读取多维表格"}</p>
        <button onClick={() => void refresh()}>重新连接</button>
      </main>
    );
  }

  const submittedRecords = snapshot.mySubmissions.filter((record) =>
    record.status === "待审核" && record.validationStatus === "通过");
  const legacyProfit = (record: CandidateRecord) => record.input.targetPriceCad > 0 &&
    [record.calculation.completeContributionCad, record.calculation.completeContributionRate,
      record.calculation.upfrontCapitalRoi].every(Number.isFinite);
  const legacySalesDeductions = (record: CandidateRecord) => record.input.targetPriceCad *
    (record.rates.adRate + record.rates.returnLossRate + record.rates.storageRemovalDefectRate +
      record.rates.promotionRate + record.rates.fxSettlementLossRate);
  const legacyPayoutRate = (record: CandidateRecord) => estimatedPayoutRate({
    listedPrice: record.input.targetPriceCad, netRevenue: record.input.targetPriceCad,
    fbaCharged: record.calculation.fbaFinalFeeCad,
    referralCharged: record.input.targetPriceCad * record.rates.referralFeeRate,
    withholdingCharged: 0, salesDeductions: legacySalesDeductions(record),
  });
  const profitLine = (record: CandidateRecord, review = false) => <div className="queue-profit">
    <b className={legacyProfit(record) && record.calculation.completeContributionRate < record.rates.targetContributionRate ? "below-line" : undefined}>
      毛利率 {legacyProfit(record) ? percent(record.calculation.completeContributionRate) : "待计算"}</b>
    <span>利润/件 {legacyProfit(record) ? money(record.calculation.completeContributionCad) : "待计算"}</span>
    <span>前置资金 ROI {legacyProfit(record) ? percent(record.calculation.upfrontCapitalRoi) : "待计算"}</span>
    {review && <>
      <span title="按售价减 FBA、佣金及营销售后等模型扣款后除以售价；不扣采购和头程。实际到账以亚马逊结算单为准。">回款占比 {legacyProfit(record) && legacyPayoutRate(record) !== null ? percent(legacyPayoutRate(record)!) : "待计算"}</span>
      <span>净销售 {legacyProfit(record) ? money(record.input.targetPriceCad) : "待计算"}</span>
      <span>头程 {legacyProfit(record) ? money(record.calculation.headhaulCad) : "待计算"}</span>
      <span>佣金 {legacyProfit(record) ? money(record.input.targetPriceCad * record.rates.referralFeeRate) : "待计算"}</span>
      <span>FBA费 {legacyProfit(record) ? money(record.calculation.fbaFinalFeeCad) : "待计算"}</span>
      <span>营销售后 {legacyProfit(record) ? money(legacySalesDeductions(record)) : "待计算"}</span>
    </>}
  </div>;
  const legacyQueueCard = (record: CandidateRecord, kind: "submitted" | "returned") =>
    <article className="candidate-card mine-card" key={record.recordId}><div className="cc-main">
      <div className="cc-head"><ProductThumb repository={repository} record={record} viewerId={snapshot.access.userId} hoverPreview />
        <div className="cc-ident"><p className="record-code">加拿大 · CAD<span className="cat">{record.input.category || "未分类"}</span></p>
          <h3>{record.input.productNameCn}</h3><span className="sub">{record.operatingGroup || "历史未分组"} · <b>{record.submitterName || "运营"}</b></span></div>
        <span className={`submission-status ${kind === "returned" ? "needs-action" : "pending"}`}>{submissionStatus(record)}</span></div>
      {record.managerComment && <p className="cc-comment">主管意见：{record.managerComment}</p>}
      <div className="row-mid"><span className="m">售价 <b>{money(record.input.targetPriceCad)}</b></span>
        <span className="m">采购 <b>{yuan(record.input.purchasePriceCny + record.input.processingCostCny)}</b></span>
        <span className="m">包装 <b>{record.input.lengthCm}×{record.input.widthCm}×{record.input.heightCm} cm · {record.input.weightG} g</b></span>
        {(() => { const ip = intellectualPropertyPreview(record.input); return <span className={`m ip-preview ${ip.tone}`} title={ip.title}>{ip.text}</span>; })()}
        <span className="m">{record.calculation.sizeTier}</span></div>
      {profitLine(record, true)}
    </div><div className="cc-actions">
      {record.submitterId === snapshot.access.userId && (kind === "submitted"
        ? <button className="secondary-button" disabled={busy} onClick={() => void withdrawPending(record)}>修改</button>
        : <button className="primary-button" disabled={busy} onClick={() => editReturned(record)}>修改并重提</button>)}
      <button className="text-button" onClick={() => setDrawerRecord(record)}>查看完整记录</button>
    </div></article>;
  const legacyReviewCard = (record: CandidateRecord) => {
    const ip = intellectualPropertyPreview(record.input);
    return <article className="review-card legacy-review-card" key={record.recordId}>
    <div className="card-left"><div className="row-top"><ProductThumb repository={repository} record={record} viewerId={snapshot.access.userId} hoverPreview />
      <div className="ident"><p className="record-code">加拿大 · CAD<span className="cat">{record.input.category || "未分类"}</span></p>
        <h3>{record.input.productNameCn}</h3><span className="sub">{record.operatingGroup || "历史未分组"} · <b>{record.submitterName || "运营"}</b></span></div><ReferenceAsins value={record.input.referenceAsins} site="CA" /></div>
      <div className="row-mid"><span className="m">售价 <b>{money(record.input.targetPriceCad)}</b></span>
        <span className="m">采购 <b>{yuan(record.input.purchasePriceCny + record.input.processingCostCny)}</b></span>
        <span className="m">包装 <b>{record.input.lengthCm}×{record.input.widthCm}×{record.input.heightCm} cm · {record.input.weightG} g</b></span>
        <span className={`m ip-preview ${ip.tone}`} title={ip.title}>{ip.text}</span>
        <span className="m">{record.calculation.sizeTier} · {record.calculation.chargeableWeightKg.toFixed(3)} kg</span></div>
      {profitLine(record, true)}
      <div className="row-bot"><input value={reviewComments[record.recordId] ?? ""}
        onChange={(event) => setReviewComments((current) => ({ ...current, [record.recordId]: event.target.value }))}
        placeholder="主管审核意见（打回 / 拒绝必填）" />
        <button className="link-record" onClick={() => setDrawerRecord(record)}>查看完整记录</button></div>
    </div><div className="actions">{(["approve", "return", "reject"] as const).map((action) =>
      <button key={action} className={action === "approve" ? "approve-button" : action === "return" ? "return-button" : "reject-button"}
        disabled={busy || record.submitterId === snapshot.access.userId}
        title={record.submitterId === snapshot.access.userId ? "本人提交不可自审" : undefined}
        onClick={() => void review(record, action)}>{action === "approve" ? "审核通过" : action === "return" ? "打回修改" : "拒绝"}</button>)}</div>
  </article>;
  };
  const entry = (record: CandidateRecord, node: React.ReactNode): QueueEntry => ({
    id: record.recordId, search: [record.input.productNameCn,
      record.input.referenceAsins].join(" "), node,
    submitterId: record.submitterId, submitterName: record.submitterName, site: "CA",
    listedPrice: record.input.targetPriceCad, purchaseCny: record.input.purchasePriceCny,
    contributionRate: legacyProfit(record) ? record.calculation.completeContributionRate : null,
  });
  const legacyQueues: LegacyQueues = {
    submitted: submittedRecords.map((record) => entry(record, legacyQueueCard(record, "submitted"))),
    returned: snapshot.returnedRecords.map((record) => entry(record, legacyQueueCard(record, "returned"))),
    review: snapshot.pendingReviews.map((record) => entry(record, legacyReviewCard(record))),
    excludedPendingCount: snapshot.excludedPendingCount,
  };

  return (
    <main className="app-shell" data-theme={theme}>
      <header className="hero">
        <div>
          <p className="eyebrow">AMAZON · MULTI-SITE SKU REVIEW</p>
          <h1>亚马逊选品评审台</h1>
          <p className="hero-copy">
            从机会粗选、站点录入和利润测算，到主管审核。
          </p>
        </div>
        <div className="hero-status">
          <span className={`status-dot ${snapshot.integration.ready ? "online" : "offline"}`} />
          <strong>{snapshot.integration.source === "mock" ? "本地演示 · 合成数据" : snapshot.integration.ready ? "数据已连接" : "配置未完成"}</strong>
          <small>
            {snapshot.access.role} · {accessScopeLabel(snapshot.access)} · 多站点通用评审
          </small>
          <button className="ghost-button" onClick={() => { void refresh(); setSiteRefreshVersion((value) => value + 1); }} disabled={loading}>
            {loading ? "刷新中" : "刷新数据"}
          </button>
          <div className="theme-picker" role="group" aria-label="主题色">
            <span>主题色</span>
            <div className="theme-options">
              {THEMES.map((option) => (
                <button key={option.id} type="button" className="theme-option" style={{ backgroundColor: option.swatch }}
                  title={option.label} aria-label={option.label} aria-pressed={theme === option.id}
                  onClick={() => changeTheme(option.id)} />
              ))}
            </div>
          </div>
        </div>
      </header>

      {!snapshot.integration.ready && (
        <section className="alert error">
          <strong>多维表格配置不完整，写入已关闭。</strong>
          {[...snapshot.integration.missingTables, ...snapshot.integration.missingFields].join("、")}
        </section>
      )}
      {snapshot.access.role === "未配置" && (
        <section className="alert warning">
          当前人员未配置运营角色，请由表管理员在“插件人员权限配置”中启用。
        </section>
      )}
      {snapshot.access.role !== "未配置" &&
        snapshot.access.role !== "管理员" &&
        !snapshot.access.submitGroup && (
        <section className="alert warning">
          当前人员未配置所属小组，不能提交新选品；请联系管理员在成员权限表中配置。
        </section>
      )}
      {feedback && (
        <section className={`alert ${feedback.ok ? "success" : "error"}`} role={feedback.ok ? "status" : "alert"}>
          <span>{feedback.message}</span>
          <button className="alert-close" aria-label="关闭提示" onClick={() => setFeedback(null)}>×</button>
        </section>
      )}

      <nav className="tabs" aria-label="工作区">
        <button className={activeTab === "multisite" ? "active" : ""} aria-current={activeTab === "multisite" ? "page" : undefined} onClick={() => navigateTo("multisite")}>选品录入与评审</button>
        <button className={activeTab === "submitted" ? "active" : ""} aria-current={activeTab === "submitted" ? "page" : undefined} onClick={() => navigateTo("submitted")}>
          我的提交 <span>{submittedRecords.length + siteCounts.submitted}</span>
        </button>
        <button className={activeTab === "returned" ? "active" : ""} aria-current={activeTab === "returned" ? "page" : undefined} onClick={() => navigateTo("returned")}>
          我的打回 <span className={snapshot.returnedRecords.length + siteCounts.returned > 0 ? "needs-action-badge" : undefined}>{snapshot.returnedRecords.length + siteCounts.returned}</span>
        </button>
        {snapshot.access.canReview && (
          <button className={activeTab === "review" ? "active" : ""} aria-current={activeTab === "review" ? "page" : undefined} onClick={() => navigateTo("review")}>
            主管审核 <span>{snapshot.pendingReviews.length + siteCounts.review}</span>
          </button>
        )}
      </nav>

      {activeTab === "submitted" && lastSubmissionId && <div className="submission-confirmation" role="status">
        <strong>提交已写入登记表</strong><span>状态可能稍后更新，可点击顶部「刷新数据」。</span>
        <button className="text-button" onClick={() => void repository.openRecord(lastSubmissionId)}>打开本次记录</button>
      </div>}
      <MultiSitePanel demo={new URLSearchParams(window.location.search).get("mock") === "1"} access={snapshot.access} view={activeTab} hiddenEntry={Boolean(editing)} refreshVersion={siteRefreshVersion}
        legacyQueues={legacyQueues}
        onNavigate={navigateTo} onCounts={setSiteCounts} />

      {activeTab === "multisite" && editing && (
        <div className="entry-workspace" onPaste={handleImagePaste}>
          <section className="alert warning">正在修改加拿大历史打回记录，沿用原 CAD 计算口径。完成修改后可重新提审。</section>
          <section className={`quick-card ${quickExpanded ? "" : "collapsed"}`}>
            <div className="panel-heading quick-card-head">
              <div className="panel-heading-title">
                <span className="panel-number">01</span>
                <h2>快速机会粗算</h2>
                <span className="quick-tag" title="不影响提审">可选 · 不保存</span>
              </div>
              <button className="text-button" aria-expanded={quickExpanded} onClick={() => setQuickExpanded((current) => !current)}>
                {quickExpanded ? "收起" : "展开粗算"}
              </button>
            </div>
            {quickExpanded && (
              <>
                <div className="quick-grid">
                  <NullableNumberField label="售价 CAD" value={quickInput.salePriceCad} onChange={(value) => updateQuickInput("salePriceCad", value)} />
                  <NullableNumberField label="采购 ¥" value={quickInput.purchaseCostCny} onChange={(value) => updateQuickInput("purchaseCostCny", value)} allowZero />
                  <NullableNumberField label="头程 ¥" value={quickInput.headhaulCny} onChange={(value) => updateQuickInput("headhaulCny", value)} allowZero />
                  <NullableNumberField label="FBA CAD" value={quickInput.fbaFeeCad} onChange={(value) => updateQuickInput("fbaFeeCad", value)} allowZero />
                  <NullableRateField label="营销售后占比" value={quickInput.marketingAfterSalesRate} onChange={(value) => updateQuickInput("marketingAfterSalesRate", value)} />
                </div>
                <div className="quick-strip">
                  <span className="quick-params">1加元 ≈ {(quickInput.cadPerCny > 0 ? (1 / quickInput.cadPerCny).toFixed(4) : "—")}人民币 · 佣金率 {percent(quickInput.referralFeeRate)} · 参考线 <b>{percent(quickTarget)}</b></span>
                  {quickResult ? (
                    <>
                      <span>净利润 <b className={quickResult.quickProfitCny >= 0 ? "positive" : "negative"}>{cny(quickResult.quickProfitCny)}</b></span>
                      <span>利润率 <b>{percent(quickResult.quickMarginRate)}</b></span>
                      <span>前置资金ROI <b>{percent(quickResult.upfrontCapitalRoi)}</b></span>
                      <span>销售佣金 <b>{money(quickResult.salesCommissionCad)} / 约 {cny(quickResult.salesCommissionCny)}</b></span>
                      <span className={`quick-verdict ${quickPassed ? "pass" : "watch"}`}>
                        {quickPassed ? `过 ${percent(quickTarget)} 参考线` : "低于参考线"}
                      </span>
                    </>
                  ) : (
                    <span className="quick-empty">{quickError ?? "填入上方数字即出粗算"}</span>
                  )}
                </div>
                <div className="quick-footer">
                  <p>粗算未覆盖仓储/移除/残次、促销和汇兑损耗；正式尺寸、重量与FBA计算以完整表单为准。</p>
                  <div>
                    <button className="text-button" onClick={() => goToFullForm(false)}>跳过粗算，直接填写</button>
                    <button className="secondary-button" disabled={!quickResult} onClick={() => goToFullForm(true)}>
                      {quickPassed ? "带入完整表单" : "仍要继续完善"}
                    </button>
                  </div>
                </div>
              </>
            )}
          </section>

          <div className="entry-layout">
          <section className="form-panel" ref={fullFormRef}>
            <div className="panel-heading section-heading">
              <div className="panel-heading-title">
                <span className="panel-number">02</span>
                <h2>{editing ? `修改候选产品 · ${editing.input.productNameCn}` : "候选产品录入"}</h2>
              </div>
              {editing && <button className="text-button" onClick={() => resetForm(snapshot.rates)}>退出修改</button>}
            </div>

            <fieldset>
              <legend>商品识别</legend>
              <div className="field-grid two">
                <label>产品中文名称 *<input value={input.productNameCn} onChange={(event) => updateInput("productNameCn", event.target.value)} /></label>
                <label>产品英文名称<input value={input.productNameEn} onChange={(event) => updateInput("productNameEn", event.target.value)} /></label>
                <label>品类 *
                  <select value={input.category} onChange={(event) => updateInput("category", event.target.value)}>
                    <option value="">请选择</option>
                    {["配件", "家居", "厨房", "宠物", "美妆", "文具", "3C", "工具", "其他"].map((item) => <option key={item}>{item}</option>)}
                  </select>
                </label>
                <label>参考竞品ASIN<input value={input.referenceAsins} onChange={(event) => updateInput("referenceAsins", event.target.value)} placeholder="最多3个，逗号分隔" /></label>
                <label className="span-two">1688链接 *<input type="url" value={input.sourceUrl} onChange={(event) => updateInput("sourceUrl", event.target.value)} placeholder="https://detail.1688.com/…" /></label>
                <label>产品图片 *
                  <input type="file" accept="image/*" onChange={(event) => setImageFile(event.target.files?.[0] ?? null)} />
                  <small className="hint">支持 Ctrl+V / ⌘V 直接粘贴截图</small>
                </label>
                <div className="img-preview">
                  {imagePreviewUrl
                    ? <img className="pv-thumb" src={imagePreviewUrl} alt="产品图片预览" />
                    : editing?.hasImage
                      ? <ProductThumb repository={repository} record={editing} viewerId={snapshot.access.userId} className="pv-thumb" />
                      : <div className="pv-thumb placeholder"><PhotoPlaceholderIcon /></div>}
                  <div className="pv-meta">
                    {imageFile || editing?.hasImage ? (
                      <>
                        <b>{imageFile ? (imageFile.name || "剪贴板图片") : "原记录产品图片"}</b>
                        {imageFile ? ` · ${(imageFile.size / 1024).toFixed(0)} KB · 已就绪，提交时上传` : " · 已保留"}
                        <br /><span className="hint">{imageFile ? "重新选择或粘贴即替换" : "重新选择或粘贴即替换原图"}</span>
                      </>
                    ) : (
                      <>
                        <b>未选择图片</b>
                        <br /><span className="hint">选择文件或 Ctrl+V / ⌘V 粘贴截图后，在此预览</span>
                      </>
                    )}
                  </div>
                  {imageFile && <button type="button" className="text-button danger pv-remove" onClick={() => setImageFile(null)}>移除</button>}
                </div>
              </div>
            </fieldset>

            <SelectionRationaleInput value={input.selectionRationale} onChange={value => updateInput("selectionRationale", value)} />
            <fieldset>
              <legend>尺寸与物流</legend>
              <div className="field-grid two">
                <NumberField label="包装长(cm) *" value={input.lengthCm} onChange={(value) => updateInput("lengthCm", value)} />
                <NumberField label="包装宽(cm) *" value={input.widthCm} onChange={(value) => updateInput("widthCm", value)} />
                <NumberField label="包装高(cm) *" value={input.heightCm} onChange={(value) => updateInput("heightCm", value)} />
                <NumberField label="包装重量(g) *" value={input.weightG} onChange={(value) => updateInput("weightG", value)} />
              </div>
            </fieldset>

            <fieldset>
              <legend>成本与定价</legend>
              <div className="field-grid three">
                <NumberField label="目标售价(CAD) *" value={input.targetPriceCad} onChange={(value) => updateInput("targetPriceCad", value)} />
                <NumberField label="采购单价(¥) *" value={input.purchasePriceCny} onChange={(value) => updateInput("purchasePriceCny", value)} />
                <NumberField label="包装加工费(¥/件) *" value={input.processingCostCny} onChange={(value) => updateInput("processingCostCny", value)} allowZero />
              </div>
              {formRates && (
                <div className="override-box">
                  <div><strong>本商品参数</strong><small>提交时固化为快照，可按实际报价调整</small></div>
                  <div className="field-grid four">
                    <RateField label="加元兑人民币汇率" value={formRates.cadPerCny > 0 ? Number((1 / formRates.cadPerCny).toFixed(4)) : 0} onChange={(value) => { if (value > 0) updateRate("cadPerCny", Number((1 / value).toFixed(6))); }} />
                    <RateField label="头程¥/kg" value={formRates.headhaulCnyPerKg} onChange={(value) => updateRate("headhaulCnyPerKg", value)} />
                    <RateField label="佣金率" value={formRates.referralFeeRate} percentage onChange={(value) => updateRate("referralFeeRate", value)} />
                    <RateField label="广告费率" value={formRates.adRate} percentage onChange={(value) => updateRate("adRate", value)} />
                  </div>
                </div>
              )}
            </fieldset>

            <fieldset>
              <legend>侵权排查（一票否决）</legend>
              <div className="field-grid three">
                <RiskField label="商标侵权排查 *" value={input.trademarkRisk} onChange={(value) => updateInput("trademarkRisk", value)} />
                <RiskField label="专利侵权排查 *" value={input.patentRisk} onChange={(value) => updateInput("patentRisk", value)} />
                <RiskField label="版权侵权排查 *" value={input.copyrightRisk} onChange={(value) => updateInput("copyrightRisk", value)} />
              </div>
              <label>侵权排查说明 *
                <textarea rows={3} value={input.complianceNotes} onChange={(event) => updateInput("complianceNotes", event.target.value)} placeholder="记录查询渠道、关键词、链接和结论依据" />
              </label>
            </fieldset>

            <fieldset>
              <legend>供应商可行性</legend>
                <div className="field-grid three">
                <label>供应商名称<input value={input.supplierName} onChange={(event) => updateInput("supplierName", event.target.value)} /></label>
                <label>MOQ<DecimalInput value={input.moq} onChange={(value) => updateInput("moq", value ?? 0)} min="0" step="1" blankWhenZero /></label>
                <label>供应商配合度
                  <select value={input.supplierCooperation} onChange={(event) => updateInput("supplierCooperation", event.target.value)}>
                    <option value="">未评估</option><option>高</option><option>中</option><option>低</option>
                  </select>
                </label>
              </div>
              <div className="field-grid two">
                <label>打样及交期说明<textarea rows={2} value={input.sampleAndLeadTimeNotes} onChange={(event) => updateInput("sampleAndLeadTimeNotes", event.target.value)} /></label>
                <label className="span-two">阶梯报价说明<textarea rows={2} value={input.tieredQuotationNotes} onChange={(event) => updateInput("tieredQuotationNotes", event.target.value)} /></label>
                <label className="span-two">备注<textarea rows={2} value={input.notes} onChange={(event) => updateInput("notes", event.target.value)} /></label>
              </div>
            </fieldset>
          </section>

          <aside className="calculator-panel">
            <div className="calculator-sticky">
              <div className="panel-heading">
                <div className="panel-heading-title">
                  <span className="panel-number">03</span>
                  <h2>完整测算与提交</h2>
                </div>
              </div>
              {calculation && formRates ? (
                <>
                  <div className="metric-hero">
                    <span>毛利率</span>
                    <strong className={statusTone(calculation.completeContributionRate, formRates.targetContributionRate)}>{percent(calculation.completeContributionRate)}</strong>
                    <small>审核线 {percent(formRates.targetContributionRate)}</small>
                  </div>
                  <div className="metric-grid">
                    <Metric label="初步利润/件" value={money(calculation.estimatedProfitCad)} />
                    <Metric label="利润/件" value={money(calculation.completeContributionCad)} />
                    <Metric label="前置资金ROI" value={percent(calculation.upfrontCapitalRoi)} />
                    <Metric label="FBA配送费" value={money(calculation.fbaFinalFeeCad)} />
                    <Metric label="计费重" value={`${calculation.chargeableWeightKg.toFixed(3)}kg`} />
                    <Metric label="采购+包装" value={yuan(input.purchasePriceCny + input.processingCostCny)} />
                  </div>
                  <div className="calculation-detail">
                  <div className="cd-title">自动测算 · 随输入实时更新</div>
                  <dl>
                    <div><dt>尺寸分段</dt><dd>{calculation.sizeTier}</dd></div>
                    <div><dt>体积重</dt><dd>{calculation.volumeWeightKg.toFixed(3)} kg</dd></div>
                    <div><dt>计费重量</dt><dd>{calculation.chargeableWeightKg.toFixed(3)} kg</dd></div>
                    <div><dt>FBA基础费</dt><dd>{money(calculation.fbaBaseFeeCad)}</dd></div>
                    <div><dt>低价折扣</dt><dd>-{money(calculation.lowPriceDiscountCad)}</dd></div>
                    <div><dt>3.5%附加费</dt><dd>{money(calculation.fbaSurchargeCad)}</dd></div>
                    <div><dt>头程</dt><dd>{money(calculation.headhaulCad)}</dd></div>
                  </dl>
                  </div>
                  <div className="gate-list">
                    <strong>提交硬门槛</strong>
                    {gate?.items.map((item) => (
                      <div className={item.passed ? "gate-pass" : "gate-fail"} key={item.label}>
                        <span>{item.passed ? "✓" : "×"}</span><p><b>{item.label}</b><small>{item.detail}</small></p>
                      </div>
                    ))}
                  </div>
                  <button className="primary-button" disabled={busy || !gate?.passed || !snapshot.access.canSubmit || !snapshot.integration.ready || (LEGACY_NEW_SUBMISSIONS_CLOSED && !editing)} onClick={() => void submit()}>
                    {busy ? "正在写入并校验…" : editing ? "修改并重新提交" : selfExempt ? "提交选品（免评审）" : "提交主管审核"}
                  </button>
                  <p className="submit-note">提交后写入SKU选品库并回读公式；{selfExempt ? "校验通过后自动免评审。" : "校验通过后进入主管审核。"}未提交内容不会保存。</p>
                </>
              ) : <p>等待参数加载…</p>}
            </div>
          </aside>
          </div>
        </div>
      )}

      {drawerRecord && (
        <RecordDrawer repository={repository} record={drawerRecord} viewerId={snapshot.access.userId} onClose={() => setDrawerRecord(null)} />
      )}
      {legacyOverride && <div className="site-validation-backdrop"><div className="site-validation-dialog" role="dialog" aria-modal="true" aria-label="例外通过确认">
        <h2>确认例外通过</h2><p>以下指标未达标：{legacyOverride.reasons.join("、")}。请填写原因并确认，审核意见会保留在记录中。</p>
        <textarea aria-label="例外通过原因" value={reviewComments[legacyOverride.record.recordId] ?? ""}
          onChange={(event) => setReviewComments((current) => ({ ...current, [legacyOverride.record.recordId]: event.target.value }))} />
        <div className="site-validation-actions"><button className="secondary-button" onClick={() => setLegacyOverride(null)}>取消</button>
          <button className="primary-button" disabled={busy || !reviewComments[legacyOverride.record.recordId]?.trim()}
            onClick={() => void review(legacyOverride.record, "approve", true)}>确认例外通过</button></div>
      </div></div>}
    </main>
  );
}

function decimalDraft(value: number | null, blankWhenZero: boolean): string {
  return value === null || (blankWhenZero && value === 0) ? "" : String(value);
}

function DecimalInput({ value, onChange, blankWhenZero = false, min = "0", max, step = "any", ariaLabel }: {
  value: number | null;
  onChange: (value: number | null) => void;
  blankWhenZero?: boolean;
  min?: string;
  max?: string;
  step?: string;
  ariaLabel?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState(() => decimalDraft(value, blankWhenZero));
  useEffect(() => {
    if (document.activeElement !== inputRef.current) setDraft(decimalDraft(value, blankWhenZero));
  }, [value, blankWhenZero]);
  return <input
    ref={inputRef}
    type="number"
    min={min}
    max={max}
    step={step}
    value={draft}
    aria-label={ariaLabel}
    onChange={(event) => {
      const raw = event.target.value;
      setDraft(raw);
      if (raw === "") return onChange(null);
      const parsed = Number(raw);
      if (Number.isFinite(parsed)) onChange(parsed);
    }}
    onBlur={() => setDraft(decimalDraft(value, blankWhenZero))}
  />;
}

function NumberField({ label, value, onChange, allowZero = false }: { label: string; value: number; onChange: (value: number) => void; allowZero?: boolean }) {
  return <label>{label}<DecimalInput value={value} onChange={(next) => onChange(next ?? 0)} min={allowZero ? "0" : "0.0001"} blankWhenZero={!allowZero} /></label>;
}

function NullableNumberField({ label, value, onChange, allowZero = false }: { label: string; value: number | null; onChange: (value: number | null) => void; allowZero?: boolean }) {
  return <label>{label}<DecimalInput value={value} onChange={onChange} min={allowZero ? "0" : "0.0001"} /></label>;
}

function NullableRateField({ label, value, onChange }: { label: string; value: number | null; onChange: (value: number | null) => void }) {
  return (
    <label>
      <span>{label}</span>
      <span className="input-with-suffix">
        <DecimalInput
          ariaLabel={label}
          min="0"
          max="100"
          step="0.1"
          value={value === null ? null : Number((value * 100).toFixed(6))}
          onChange={(next) => onChange(next === null ? null : next / 100)}
        />
        <span className="input-suffix" aria-hidden="true">%</span>
      </span>
    </label>
  );
}

function RateField({ label, value, onChange, percentage = false }: { label: string; value: number; onChange: (value: number) => void; percentage?: boolean }) {
  if (!percentage) {
    return <label>{label}<DecimalInput value={value} onChange={(next) => onChange(next ?? 0)} min="0" /></label>;
  }
  return (
    <label>
      <span>{label}</span>
      <span className="input-with-suffix">
        <DecimalInput
          ariaLabel={label}
          min="0"
          max="100"
          step="0.1"
          value={Number((value * 100).toFixed(6))}
          onChange={(next) => onChange((next ?? 0) / 100)}
        />
        <span className="input-suffix" aria-hidden="true">%</span>
      </span>
    </label>
  );
}

function RiskField({ label, value, onChange }: { label: string; value: ComplianceStatus; onChange: (value: ComplianceStatus) => void }) {
  return <label>{label}<select value={value} onChange={(event) => onChange(event.target.value as ComplianceStatus)}><option>未排查</option><option>无风险</option><option>待确认</option><option>有风险</option></select></label>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
