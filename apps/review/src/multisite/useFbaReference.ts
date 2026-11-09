import { useEffect, useState } from "react";
import { aeInputProblem, fetchAeEstimates } from "./aeFbaService";
import { estimateFbaCard, type CardEstimate, type CardRequest, type FbaCard } from "./rateCards";
import { comparePackaging, packagingOpportunity, packagingTrial, type SmallSizeRules } from "./sizing";

type RemoteState = { key: string; estimates: CardEstimate[]; unavailable: boolean };
export function useFbaReference(cards: FbaCard[], request: CardRequest, saleDate: string, rules: SmallSizeRules = {}) {
  const trial = packagingTrial(request, rules);
  const problem = aeInputProblem(request, saleDate);
  // Use input values, not object identities or wall-clock timestamps, as request dependencies.
  const key = request.site === "AE" && !problem ? JSON.stringify({ date: saleDate,
    requests: [request, ...(trial ? [{ ...request, dimensionsCm: trial }] : [])].map(r => ({
      site: r.site, category: r.category, price: r.price, dimensionsCm: r.dimensionsCm,
      unitWeightG: r.unitWeightG, saleDate: 0,
    })) }) : "";
  const [remote, setRemote] = useState<RemoteState | null>(null);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!key) { setRemote(null); return; }
    let active = true;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      const input = JSON.parse(key) as { date: string; requests: CardRequest[] };
      void fetchAeEstimates(input.requests, input.date, controller.signal).then(estimates => {
        if (active) setRemote({ key, estimates, unavailable: false });
      }).catch(() => {
        if (active) setRemote({ key, estimates: [{ status: "quote_required",
          reason: "自动预估暂不可用，可继续手填 FBA 费用；稍后重试" }], unavailable: true });
      });
    }, 350);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [key, retry]);

  if (request.site !== "AE") return { estimate: estimateFbaCard(cards, request), loading: false, unavailable: false,
    packing: packagingOpportunity(cards, request, rules), retry: () => {} };
  const current = remote?.key === key && key ? remote : null;
  const estimate: CardEstimate = !key ? { status: "quote_required", reason: problem ?? "待填售价与包装信息" }
    : current?.estimates[0] ?? { status: "quote_required", reason: "自动预估计算中…" };
  return { estimate, loading: Boolean(key && !current), unavailable: current?.unavailable ?? false,
    packing: trial && current?.estimates[1] ? comparePackaging(request, trial, estimate, current.estimates[1]) : null,
    retry: () => { setRemote(null); setRetry(value => value + 1); } };
}
