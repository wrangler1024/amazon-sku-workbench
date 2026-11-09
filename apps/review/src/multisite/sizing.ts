import type { Marketplace } from "./domain";
import { estimateFbaCard, type CardEstimate, type CardRequest, type FbaCard } from "./rateCards";

export type SmallTier = { name: string; dimensions: [number, number, number]; weightG: number | null };
export type SmallSizeRuleSet = { source: string; tiers: SmallTier[] };
export type SmallSizeRules = Partial<Record<Marketplace, SmallSizeRuleSet>>;

export function smallSizeAdvice(rules: SmallSizeRules, site: Marketplace, input: number[], weightG: number) {
  const rule = rules[site];
  if (!rule?.tiers.length) return { result: "本站尺寸规则待配置",
    detail: "请由管理员维护 FBA尺寸档规则；尺寸参考不影响保存草稿。", target: null };
  if (input.length !== 3 || [...input, weightG].some((value) => !Number.isFinite(value) || value <= 0))
    return { result: "待填写尺寸重量", detail: "以封袋、缓冲和贴标后的单件可售包装为准，不使用入仓外箱尺寸。", target: null };
  const dims = [...input].sort((a, b) => b - a);
  const matched = rule.tiers.find((tier) => dims.every((side, i) => side <= tier.dimensions[i]) &&
    (tier.weightG === null || weightG <= tier.weightG));
  if (matched) return { result: `符合${matched.name}参考范围`,
    detail: `本档包装上限 ${matched.dimensions.join("×")} cm${matched.weightG === null ? "" : `、${matched.weightG} g`}。${site === "SA" ? "重量上限按亚马逊出库计费重量，当前实重比较仅作初筛。" : ""}备货时抽检成品包装，避免加包材后跨档；仅供主管参考，不影响提审或审核通过。`, target: matched };
  const target = rule.tiers[rule.tiers.length - 1];
  const names = ["最长边", "次长边", "最短边"];
  const excess = dims.flatMap((side, i) => side > target.dimensions[i]
    ? [`${names[i]}超 ${Number((side - target.dimensions[i]).toFixed(2))} cm`] : []);
  if (target.weightG !== null && weightG > target.weightG)
    excess.push(`重量超 ${Number((weightG - target.weightG).toFixed(2))} g`);
  return { result: "超出本站小件优选范围",
    detail: `${excess.join("、")}。防护允许时可试包至 ≤${target.dimensions.join("×")} cm${target.weightG === null ? "" : `、${target.weightG} g`}，再以实测和官方报价确认；超出本身不阻止提审或审核通过。`, target };
}

export function packagingTrial(request: CardRequest, rules: SmallSizeRules) {
  const dims = [...request.dimensionsCm].sort((a, b) => b - a);
  const target = rules[request.site]?.tiers.find((tier) =>
    (tier.weightG === null || request.unitWeightG <= tier.weightG) &&
    dims.some((side, i) => side > tier.dimensions[i]) &&
    dims.every((side, i) => side - tier.dimensions[i] <= 2));
  if (!target) return null;
  const trialDims = dims.map((side, i) => Math.min(side, target.dimensions[i])) as [number, number, number];
  return trialDims;
}

export function comparePackaging(request: CardRequest, trialDims: [number, number, number],
  current: CardEstimate, trial: CardEstimate) {
  if (current.status !== "matched" || !current.fee || trial.status !== "matched" || !trial.fee) return null;
  const currentCents = Math.round(current.fee * 100);
  const trialCents = Math.round(trial.fee * 100);
  if (trialCents >= currentCents) return null;
  const names = ["最长边", "次长边", "最短边"];
  const dims = [...request.dimensionsCm].sort((a, b) => b - a);
  const reductions = dims.flatMap((side, i) => side > trialDims[i]
    ? [`${names[i]}减 ${Number((side - trialDims[i]).toFixed(2))} cm`] : []);
  return { currentFee: currentCents / 100, trialFee: trialCents / 100,
    savingPerUnit: (currentCents - trialCents) / 100,
    trialDimensions: trialDims, reductions };
}

export function packagingOpportunity(cards: FbaCard[], request: CardRequest, rules: SmallSizeRules) {
  const trialDims = packagingTrial(request, rules);
  return trialDims ? comparePackaging(request, trialDims, estimateFbaCard(cards, request),
    estimateFbaCard(cards, { ...request, dimensionsCm: trialDims })) : null;
}
