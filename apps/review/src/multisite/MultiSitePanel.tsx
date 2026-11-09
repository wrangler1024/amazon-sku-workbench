import { selectionRationaleProblem } from "../domain/selectionRationale";
import SelectionRationale, { SelectionRationaleInput } from "../components/SelectionRationale";
import ReferenceAsins from "../components/ReferenceAsins";
import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type ReactNode } from "react";
import type { UserAccess } from "../domain/types";
import { intellectualPropertyPreview } from "../domain/riskPreview";
import { reviewPathForSubmission } from "../domain/permissions";
import { cachedImageUrl, resolveImageUrl } from "../domain/imageUrlCache";
import { calculateSite, currentShanghaiDate, forecastTimestamp, MARKETPLACES, newSiteDraft,
  submissionProblems, type Marketplace, type SiteDraft, type TaxClass } from "./domain";
import type { OfficialFbaQuoteInput, SiteDashboard, SiteReview } from "./repository";
import QuickOpportunity, { type QuickCarryValues } from "./QuickOpportunity";
import SiteReviewThumb from "./SiteReviewThumb";
import ReviewDetail from "./ReviewDetail";
import { storedReviewQuote, reviewAttention, reviewDate } from "./reviewPresentation";
import type { FbaCard } from "./rateCards";
import { useFbaReference } from "./useFbaReference";
import { summarizePendingReviews, type NumberRange } from "./reviewSummary";
import { smallSizeAdvice } from "./sizing";

import { loadSitePreference, saveSitePreference, type SitePreferenceScope } from "./sitePreference";

type View = "multisite" | "submitted" | "returned" | "review";
export interface QueueEntry { id: string; search: string; node: ReactNode;
  submitterId?: string; submitterName?: string; site?: Marketplace;
  listedPrice?: number; purchaseCny?: number; contributionRate?: number | null }
export interface LegacyQueues { submitted: QueueEntry[]; returned: QueueEntry[]; review: QueueEntry[];
  excludedPendingCount: number }
const sites = Object.entries(MARKETPLACES) as Array<[Marketplace, { label: string; currency: string }]>;
const commonCategories = ["配件", "家居", "厨房", "宠物", "美妆", "文具", "3C", "工具", "其他"];
const money = (currency: string, value: number) => `${currency} ${value.toFixed(2)}`;
const rangeText = (value: NumberRange | null, format: (number: number) => string) =>
  value ? value.min === value.max ? format(value.min) : `${format(value.min)}–${format(value.max)}` : "待计算";

interface Props { demo?: boolean; access: UserAccess; view?: View; hiddenEntry?: boolean;
  refreshVersion?: number;
  onNavigate?: (view: View) => void;
  onCounts?: (counts: { submitted: number; returned: number; review: number }) => void;
  legacyQueues?: LegacyQueues;
}
export default function MultiSitePanel(props: Props) {
  return <SitePanel key={props.access.userId} {...props} />;
}
function SitePanel({ demo = false, access, view = "multisite", hiddenEntry = false, refreshVersion = 0, onNavigate,
  onCounts, legacyQueues }: Props) {
  const [data, setData] = useState<SiteDashboard | null>(null);
  const [draft, setDraft] = useState<SiteDraft>(() => newSiteDraft());
  const [siteSelected, setSiteSelected] = useState(false);
  const [preferenceLoaded, setPreferenceLoaded] = useState(false);
  const [preferenceNotice, setPreferenceNotice] = useState("");
  const [preferenceWarning, setPreferenceWarning] = useState("");
  const preferenceScope = useRef<SitePreferenceScope | null>(null);
  const preferredSite = useRef<Marketplace | null>(null);
  const siteTouched = useRef(false);
  const [cards, setCards] = useState<FbaCard[]>([]);
  const [cardError, setCardError] = useState("");
  const [quickPurchaseCny, setQuickPurchaseCny] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [image, setImage] = useState<File | null>(null);
  const [imagePreviewUrl, setImagePreviewUrl] = useState("");
  const [existingImageUrl, setExistingImageUrl] = useState("");
  const [reviewDetail, setReviewDetail] = useState<SiteReview | null>(null);
  const [reviewQueueIds, setReviewQueueIds] = useState<string[]>([]);
  const [reviewCompleted, setReviewCompleted] = useState(false);
  const [reviewResult, setReviewResult] = useState<{ title: string; detail: string } | null>(null);
  const reviewActionLock = useRef(false);
  const [comments, setComments] = useState<Record<string, string>>({});
  const [quotes, setQuotes] = useState<Record<string, OfficialFbaQuoteInput>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [actionError, setActionError] = useState("");
  const [submitAttempted, setSubmitAttempted] = useState(false);
  const [validationOpen, setValidationOpen] = useState(false);
  const [approvalOverride, setApprovalOverride] = useState<{ id: string; reasons: string[] } | null>(null);
  const [submissionQuery, setSubmissionQuery] = useState("");
  const [reviewOperator, setReviewOperator] = useState("");
  const [reviewOperatorName, setReviewOperatorName] = useState("");
  const [reviewSite, setReviewSite] = useState<Marketplace | "">("");
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [categoryFiltering, setCategoryFiltering] = useState(false);

  useEffect(() => {
    let active = true;
    void loadSitePreference(access.userId).then(({ scope, preference, storage }) => {
      if (!active) return;
      preferenceScope.current = scope;
      preferredSite.current = preference?.site ?? null;
      if (preference && !siteTouched.current) {
        setDraft(newSiteDraft(preference.site)); setSiteSelected(true);
        setPreferenceNotice(`已沿用你上次使用的站点：${MARKETPLACES[preference.site].label} · ${MARKETPLACES[preference.site].currency}${storage === "local" ? "（当前设备记忆）" : ""}`);
      }
      setPreferenceLoaded(true);
    });
    return () => { active = false; };
  }, [access.userId]);

  const rememberNewSite = async (site: Marketplace) => {
    preferredSite.current = site;
    const storage = await saveSitePreference(preferenceScope.current, site);
    setPreferenceNotice(`已记住站点：${MARKETPLACES[site].label} · ${MARKETPLACES[site].currency}`);
    setPreferenceWarning(storage === "cloud" ? "" : storage === "local"
      ? "站点已在当前设备记住，跨设备同步暂不可用。"
      : "选品已保存；站点记忆暂不可用，下次新建时请重新选择站点。");
  };
  const startNew = () => {
    siteTouched.current = true;
    setEditingId(null); setDraft(newSiteDraft(preferredSite.current ?? "CA"));
    setSiteSelected(Boolean(preferredSite.current)); setImage(null); setExistingImageUrl("");
    setQuickPurchaseCny(null); setSubmitAttempted(false); setValidationOpen(false);
    setMessage(""); setActionError("");
  };

  const reload = useCallback(async () => {
    if (demo) {
      const loaded: SiteDashboard = { reviews: [], taxes: [], finances: [], rules: [], problems: ["本地演示仅展示合成评审任务；多站点录入需配置自己的飞书表格。"] };
      setData(loaded); setCards([]); return loaded;
    }
    const { loadSiteDashboard, loadFbaRateCards } = await import("./repository");
    const loaded = await loadSiteDashboard();
    setData(loaded);
    try { setCards(await loadFbaRateCards()); setCardError(""); }
    catch (error) { setCards([]); setCardError(error instanceof Error ? error.message : String(error)); }
    return loaded;
  }, [demo]);
  useEffect(() => { void reload().catch((error) => setMessage(String(error))); }, [reload]);
  useEffect(() => { if (refreshVersion > 0) void reload().catch((error) => setMessage(String(error))); }, [refreshVersion, reload]);
  useEffect(() => {
    if (!image || typeof URL.createObjectURL !== "function") { setImagePreviewUrl(""); return; }
    const url = URL.createObjectURL(image);
    setImagePreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [image]);
  useEffect(() => {
    const record = data?.reviews.find((item) => item.id === editingId);
    if (!record?.imageToken) { setExistingImageUrl(""); return; }
    let active = true;
    setExistingImageUrl(cachedImageUrl(access.userId, "SKU选品库", record.id, record.imageToken) ?? "");
    void resolveImageUrl(access.userId, "SKU选品库", record.id, record.imageToken,
      async () => (await import("./repository")).getSiteImageUrl(record.id, record.imageToken))
      .then((url) => { if (active) setExistingImageUrl(url ?? ""); });
    return () => { active = false; };
  }, [data, editingId, access.userId]);

  const ownDrafts = data?.reviews.filter((item) => item.submitterId === access.userId && item.status === "草稿") ?? [];
  const pendingRecord = editingId ? data?.reviews.find((item) =>
    item.id === editingId && item.validation === "校验中") : undefined;
  const pendingEditing = Boolean(pendingRecord);
  const freshPending = pendingEditing && Date.now() - (pendingRecord?.submittedAt ?? 0) < 120_000;
  useEffect(() => {
    if (!pendingEditing || !editingId) return;
    let active = true;
    const interval = window.setInterval(() => {
      void reload().then((loaded) => {
        if (!active) return;
        const record = loaded.reviews.find((item) => item.id === editingId);
        if (record && ["待审核", "审核通过"].includes(record.status)) {
          startNew();
          setMessage(record.status === "审核通过"
            ? "选品已审核通过" : "已提交审核，可在「我的提交」查看待审批记录");
        }
      }).catch((error) => { if (active) setMessage(String(error)); });
    }, 3000);
    return () => { active = false; window.clearInterval(interval); };
  }, [pendingEditing, editingId, reload]);
  const selfExempt = Boolean(access.submitGroup &&
    reviewPathForSubmission(access, access.submitGroup) === "主管自提免评审");
  const isAdmin = access.role === "管理员";
  const ownSubmitted = data?.reviews.filter((item) => (isAdmin || item.submitterId === access.userId) &&
    item.status === "待审核" && item.validation === "通过") ?? [];
  const ownReturned = data?.reviews.filter((item) => (isAdmin || item.submitterId === access.userId) && item.status === "打回修改") ?? [];
  const pending = data?.reviews.filter((item) => item.status === "待审核" &&
    item.validation === "通过" && (isAdmin || item.submitterId !== access.userId) &&
    (access.canReviewAll || access.reviewGroups.includes(item.group as never))) ?? [];
  const reviewOperators = [...new Map([
    ...(reviewOperator ? [[reviewOperator, reviewOperatorName || "已选运营"] as const] : []),
    ...pending.map((item) => [item.submitterId, item.submitterName || "运营"] as const),
    ...(legacyQueues?.review ?? []).map((item) =>
      [item.submitterId || item.id, item.submitterName || "运营"] as const),
  ]).entries()].sort((a, b) => a[1].localeCompare(b[1], "zh-CN"));
  const visiblePending = pending.filter((item) =>
    (!reviewOperator || item.submitterId === reviewOperator) &&
    (!reviewSite || item.draft.site === reviewSite));
  const visibleLegacyReview = (legacyQueues?.review ?? []).filter((item) =>
    (!reviewOperator || (item.submitterId || item.id) === reviewOperator) &&
    (!reviewSite || (item.site ?? "CA") === reviewSite));
  const reviewSummary = summarizePendingReviews([
    ...visiblePending.map((item) => ({ site: item.draft.site,
      listedPrice: item.draft.listedPrice, purchaseCny: item.draft.purchaseCny,
      contributionRate: item.profit?.rate ?? null })),
    ...visibleLegacyReview.map((item) => ({ site: item.site ?? "CA" as Marketplace,
      listedPrice: item.listedPrice ?? NaN, purchaseCny: item.purchaseCny ?? NaN,
      contributionRate: item.contributionRate ?? null })),
  ]);
  const saleRanges = sites.filter(([code]) => reviewSummary.prices.has(code)).map(([code, site]) =>
    `${site.currency} ${rangeText(reviewSummary.prices.get(code)!, (value) => value.toFixed(2))}`).join(" · ");
  const query = submissionQuery.trim().toLocaleLowerCase();
  const visibleSubmitted = ownSubmitted.filter((item) => !query ||
    [item.draft.entitySku, item.draft.productName, item.draft.referenceAsins]
      .some((value) => value.toLocaleLowerCase().includes(query)));
  const visibleLegacySubmitted = (legacyQueues?.submitted ?? []).filter((item) =>
    !query || item.search.toLocaleLowerCase().includes(query));
  useEffect(() => { onCounts?.({ submitted: ownSubmitted.length, returned: ownReturned.length,
    review: pending.length }); }, [ownSubmitted.length, ownReturned.length, pending.length, onCounts]);

  const currency = MARKETPLACES[draft.site].currency;
  const forecast = forecastTimestamp(draft.expectedSaleDate);
  const tax = data?.taxes.find((item) => item.site === draft.site &&
    item.effectiveFrom <= forecast && (item.effectiveUntil === null || item.effectiveUntil > forecast)) ?? null;
  const finance = data?.finances.filter((item) => item.site === draft.site &&
    [draft.category, "通用", "全站", "待确认"].includes(item.category) &&
    (item.effectiveFrom === 0 || item.effectiveFrom <= forecast) &&
    (item.effectiveUntil === null || item.effectiveUntil > forecast))
    .sort((a, b) => Number(b.category === draft.category) - Number(a.category === draft.category) ||
      b.effectiveFrom - a.effectiveFrom)[0] ?? null;
  const roughFinance = data?.finances.find((item) => item.site === draft.site &&
    ["通用", "全站", "待确认"].includes(item.category)) ?? null;
  const categoryOptions = [...new Set([...commonCategories,
    ...(data?.reviews.map((item) => item.draft.category.trim()).filter(Boolean) ?? [])])];
  const visibleCategories = categoryFiltering
    ? categoryOptions.filter((item) => item.toLocaleLowerCase().includes(draft.category.trim().toLocaleLowerCase()))
    : categoryOptions;
  const calculation = useMemo(() => tax && finance?.approved && finance.cnyPerCurrency > 0 &&
    finance.headhaulCnyPerKg > 0 && finance.volumeDivisorCm3PerKg > 0 && data
    ? calculateSite(draft, tax, finance, data.rules, forecast) : null,
  [draft, tax, finance, data, forecast]);
  const problems = submissionProblems(draft, tax, finance, calculation, forecast);
  const rationaleProblem = selectionRationaleProblem(draft.selectionRationale);
  if (rationaleProblem) problems.push(rationaleProblem);
  const hasImage = Boolean(image) || Boolean(data?.reviews.find((item) => item.id === editingId)?.hasImage);
  if (!hasImage) problems.push("产品图片未上传");
  const invalidFields = new Set<string>();
  if (rationaleProblem) invalidFields.add("selectionRationale");
  if (!Number.isFinite(forecast)) invalidFields.add("expectedSaleDate");
  if (!draft.productName.trim()) invalidFields.add("productName");
  if (!draft.category.trim()) invalidFields.add("category");
  if (!draft.sourceUrl.trim()) invalidFields.add("sourceUrl");
  if (!draft.region.trim()) invalidFields.add("region");
  for (const key of ["listedPrice", "fbaFeeLocal", "purchaseCny", "lengthCm", "widthCm",
    "heightCm", "unitWeightG"] as const) {
    if (!Number.isFinite(draft[key]) || draft[key] <= 0) invalidFields.add(key);
  }
  if (!Number.isFinite(draft.processingCny) || draft.processingCny < 0) invalidFields.add("processingCny");
  for (const key of ["trademarkRisk", "patentRisk", "copyrightRisk"] as const) {
    if (draft[key] !== "无风险") invalidFields.add(key);
  }
  if (!draft.complianceNotes.trim()) invalidFields.add("complianceNotes");
  if (draft.taxClass !== "标准") invalidFields.add("taxClass");
  if (!hasImage) invalidFields.add("image");
  const fieldInvalid = (key: string) => submitAttempted && invalidFields.has(key);
  const sizing = smallSizeAdvice(data?.sizeRules ?? {}, draft.site,
    [draft.lengthCm, draft.widthCm, draft.heightCm], draft.unitWeightG);
  const cardRequest = { site: draft.site, category: "普通商品" as const,
    price: draft.listedPrice,
    dimensionsCm: [draft.lengthCm, draft.widthCm, draft.heightCm] as [number, number, number],
    unitWeightG: draft.unitWeightG, saleDate: forecast };
  const fbaReference = useFbaReference(cards, cardRequest, draft.expectedSaleDate, data?.sizeRules ?? {});
  const { estimate: cardEstimate, packing } = fbaReference;
  const referenceInfo = cardEstimate.reference ?? cardEstimate.card;
  const previewReady = [draft.listedPrice, draft.lengthCm, draft.widthCm,
    draft.heightCm, draft.unitWeightG].every((value) => Number.isFinite(value) && value > 0);

  const update = <K extends keyof SiteDraft>(key: K, value: SiteDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const changeSite = (site: Marketplace) => {
    if (editingId || busy || !Object.hasOwn(MARKETPLACES, site)) return;
    siteTouched.current = true; setSiteSelected(true); setPreferenceNotice("");
    setSubmitAttempted(false); setValidationOpen(false);
    setDraft((current) => ({ ...current, site, listedPrice: 0, fbaFeeLocal: 0,
      region: "全站", taxClass: "标准", expectedSaleDate: currentShanghaiDate() }));
  };
  const carryQuick = (values: QuickCarryValues) => {
    setDraft((current) => ({ ...current, ...values }));
    document.getElementById("site-review-form")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  const numberField = (label: string, key: keyof SiteDraft, required = false) =>
    <label key={key}>{label}{required ? " *" : ""}
      <input type="number" min="0" step="any" value={draft[key] as number || ""}
        aria-invalid={fieldInvalid(key)}
        onChange={(event) => update(key, (event.target.value === "" ? 0 : Number(event.target.value)) as never)} />
    </label>;
  const act = async (operation: () => Promise<void>) => {
    setBusy(true); setMessage(""); setActionError("");
    try { await operation(); await reload(); }
    catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setMessage(reason); setActionError(reason);
    }
    finally { setBusy(false); }
  };
  const save = () => void act(async () => {
    if (!siteSelected || !preferenceLoaded) return;
    const { saveSiteDraft } = await import("./repository");
    const id = await saveSiteDraft(draft, access, editingId, image);
    setEditingId(id); setImage(null); setMessage("草稿已保存，可在下方「我的草稿」继续编辑");
    if (!editingId) await rememberNewSite(draft.site);
  });
  const submit = () => void (async () => {
    setBusy(true); setMessage(""); setActionError("");
    let savedId = editingId;
    try {
      const { saveSiteDraft, submitSiteReview } = await import("./repository");
      const id = await saveSiteDraft(draft, access, editingId, image);
      savedId = id;
      setEditingId(id);
      if (!editingId) await rememberNewSite(draft.site);
      await submitSiteReview(id, access);
      startNew();
      setMessage(selfExempt
        ? "主管自提已免审核通过，后续制图和 ERP 任务将按流程处理"
        : "已提交审核，可在「我的提交」查看待审批记录");
      try { await reload(); }
      catch { setMessage("已提交审核；列表暂未刷新，请重新打开评审台查看"); }
    } catch (error) {
      const { SubmissionPendingError } = await import("./repository");
      if (error instanceof SubmissionPendingError) {
        setMessage(error.message); setActionError(""); setValidationOpen(false);
        try { await reload(); } catch { /* The Base workflow continues independently. */ }
        return;
      }
      const reason = error instanceof Error ? error.message : String(error);
      setMessage(reason); setActionError(reason); setValidationOpen(true);
      try {
        const loaded = await reload();
        const editable = loaded.reviews.filter((item) => item.submitterId === access.userId &&
          (item.status === "草稿" || item.status === "打回修改"));
        const saved = savedId
          ? editable.find((item) => item.id === savedId)
          : (() => {
            const matches = editable.filter((item) => item.draft.productName === draft.productName &&
              item.draft.site === draft.site);
            return matches.length === 1 ? matches[0] : undefined;
          })();
        if (saved) setEditingId(saved.id);
      } catch { /* Keep the current form and uploaded image for retry. */ }
    } finally { setBusy(false); }
  })();
  const requestSubmit = () => {
    if (!siteSelected || !preferenceLoaded) return;
    setSubmitAttempted(true); setActionError("");
    if (problems.length) { setValidationOpen(true); return; }
    submit();
  };
  const open = (record: SiteReview) => {
    siteTouched.current = true; setSiteSelected(true);
    setDraft(record.draft); setEditingId(record.id); setImage(null);
    setSubmitAttempted(false); setValidationOpen(false);
    setMessage(""); setActionError("");
    onNavigate?.("multisite"); window.scrollTo({ top: 0, behavior: "smooth" });
  };
  const withdraw = (record: SiteReview) => void (async () => {
    setBusy(true); setMessage(""); setActionError("");
    try {
      const { withdrawSiteReview } = await import("./repository");
      await withdrawSiteReview(record.id, access, record.submittedAt);
      open(record); setMessage("已撤回到草稿，请修改后重新提交审核");
      try { await reload(); }
      catch { setMessage("已撤回到草稿；列表暂未刷新，可继续修改原记录"); }
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error);
      setMessage(reason); setActionError(reason);
    } finally { setBusy(false); }
  })();
  const openReview = (item: SiteReview, keepQueue = false) => {
    if (busy) return;
    if (!keepQueue) setReviewQueueIds(visiblePending.map(record => record.id));
    setReviewDetail(item); setReviewCompleted(false); setApprovalOverride(null); setMessage(""); setReviewResult(null);
  };
  const detailIndex = reviewDetail ? reviewQueueIds.indexOf(reviewDetail.id) : -1;
  const detailNeighbor = (direction: -1 | 1) => {
    for (let index = detailIndex + direction; index >= 0 && index < reviewQueueIds.length; index += direction) {
      const record = visiblePending.find(item => item.id === reviewQueueIds[index]);
      if (record) return record;
    }
    return undefined;
  };
  const refreshReview = async () => {
    if (!reviewDetail || reviewActionLock.current) return;
    reviewActionLock.current = true; setBusy(true); setMessage(""); setApprovalOverride(null);
    try {
      const loaded = await reload();
      const record = loaded.reviews.find(item => item.id === reviewDetail.id);
      if (!record || (!access.canReviewAll && !access.reviewGroups.includes(record.group as never))) {
        setReviewCompleted(true); setMessage("记录不存在或已不在当前审批范围，请返回列表。");
      } else {
        setReviewDetail(record);
        setReviewCompleted(record.status !== "待审核" || record.validation !== "通过");
        setMessage(record.status === "待审核" && record.validation === "通过" ? "已重新读取，请核对最新数据后审核。" : "该记录已不在待审状态，请返回列表。");
      }
    } catch (error) { setMessage(`重新读取失败：${error instanceof Error ? error.message : String(error)}`); }
    finally { reviewActionLock.current = false; setBusy(false); }
  };
  const decide = (item: SiteReview, action: "approve" | "return" | "reject", allowOverride = false) => void (async () => {
    if (reviewActionLock.current || reviewCompleted) return;
    reviewActionLock.current = true; setBusy(true); setMessage("");
    try {
      const { decideSiteReview } = await import("./repository");
      await decideSiteReview(item.id, action, comments[item.id] ?? "", access,
        action === "approve" ? quotes[item.id] ?? storedReviewQuote(item) : undefined, allowOverride, item);
      const resultTitle = `${item.draft.productName || item.number}：${action === "approve" ? "审核通过" : action === "return" ? "已打回修改" : "已拒绝"}`;
      // Return as soon as the decision is saved; list refresh must not strand the reviewer in a completed detail.
      setApprovalOverride(null); setReviewDetail(null); setReviewCompleted(false);
      setData(current => current ? { ...current, reviews: current.reviews.filter(record => record.id !== item.id) } : current);
      setComments(current => { const next = { ...current }; delete next[item.id]; return next; });
      setQuotes(current => { const next = { ...current }; delete next[item.id]; return next; });
      setReviewResult({ title: resultTitle, detail: "已返回待审列表，正在刷新…" });
      try {
        await reload();
        setReviewResult(current => current ? { title: resultTitle, detail: "已返回待审列表。" } : null);
      } catch {
        setReviewResult(current => current ? { title: resultTitle, detail: "审批已成功，列表暂未刷新，请点击顶部「刷新数据」。" } : null);
      }
    } catch (error) {
      const { ApprovalOverrideRequiredError } = await import("./repository");
      if (error instanceof ApprovalOverrideRequiredError)
        setApprovalOverride({ id: item.id, reasons: error.reasons });
      else setMessage(error instanceof Error ? error.message : String(error));
    } finally { reviewActionLock.current = false; setBusy(false); }
  })();
  const handleImagePaste = (event: ClipboardEvent<HTMLElement>) => {
    const file = Array.from(event.clipboardData.items ?? [])
      .find((item) => item.type.startsWith("image/"))?.getAsFile();
    if (file) { event.preventDefault(); setImage(file); }
  };
  useEffect(() => {
    if (!validationOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setValidationOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    document.getElementById("site-validation-close")?.focus();
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [validationOpen]);
  const entryFeedback = <>
    {actionError ? <p role="alert" className="alert error site-action-feedback">
      <strong>操作未完成</strong><span>{actionError}</span>
    </p> : message && <div role="status" className="site-action-feedback">
      <span>{message}</span><button type="button" className="alert-close" aria-label="关闭操作提示" onClick={() => setMessage("")}>×</button>
    </div>}
    {preferenceWarning && <p className="hint site-preference-warning" role="status">{preferenceWarning}</p>}
  </>;
  const sitePicker = <label className="multisite-site-control" title={editingId
    ? "当前记录的站点已固定；如需其他站点，请新建选品。" : preferenceNotice || undefined}>目标站点
    <select aria-label="目标站点" value={siteSelected ? draft.site : ""} disabled={Boolean(editingId) || busy}
      onChange={(event) => changeSite(event.target.value as Marketplace)}>
      <option value="" disabled>请选择站点</option>
      {sites.map(([code, item]) => <option value={code} key={code}>{item.label} · {item.currency}</option>)}
    </select></label>;
  const queueCard = (record: SiteReview, action: "draft" | "submitted" | "returned") =>
    <article className="candidate-card mine-card site-queue-card" key={record.id}>
      <div className="cc-main"><div className="cc-head"><SiteReviewThumb record={record} viewerId={access.userId} /><div className="cc-ident">
        <p className="record-code">{record.draft.entitySku || record.number}
          <span className="cat">{MARKETPLACES[record.draft.site].label} · {MARKETPLACES[record.draft.site].currency}</span></p>
        <h3>{record.draft.productName || "未命名产品"}</h3>
        <span className="sub">{record.group || "运营小组待确认"} · <b>{record.submitterName || "运营"}</b> · {record.draft.category || "品类待填"}</span>
      </div><span className={`submission-status ${action === "returned" ? "needs-action" : "pending"}`}>{record.status}</span></div>
        <SelectionRationale value={record.draft.selectionRationale} compact />
        {record.managerComment && <p className="cc-comment">主管意见：{record.managerComment}</p>}
        {action !== "draft" ? <>
          <div className="row-mid"><span className="m">售价 <b>{money(MARKETPLACES[record.draft.site].currency, record.draft.listedPrice)}</b></span>
            <span className="m">采购 <b>¥{record.draft.purchaseCny.toFixed(2)}</b></span>
            <span className="m">包装 <b>{record.draft.lengthCm}×{record.draft.widthCm}×{record.draft.heightCm} cm · {record.draft.unitWeightG} g</b></span>
            {(() => { const ip = intellectualPropertyPreview(record.draft); return <span className={`m ip-preview ${ip.tone}`} title={ip.title}>{ip.text}</span>; })()}
            <span className="m">{smallSizeAdvice(data?.sizeRules ?? {}, record.draft.site,
              [record.draft.lengthCm, record.draft.widthCm, record.draft.heightCm], record.draft.unitWeightG).result}</span></div>
          <div className="queue-profit"><b className={record.profit && record.profit.rate < .25 ? "below-line" : undefined}>毛利率 {record.profit ? `${(record.profit.rate * 100).toFixed(2)}%` : "待计算"}</b>
            <span>利润/件 {record.profit ? money(MARKETPLACES[record.draft.site].currency, record.profit.contribution) : "待计算"}</span>
            <span>前置资金 ROI {record.profit ? `${(record.profit.roi * 100).toFixed(2)}%` : "待计算"}</span>
            <span>回款占比 {typeof record.profit?.payoutRate === "number" ? `${(record.profit.payoutRate * 100).toFixed(2)}%` : "待计算"}</span>
            <span>净销售 {typeof record.profit?.netRevenue === "number" ? money(MARKETPLACES[record.draft.site].currency, record.profit.netRevenue) : "待计算"}</span>
            <span>头程 {typeof record.profit?.headhaul === "number" ? money(MARKETPLACES[record.draft.site].currency, record.profit.headhaul) : "待计算"}</span>
            <span>佣金 {typeof record.profit?.referral === "number" ? money(MARKETPLACES[record.draft.site].currency, record.profit.referral) : "待计算"}</span>
            <span>FBA费 {money(MARKETPLACES[record.draft.site].currency, record.draft.fbaFeeLocal)}</span>
            <span>营销售后 {typeof record.profit?.marketingAndOther === "number" ? money(MARKETPLACES[record.draft.site].currency, record.profit.marketingAndOther) : "待计算"}</span></div>
        </> : <div className="cc-metrics"><span>售价 <b>{money(MARKETPLACES[record.draft.site].currency, record.draft.listedPrice)}</b></span>
          <span>手填 FBA <b>{money(MARKETPLACES[record.draft.site].currency, record.draft.fbaFeeLocal)}</b></span>
          <span>采购 <b>¥{record.draft.purchaseCny.toFixed(2)}</b></span></div>}
      </div><div className="cc-actions">
        <button className={action === "returned" ? "primary-button" : "secondary-button"} disabled={busy}
          onClick={() => isAdmin && record.submitterId !== access.userId
            ? void import("./repository").then(({ openSiteRecord }) => openSiteRecord(record.id))
            : action === "submitted" ? withdraw(record) : open(record)}>
          {isAdmin && record.submitterId !== access.userId ? "查看原记录" :
            action === "submitted" ? "撤回修改" : action === "returned" ? "修改并重提" : "继续填写"}</button>
      </div>
    </article>;
  const cost = (label: string, value: number | undefined) =>
    <div><dt>{label}</dt><dd>{value === undefined ? "—" : money(currency, value)}</dd></div>;
  const rateCost = (rate: number | undefined, base: number | undefined) =>
    rate === undefined || base === undefined ? undefined : rate * base;
  const lossBase = calculation?.netRevenueLocal;
  const losses = finance && lossBase !== undefined
    ? (finance.adRate + finance.returnLossRate + finance.storageLossRate +
      finance.promotionRate + finance.settlementLossRate) * lossBase +
      (calculation?.incomeTaxWithholdingNonrecoverableLocal ?? 0) : undefined;

  const draftList = <details className="site-drafts"><summary>我的草稿 · 从这里继续编辑（{ownDrafts.length}）</summary>
    {ownDrafts.length ? <div className="card-list">{ownDrafts.map((item) => queueCard(item, "draft"))}</div>
      : <p className="hint">暂无草稿；保存后会出现在这里。</p>}
  </details>;

  return <>
    {view === "multisite" && !hiddenEntry && <section className="multisite-panel entry-workspace" onPaste={handleImagePaste}>
      {data?.problems.length ? <p role="alert" className="alert error">{data.problems.join("；")}</p> : null}
      {!siteSelected ? <section className="form-panel">
        <h2>新建选品</h2>{sitePicker}
        <p className="hint">{preferenceLoaded ? "请先选择本次目标站点，成功保存新选品后会为你的账号记住。" : "正在读取你的站点记忆，也可以直接选择本次站点。"}</p>
        {entryFeedback}
        {draftList}
      </section> : <>
      <QuickOpportunity key={draft.site} site={draft.site} sitePicker={sitePicker}
        onCarry={carryQuick} tax={tax} finance={roughFinance}
        cards={cards}
        purchaseCny={quickPurchaseCny} onPurchaseChange={setQuickPurchaseCny}
        disabled={Boolean(editingId)} />
      <div className="multisite-grid" id="site-review-form">
        <div className="form-panel site-form-panel">
          <div className="panel-heading"><div className="panel-heading-title"><span className="panel-number">02</span>
            <h2>候选产品录入</h2></div>
            {editingId && <span className="site-editing-context">编辑中 · {MARKETPLACES[draft.site].label} · {currency}</span>}
          </div>
          <fieldset><legend>商品识别</legend><div className="field-grid two">
            <label>选品SKU<input value={draft.entitySku} aria-invalid={fieldInvalid("entitySku")}
              onChange={(event) => update("entitySku", event.target.value.toUpperCase())}
              placeholder="DEMO-ITEM-005" /></label>
            <label>产品中文名称 *<input value={draft.productName} aria-invalid={fieldInvalid("productName")}
              onChange={(event) => update("productName", event.target.value)} /></label>
            <label>产品英文名称<input value={draft.productNameEn} onChange={(event) => update("productNameEn", event.target.value)} /></label>
            <div className="category-field" onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) setCategoryOpen(false);
            }}><label htmlFor="site-product-category">品类 *</label>
              <div className="category-combobox">
                <input id="site-product-category" value={draft.category}
                  role="combobox" aria-expanded={categoryOpen} aria-controls="site-category-options"
                  aria-autocomplete="list" aria-invalid={fieldInvalid("category")}
                  onChange={(event) => { update("category", event.target.value); setCategoryFiltering(true); setCategoryOpen(true); }}
                  onKeyDown={(event) => {
                    if (event.key === "ArrowDown") { event.preventDefault(); setCategoryFiltering(false); setCategoryOpen(true); }
                    if (event.key === "Escape") setCategoryOpen(false);
                  }} />
                <button type="button" aria-label="展开品类选项" title="选择常用品类，也可直接输入"
                  aria-expanded={categoryOpen} aria-controls="site-category-options"
                  onClick={() => { setCategoryFiltering(false); setCategoryOpen((open) => !open); }}>
                  <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
                    <path d="m3 6 5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg></button>
              </div>
              {categoryOpen && <div className="category-options" id="site-category-options" role="listbox"
                aria-label="常用品类">
                {visibleCategories.length ? visibleCategories.map((item) =>
                  <button type="button" role="option" aria-selected={draft.category === item} key={item}
                    onClick={() => { update("category", item); setCategoryOpen(false); }}>{item}</button>)
                  : <span>没有匹配项，可直接输入新品类</span>}
              </div>}
            </div>
            <label className="span-two">参考竞品 ASIN<input value={draft.referenceAsins}
              onChange={(event) => update("referenceAsins", event.target.value)} placeholder="最多3个，逗号分隔" /></label>
            <label className="span-two">1688链接 *<input type="url" value={draft.sourceUrl} aria-invalid={fieldInvalid("sourceUrl")}
              onChange={(event) => update("sourceUrl", event.target.value)} /></label>
          </div><div className="site-image-row"><label>产品图片 *<input type="file" accept="image/*" aria-invalid={fieldInvalid("image")}
            onChange={(event) => setImage(event.target.files?.[0] ?? null)} />
            <small className="hint">支持 Ctrl+V / ⌘V 粘贴截图</small></label>
            <div className="site-image-preview" aria-label="产品图片预览" aria-invalid={fieldInvalid("image")}>
              {imagePreviewUrl || existingImageUrl ? <img src={imagePreviewUrl || existingImageUrl} alt="产品图片预览" />
                : <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="6" y="8" width="36" height="32" rx="5"/><circle cx="17" cy="18" r="3"/><path d="m10 34 10-11 7 7 5-5 6 9"/></svg>}
              <span>{image ? image.name : hasImage ? "已保存产品图片" : "未选择图片 · 选择或粘贴后预览"}</span>
              {image && <button type="button" className="text-button" onClick={() => setImage(null)}>移除</button>}
            </div></div></fieldset>
          <details className="multisite-optional-settings"><summary>日期与税类设置（默认已填）</summary>
            <div className="field-grid three">
              <label>预计开售日期<input type="date" value={draft.expectedSaleDate} aria-invalid={fieldInvalid("expectedSaleDate")}
                onChange={(event) => update("expectedSaleDate", event.target.value)} /></label>
              <label>适用地区<input value={draft.region} aria-invalid={fieldInvalid("region")}
                onChange={(event) => update("region", event.target.value)} /></label>
              <label>商品税类<select value={draft.taxClass} aria-invalid={fieldInvalid("taxClass")}
                onChange={(event) => update("taxClass", event.target.value as TaxClass)}>
                {["标准", "低税率", "零税率", "免税"].map((item) => <option key={item}>{item}</option>)}
              </select></label></div><p className="hint">特殊税类需人工核对。</p>
          </details>
          <SelectionRationaleInput value={draft.selectionRationale} invalid={fieldInvalid("selectionRationale")} onChange={value => update("selectionRationale", value)} />
          <fieldset><legend>本币售价、成本与包装 <small className="tax-rate-hint"
            title="选品测算口径；0% 表示税前标价不拆税，不代表消费者结账税率为零">销售税率 {finance?.approved && tax?.roughConfigured ? `${(tax.salesTaxRate * 100).toFixed(2)}%` : "未配置"}</small></legend><div className="field-grid three site-cost-grid">
            {numberField(`售价（${currency}）`, "listedPrice", true)}
            {numberField("采购单价（¥）", "purchaseCny", true)}
            {numberField("包装加工费（¥）", "processingCny")}
            {numberField("包装长（cm）", "lengthCm", true)}
            {numberField("包装宽（cm）", "widthCm", true)}
            {numberField("包装高（cm）", "heightCm", true)}
            {numberField("包装重量（g）", "unitWeightG", true)}
            {numberField(`FBA 费用（${currency}）`, "fbaFeeLocal", true)}
            <div className="site-fba-preview"><strong>FBA 预估（仅参考）</strong>
              <div role="status" title={cardEstimate.status === "matched"
                ? `${referenceInfo?.tier} · ${referenceInfo?.plan} · ${referenceInfo?.version}${draft.site === "AE" ? " · 未含适用 VAT" : ""}` : cardEstimate.reason}>
                {!previewReady ? "待填售价与包装信息"
                  : cardEstimate.status === "matched" ? money(currency, cardEstimate.fee ?? 0)
                    : draft.site === "AE" ? fbaReference.loading ? "预估计算中…"
                      : fbaReference.unavailable ? "预估暂不可用" : "需人工核价"
                      : ["MX", "SA"].includes(draft.site) ? "计费重待确认" : "需逐 SKU 报价"}
              </div>{draft.site === "AE" && fbaReference.unavailable &&
                <button type="button" className="text-button" onClick={fbaReference.retry}>重试预估</button>}</div>
          </div><p className="hint site-form-note">预估按普通商品基准费率计算，不匹配类目专属优惠；手填 FBA 费用用于利润，预估不会自动带入。无匹配档位、特殊商品或危险品需逐 SKU 核价。</p>
            {draft.site === "UK" && <p className="hint">英国低价费率仅用于售价不超过 £10 且符合尺寸重量条件的商品；售价超过 £10 按常规费率预估，旺季按适用日期计费。</p>}
            {draft.site === "AE" && <p className="hint">阿联酋 FBA 按完整销售包装的尺寸、实重、售价和预计开售日期自动预估，参考金额未含适用 VAT。利润仍以运营手填的 FBA 金额为准，预估不会自动带入；如需进一步确认，可核对亚马逊后台费用。</p>}
            {draft.site === "AE" && previewReady && !fbaReference.loading && cardEstimate.status !== "matched" &&
              <p className="hint" role="status">{cardEstimate.reason}</p>}
            {draft.site === "SA" && <p className="hint">沙特 FBA 按官方出库计费重核价，公开参考费不含适用 VAT；售价恰好 25 SAR 时需人工核对低价资格。制图需明确英文或阿拉伯语文案、校对人；阿语检查从右向左排版。</p>}
            {draft.site === "JP" && <p className="hint">日本 FBA 仅预估小型与标准薄件；参考费已含 10% 消费税，不再重复加税。其他尺寸档逐 SKU 核价。制图使用日语文案，核对单位、适配规格与警示内容。</p>}
            {cardError && draft.site !== "AE" && <p role="alert" className="alert warning">分档预估暂不可用：{cardError}</p>}
          </fieldset>
          <fieldset><legend>供应商可行性（可后补）</legend><div className="field-grid three">
            <label>供应商名称<input value={draft.supplierName} onChange={(event) => update("supplierName", event.target.value)} /></label>
            <label>MOQ<input type="number" min="0" step="1" value={draft.moq ?? ""}
              onChange={(event) => update("moq", event.target.value === "" ? null : Number(event.target.value))} /></label>
            <label>供应商配合度<select value={draft.supplierCooperation}
              onChange={(event) => update("supplierCooperation", event.target.value)}>
              <option value="">未评估</option><option>高</option><option>中</option><option>低</option></select></label>
            <label>打样及交期说明<textarea value={draft.sampleAndLeadTimeNotes}
              onChange={(event) => update("sampleAndLeadTimeNotes", event.target.value)} /></label>
            <label>阶梯报价说明<textarea value={draft.tieredQuotationNotes}
              onChange={(event) => update("tieredQuotationNotes", event.target.value)} /></label>
            <label>备注<textarea value={draft.notes} onChange={(event) => update("notes", event.target.value)} /></label>
          </div></fieldset>
          <fieldset><legend>侵权排查</legend><div className="field-grid three">
            {(["trademarkRisk", "patentRisk", "copyrightRisk"] as const).map((key, index) =>
              <label key={key}>{["商标", "专利", "版权"][index]}排查 *
                <select value={draft[key]} aria-invalid={fieldInvalid(key)}
                  onChange={(event) => update(key, event.target.value as SiteDraft[typeof key])}>
                  {["未排查", "无风险", "有风险", "待确认"].map((item) => <option key={item}>{item}</option>)}
                </select></label>)}
          </div><label>排查依据 *<textarea value={draft.complianceNotes} aria-invalid={fieldInvalid("complianceNotes")}
            onChange={(event) => update("complianceNotes", event.target.value)} /></label></fieldset>
          {draftList}
        </div>
        <aside className="calculator-panel multisite-summary" aria-label="测算结果与提审检查">
          <div className="panel-heading"><div className="panel-heading-title"><span className="panel-number">03</span>
            <h2>测算结果与提审检查</h2></div><b>{MARKETPLACES[draft.site].label} · {currency}</b></div>
          <div className="site-sizing-card"><div className="site-sizing-head"><strong>小件尺寸参考 · 内部优选</strong>
            {data?.sizeRules?.[draft.site]?.source && <a href={data.sizeRules[draft.site]!.source} target="_blank" rel="noopener noreferrer">亚马逊规则 ↗</a>}</div>
            <b>{sizing.result}</b><p>{sizing.detail}</p>
            {packing && <div className="site-packing-savings"><strong>配送费优化空间 · 分档试算</strong>
              <span>预估 {money(currency, packing.currentFee)} → {money(currency, packing.trialFee)} · 约省 {money(currency, packing.savingPerUnit)}/件</span>
              <small>防护允许时试包至 {packing.trialDimensions.join("×")} cm（{packing.reductions.join("、")}，重量不变）；实测后以亚马逊官方报价复核。</small></div>}
          </div>
          {!finance?.approved && <p className="alert warning">站点财务参数未启用：可以保存草稿，暂不计算利润或提交审核。</p>}
          <h3>单件成本摘要</h3><dl>
            {cost("售价", draft.listedPrice || undefined)}
            {cost("标价内销售税", calculation?.salesTaxLocal)}
            {cost("销售净收入", calculation?.netRevenueLocal)}
            {cost("采购＋包装（折本币）", calculation?.acquisitionLocal)}
            {cost("双清包税头程（折本币）", calculation?.headhaulLocal)}
            {cost("FBA 费用（手填总额）", draft.fbaFeeLocal || undefined)}
            {cost("平台佣金", calculation?.referralFeeEconomicLocal)}
          </dl>
          <details className="site-cost-details"><summary>营销、售后及其他损耗 <b>{losses === undefined ? "—" : money(currency, losses)}</b></summary><dl>
            {cost("广告营销", rateCost(finance?.adRate, lossBase))}
            {cost("售后退货", rateCost(finance?.returnLossRate, lossBase))}
            {cost("仓储损失", rateCost(finance?.storageLossRate, lossBase))}
            {cost("促销费", rateCost(finance?.promotionRate, lossBase))}
            {cost("汇兑损耗", rateCost(finance?.settlementLossRate, lossBase))}
            {cost("不可回收所得税预提", calculation?.incomeTaxWithholdingNonrecoverableLocal)}
          </dl></details>
          <details className="site-cost-details"><summary>税费及费用明细</summary><dl>
            {cost("结账另收销售税（不计收入）", calculation?.checkoutTaxLocal)}
            {cost("平台收费税总额", calculation?.platformFeeTaxLocal)}
            {cost("可抵扣平台收费税", calculation?.platformFeeTaxRecoverableLocal)}
            {cost("不可抵扣平台收费税", calculation?.platformFeeTaxNonrecoverableLocal)}
          </dl></details>
          <div className="site-result-hero"><span>利润 / 件</span>
            <strong>{calculation ? money(currency, calculation.contributionLocal) : "—"}</strong>
            <small>毛利率 {calculation ? `${(calculation.contributionRate * 100).toFixed(2)}%` : "—"} · 前置资金 ROI {calculation ? `${(calculation.upfrontCapitalRoi * 100).toFixed(2)}%` : "—"}</small>
          </div>
          <h3>提审检查</h3>
          <p className="site-gate-list">点击「{selfExempt ? "提交选品" : "提交审核"}」时检查必填项与测算条件。</p>
          {pendingEditing && <p role="status" className="site-gate-list">{freshPending
            ? "Base 公式回算中，结果就绪后自动完成提审。"
            : "公式回算等待超时，可检查数据后再次提交；已填写内容仍保留。"}</p>}
          {entryFeedback}
          <div className="site-submit-actions"><button className="secondary-button" disabled={busy || !preferenceLoaded || freshPending || !access.canSubmit || Boolean(data?.problems.length)} onClick={save}>保存草稿</button>
            <button className="primary-button" disabled={busy || !preferenceLoaded || freshPending || !access.canSubmit || Boolean(data?.problems.length)} onClick={requestSubmit}>{selfExempt ? "提交选品（免审核）" : "提交审核"}</button></div>
          {editingId && <button className="text-button" disabled={busy} onClick={startNew}>新建另一站点</button>}
          <p className="hint">草稿从「我的草稿」继续编辑；小件尺寸仅供优选参考，不是提审门槛。</p>
        </aside>
      </div></>}
    </section>}
    {validationOpen && view === "multisite" && <div className="site-validation-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setValidationOpen(false);
    }}><div className="site-validation-dialog" role="dialog" aria-modal="true" aria-labelledby="site-validation-title">
      <div className="site-validation-heading"><h2 id="site-validation-title">{actionError ? "提交审核未完成" : "提交审核前请补齐"}</h2>
        <button id="site-validation-close" type="button" className="text-button" aria-label="关闭校验弹窗"
          onClick={() => setValidationOpen(false)}>×</button></div>
      <p>{actionError ? "已填写内容和产品图片会保留在草稿中，请按提示处理后重试。" : "对应录入框已标红。补齐后可再次提交审核。"}</p>
      <ul>{(actionError ? [actionError] : problems).map((problem) => <li key={problem}>{problem}</li>)}</ul>
      <div className="site-validation-actions"><button className="primary-button" type="button"
        onClick={() => setValidationOpen(false)}>返回填写</button></div>
    </div></div>}
    {view === "submitted" && <section className="queue-panel site-work-queue"><div className="mine-heading"><div>
      <p className="section-kicker">MY SUBMISSIONS</p><h2>我的提交</h2>
      <p>{isAdmin ? "查看全部正在等待主管审核的产品。" : "只显示本人正在等待主管审核的产品。"}</p></div></div>
      {message && <p role="status" className="alert">{message}</p>}
      <div className="submission-search"><label htmlFor="unified-submission-search">查找我的提交</label>
        <div className="submission-search-controls"><input id="unified-submission-search" value={submissionQuery}
          onChange={(event) => setSubmissionQuery(event.target.value)} placeholder="输入选品SKU、中文品名或参考 ASIN" />
          {submissionQuery && <button type="button" className="text-button" onClick={() => setSubmissionQuery("")}>清空搜索</button>}</div>
        <small>{query ? `找到 ${visibleSubmitted.length + visibleLegacySubmitted.length} 条匹配记录`
          : `共 ${ownSubmitted.length + (legacyQueues?.submitted.length ?? 0)} 条待审批记录`}</small></div>
      {visibleSubmitted.length + visibleLegacySubmitted.length ? <div className="card-list">
        {visibleSubmitted.map((item) => queueCard(item, "submitted"))}
        {visibleLegacySubmitted.map((item) => <div key={item.id}>{item.node}</div>)}
      </div> : <div className="empty-state"><span>✓</span><h3>{query ? "没有匹配的提交记录" : "当前没有待审批记录"}</h3></div>}
    </section>}
    {view === "returned" && <section className="queue-panel site-work-queue"><div className="mine-heading"><div>
      <p className="section-kicker">RETURNED ITEMS</p><h2>我的打回</h2>
      <p>查看主管意见，修改后重新提交。</p></div></div>
      {message && <p role="status" className="alert">{message}</p>}
      {ownReturned.length + (legacyQueues?.returned.length ?? 0) ? <div className="card-list">
        {ownReturned.map((item) => queueCard(item, "returned"))}
        {legacyQueues?.returned.map((item) => <div key={item.id}>{item.node}</div>)}
      </div> : <div className="empty-state"><span>✓</span><h3>当前没有需要修改的记录</h3></div>}
    </section>}
    {view === "review" && access.canReview && <section className="queue-panel site-work-queue"><div className="mine-heading review-heading"><div>
      <p className="section-kicker">REVIEW QUEUE</p><h2>主管审核</h2>
      <p className="review-summary-inline" aria-label="待审核汇总">
        <strong>待审 {reviewSummary.count} 条</strong>
        <span className="review-sale-range" title={saleRanges || "当前无待审售价"}>售价 {saleRanges || "—"}</span>
        <span>采购价 {rangeText(reviewSummary.purchase, (value) => `¥${value.toFixed(2)}`)}</span>
        <span title="按现行完整成本测算毛利率统计">毛利率 {rangeText(reviewSummary.contributionRate,
          (value) => `${(value * 100).toFixed(2)}%`)}</span>
      </p></div>
      <div className="review-filters"><label>运营<select aria-label="按运营筛选" value={reviewOperator}
        onChange={(event) => { setReviewOperator(event.target.value); setReviewOperatorName(event.target.selectedOptions[0]?.textContent || ""); }}>
        <option value="">全部运营</option>{reviewOperators.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select></label><label>站点<select aria-label="按站点筛选" value={reviewSite}
        onChange={(event) => setReviewSite(event.target.value as Marketplace | "")}>
        <option value="">全部站点</option>{sites.map(([code, site]) =>
          <option key={code} value={code}>{site.label}</option>)}
      </select></label></div></div>
      {message && <p role="status" className="alert">{message}</p>}
      {(legacyQueues?.excludedPendingCount ?? 0) > 0 && <p className="queue-warning">有 {legacyQueues?.excludedPendingCount} 条记录缺少有效提交校验，未进入审核队列。</p>}
      {visiblePending.length + visibleLegacyReview.length ? <div className="review-grid">{visiblePending.map((item) => {
        const siteCurrency = MARKETPLACES[item.draft.site].currency;
        const ip = intellectualPropertyPreview(item.draft);
        const attention = reviewAttention(item);
        return <article className="review-card site-review-card review-summary-card" key={item.id}><div className="card-left">
          <div className="row-top"><SiteReviewThumb record={item} viewerId={access.userId} /><div className="ident"><p className="record-code">{item.draft.entitySku || item.number}
            <span className="cat">{MARKETPLACES[item.draft.site].label} · {siteCurrency}</span></p>
            <h3>{item.draft.productName}</h3><span className="sub">{item.group || "运营小组待确认"} · <b>{item.submitterName || "运营"}</b> · {item.draft.category} · {reviewDate(item.submittedAt)}</span></div>
            <ReferenceAsins value={item.draft.referenceAsins} site={item.draft.site} /></div>
          <div className="queue-profit"><span>售价 {money(siteCurrency, item.draft.listedPrice)}</span><span>采购 ¥{item.draft.purchaseCny.toFixed(2)}</span>
            <b className={item.profit && item.profit.rate < .25 ? "below-line" : undefined}>毛利率 {item.profit ? `${(item.profit.rate * 100).toFixed(2)}%` : "待计算"}</b>
            <span>利润/件 {item.profit ? money(siteCurrency, item.profit.contribution) : "待计算"}</span>
            <span>前置资金 ROI {item.profit ? `${(item.profit.roi * 100).toFixed(2)}%` : "待计算"}</span>
            <span>回款占比 {typeof item.profit?.payoutRate === "number" ? `${(item.profit.payoutRate * 100).toFixed(2)}%` : "待计算"}</span></div>
          <SelectionRationale value={item.draft.selectionRationale} compact />
          <div className="review-summary-notes"><span className={`ip-preview ${ip.tone}`}>{ip.text}</span>{attention.length > 0 && <span>{attention.join("；")}</span>}</div>
        </div><div className="actions"><button type="button" className="approve-button" disabled={busy} onClick={() => openReview(item)}>查看评审详情</button></div></article>;
      })}{visibleLegacyReview.map((item) => <div key={item.id}>{item.node}</div>)}</div>
        : <div className="empty-state"><span>✓</span><h3>{reviewOperator || reviewSite ? "当前筛选无待审核记录" : "当前没有待审核记录"}</h3></div>}
    </section>}
    {view === "review" && access.canReview && !reviewDetail && reviewResult && <div className="review-completion-notice" role="status">
      <div><strong>{reviewResult.title}</strong><span>{reviewResult.detail}</span></div>
      <button type="button" className="text-button" aria-label="关闭审批结果提示" onClick={() => setReviewResult(null)}>×</button>
    </div>}
    {view === "review" && access.canReview && reviewDetail && <ReviewDetail record={reviewDetail}
      viewerId={access.userId} sizeRules={data?.sizeRules ?? {}} comment={comments[reviewDetail.id] ?? ""}
      quote={quotes[reviewDetail.id] ?? storedReviewQuote(reviewDetail)} busy={busy} completed={reviewCompleted}
      message={message} position={detailIndex + 1} count={reviewQueueIds.length}
      previous={detailNeighbor(-1)} next={detailNeighbor(1)} overrideReasons={approvalOverride?.id === reviewDetail.id ? approvalOverride.reasons : undefined}
      onComment={value => setComments(current => ({ ...current, [reviewDetail.id]: value }))}
      onQuote={value => setQuotes(current => ({ ...current, [reviewDetail.id]: value }))}
      onNavigate={item => openReview(item, true)}
      onClose={() => { if (!busy) { setReviewDetail(null); setApprovalOverride(null); } }}
      onRefresh={() => void refreshReview()}
      onOriginal={() => void import("./repository").then(({ openSiteRecord }) => openSiteRecord(reviewDetail.id))
        .catch(error => setMessage(`打开原记录失败：${error instanceof Error ? error.message : String(error)}`))}
      onDecide={(action, override) => decide(reviewDetail, action, override)} onCancelOverride={() => setApprovalOverride(null)} />}
  </>;
}
