import type { FbaCard } from "./rateCards";

// Only the announced 2026 peak window is used; do not infer future annual windows.
// Dates follow the Base's calendar-date convention, also used by expectedSaleDate.
const start2026 = Date.parse("2026-01-15T00:00:00+08:00");
const surchargeStart = Date.parse("2026-04-17T00:00:00+08:00");
const peakStart = Date.parse("2026-10-15T00:00:00+08:00");
const peakEnd = Date.parse("2027-01-15T00:00:00+08:00");

export function canadaFbaCards(base: FbaCard, peakFee: number): FbaCard[] {
  if (!Number.isFinite(peakFee) || peakFee <= 0)
    throw new Error("加拿大FBA费率表的旺季配送费未完整填写");
  const periods = [
    { suffix: "2026-before-surcharge", from: start2026, until: surchargeStart,
      plan: "常规", fee: base.baseFee, surcharge: 0 },
    { suffix: "2026-regular", from: surchargeStart, until: peakStart,
      plan: "常规", fee: base.baseFee, surcharge: base.surchargeRate },
    { suffix: "2026-peak", from: peakStart, until: peakEnd,
      plan: "旺季", fee: peakFee, surcharge: base.surchargeRate },
    { suffix: "after-2026-peak", from: peakEnd, until: null,
      plan: "常规", fee: base.baseFee, surcharge: base.surchargeRate },
  ];
  return periods.map(period => ({ ...base, key: `${base.key}:${period.suffix}`,
    plan: period.plan, baseFee: period.fee, surchargeRate: period.surcharge,
    effectiveFrom: period.from, effectiveUntil: period.until,
    volumeDivisor: 5000, shippingWeightStepG: 100 }));
}
