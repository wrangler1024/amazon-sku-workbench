import { useId } from "react";
import { normalizeSelectionRationale, SELECTION_RATIONALE_HINT, SELECTION_RATIONALE_LIMIT } from "../domain/selectionRationale";

export function SelectionRationaleInput({ value, invalid, onChange }: {
  value: string; invalid?: boolean; onChange(value: string): void;
}) {
  const hintId = useId();
  return <fieldset className="selection-rationale-input"><legend>选品说明</legend>
    <label><textarea aria-label="选品说明" rows={3}
      aria-invalid={invalid} aria-describedby={hintId} value={value}
      placeholder={SELECTION_RATIONALE_HINT} onChange={event => onChange(event.target.value)} /></label>
    <small id={hintId} className="selection-rationale-hint">提交审核时必填 · {Array.from(normalizeSelectionRationale(value)).length} / {SELECTION_RATIONALE_LIMIT}</small>
  </fieldset>;
}

export default function SelectionRationale({ value, compact = false, emptyText = "未填写选品说明" }: {
  value?: string; compact?: boolean; emptyText?: string;
}) {
  const text = normalizeSelectionRationale(value);
  if (compact) return <details className="selection-rationale-preview"><summary>
    <span>选品说明</span><span className="selection-rationale-excerpt">{text || emptyText}</span>
  </summary>{text && <p className="selection-rationale-text">{text}</p>}</details>;
  return <section className="selection-rationale-full"><h3>选品说明</h3>
    <p className="selection-rationale-text">{text || emptyText}</p></section>;
}
