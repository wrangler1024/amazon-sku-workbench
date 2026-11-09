export function intellectualPropertyPreview(risks: {
  trademarkRisk: string;
  patentRisk: string;
  copyrightRisk: string;
}) {
  const items = [["商标", risks.trademarkRisk], ["专利", risks.patentRisk],
    ["版权", risks.copyrightRisk]] as const;
  const marker = (value: string) => value === "无风险" ? "✓" : value === "有风险" ? "风险" : "待核";
  return {
    text: `侵权规避 ${items.map(([name, value]) => `${name}${marker(value)}`).join(" ")}`,
    title: items.map(([name, value]) => `${name}：${value || "未排查"}`).join(" · "),
    tone: items.some(([, value]) => value === "有风险") ? "risk" :
      items.some(([, value]) => value !== "无风险") ? "pending" : "clear",
  };
}
