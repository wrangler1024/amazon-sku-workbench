import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { cachedImageUrl, invalidateImageUrl, resolveImageUrl } from "../domain/imageUrlCache";
import type { SiteReview } from "./repository";

/** Match the legacy/SHEIN review thumbnail, including hover and pinned previews. */
export default function SiteReviewThumb({ record, viewerId }: { record: SiteReview; viewerId: string }) {
  const [url, setUrl] = useState<string | null>(() =>
    cachedImageUrl(viewerId, "SKU选品库", record.id, record.imageToken));
  const [failed, setFailed] = useState(false);
  const [visible, setVisible] = useState(false);
  const [pinned, setPinned] = useState(false);
  const [position, setPosition] = useState({ left: 8, top: 8 });
  const trigger = useRef<HTMLButtonElement | null>(null);
  const timer = useRef<number | null>(null);
  const retried = useRef(false);
  const clearTimer = () => { if (timer.current !== null) window.clearTimeout(timer.current); timer.current = null; };
  const positionPreview = useCallback(() => {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const width = Math.min(300, window.innerWidth - 16);
    const height = Math.min(308, window.innerHeight - 16);
    setPosition({
      left: Math.max(8, rect.left >= width + 20 ? rect.left - width - 12 : Math.min(rect.right + 12, window.innerWidth - width - 8)),
      top: Math.max(8, Math.min(rect.top - 8, window.innerHeight - height - 8)),
    });
  }, []);
  const show = () => { if (url) { positionPreview(); setVisible(true); } };
  const close = () => { clearTimer(); setPinned(false); setVisible(false); };
  useEffect(() => {
    let active = true;
    setUrl(cachedImageUrl(viewerId, "SKU选品库", record.id, record.imageToken));
    setFailed(false); setVisible(false); setPinned(false); retried.current = false;
    if (!record.imageToken) { setFailed(true); return; }
    void resolveImageUrl(viewerId, "SKU选品库", record.id, record.imageToken,
      async () => (await import("./repository")).getSiteImageUrl(record.id, record.imageToken)).then((value) => {
      if (!active) return;
      if (value) setUrl(value); else setFailed(true);
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [viewerId, record.id, record.imageToken]);
  const imageFailed = () => {
    close();
    invalidateImageUrl(viewerId, "SKU选品库", record.id, record.imageToken);
    if (retried.current) { setFailed(true); setUrl(null); return; }
    retried.current = true;
    setUrl(null);
    void resolveImageUrl(viewerId, "SKU选品库", record.id, record.imageToken,
      async () => (await import("./repository")).getSiteImageUrl(record.id, record.imageToken), true)
      .then((fresh) => { if (fresh && fresh !== url) setUrl(fresh); else setFailed(true); })
      .catch(() => setFailed(true));
  };
  useEffect(() => () => clearTimer(), []);
  useEffect(() => {
    if (!visible) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("resize", positionPreview);
    window.addEventListener("scroll", positionPreview, true);
    document.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("resize", positionPreview);
      window.removeEventListener("scroll", positionPreview, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [visible, positionPreview]);
  const label = record.draft.productName || "未命名产品";
  return <>
    <button ref={trigger} type="button" className="review-image-trigger"
      aria-label={`预览产品图片：${label}`} aria-haspopup="dialog" aria-expanded={visible}
      title="悬停放大图片，点击固定预览"
      onPointerEnter={(event) => { if (event.pointerType === "touch" || pinned) return;
        clearTimer(); timer.current = window.setTimeout(show, 150); }}
      onPointerLeave={() => { clearTimer(); if (!pinned) setVisible(false); }}
      onFocus={show} onBlur={() => { if (!pinned) setVisible(false); }}
      onClick={() => { clearTimer(); if (pinned) close(); else { show(); setPinned(true); } }}>
      {url ? <img className="review-thumb" src={url} alt={label} loading="lazy" decoding="async"
        onError={imageFailed} />
        : <span className="review-thumb empty">{record.imageToken ? failed ? "加载失败" : "加载中…" : "无图片"}</span>}
    </button>
    {visible && url && createPortal(<>
      {pinned && <div className="review-image-backdrop" onClick={close} />}
      <div className={`review-image-popover${pinned ? " pinned" : ""}`} style={position}
        role="dialog" aria-label={`产品图片预览：${label}`}>
        <img src={url} alt={label} />
        <div className="review-image-caption"><span>{record.draft.entitySku || record.number} · {label}</span>
          {pinned && <button type="button" onClick={close} aria-label="关闭产品图片预览">✕</button>}</div>
      </div>
    </>, document.body)}
  </>;
}
