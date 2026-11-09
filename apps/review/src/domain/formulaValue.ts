function cellText(value: unknown): string {
  if (typeof value === "number" || typeof value === "string") return String(value);
  if (Array.isArray(value)) return value.map(cellText).join("");
  if (value && typeof value === "object") {
    const item = value as { text?: unknown; value?: unknown };
    if (item.text !== undefined) return cellText(item.text);
    if (item.value !== undefined) return cellText(item.value);
  }
  return "";
}

export function parseFormulaNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  const text = cellText(value)
    .trim()
    .replace(/,/g, "")
    .replace(/^(?:CAD|CNY|MXN|GBP|USD|AUD|CA\$|AU\$|A\$|MX\$|US\$|¥|\$|£)\s*/i, "")
    .replace(/\s*(?:CAD|CNY|MXN|GBP|USD|AUD)$/i, "");
  const percent = text.endsWith("%");
  const numeric = percent ? text.slice(0, -1).trim() : text;
  if (!/^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$/.test(numeric)) return null;
  const parsed = Number(numeric);
  return Number.isFinite(parsed) ? parsed / (percent ? 100 : 1) : null;
}

export function displayedNumberTolerance(value: unknown, minimum: number): number {
  const text = cellText(value).trim();
  const numeric = text.replace(/,/g, "").replace(/%$/, "");
  const decimals = numeric.match(/\.([0-9]+)(?:\s*(?:CAD|CNY))?$/)?.[1].length;
  // A numeric SDK value with no decimals may be an exact integer, not a rounded display.
  if (decimals === undefined && !text.endsWith("%")) return minimum;
  const displayStep = 10 ** -(decimals ?? 0) / (text.endsWith("%") ? 100 : 1);
  return Math.max(minimum, displayStep / 2 + 1e-9);
}
