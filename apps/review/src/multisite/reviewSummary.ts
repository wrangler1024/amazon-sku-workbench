import type { Marketplace } from "./domain";

export interface ReviewSummaryItem {
  site: Marketplace;
  listedPrice: number;
  purchaseCny: number;
  contributionRate: number | null;
}

export interface NumberRange { min: number; max: number }

const range = (values: number[]): NumberRange | null => {
  const valid = values.filter(Number.isFinite);
  return valid.length ? { min: Math.min(...valid), max: Math.max(...valid) } : null;
};

export function summarizePendingReviews(items: ReviewSummaryItem[]) {
  const prices = new Map<Marketplace, NumberRange>();
  for (const item of items) {
    if (!Number.isFinite(item.listedPrice) || item.listedPrice <= 0) continue;
    const current = prices.get(item.site);
    prices.set(item.site, current
      ? { min: Math.min(current.min, item.listedPrice), max: Math.max(current.max, item.listedPrice) }
      : { min: item.listedPrice, max: item.listedPrice });
  }
  return { count: items.length, prices,
    purchase: range(items.map((item) => item.purchaseCny).filter((value) => value >= 0)),
    contributionRate: range(items.map((item) => item.contributionRate)
      .filter((value): value is number => value !== null)),
  };
}
