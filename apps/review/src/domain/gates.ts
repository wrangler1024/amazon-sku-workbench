import { selectionRationaleProblem } from "./selectionRationale";
import type {
  CalculationResult,
  CalculatorInput,
  GateResult,
  RateSnapshot,
} from "./types";

function filled(value: string): boolean {
  return value.trim().length > 0;
}

export function evaluateSubmissionGate(
  input: CalculatorInput,
  calculation: CalculationResult,
  rates: RateSnapshot,
  hasImage: boolean,
  requireRationale = true,
): GateResult {
  const basicReady =
    filled(input.productNameCn) &&
    filled(input.category) &&
    filled(input.sourceUrl) &&
    hasImage;
  const costReady =
    input.purchasePriceCny > 0 &&
    input.processingCostCny >= 0 &&
    input.lengthCm > 0 &&
    input.widthCm > 0 &&
    input.heightCm > 0 &&
    input.weightG > 0 &&
    input.targetPriceCad > 0;
  const complianceReady =
    input.trademarkRisk === "无风险" &&
    input.patentRisk === "无风险" &&
    input.copyrightRisk === "无风险" &&
    filled(input.complianceNotes);
  const contributionReady =
    calculation.completeContributionRate + 1e-9 >=
    rates.targetContributionRate;

  const rationaleProblem = selectionRationaleProblem(input.selectionRationale, requireRationale);
  const items = [
    { label: "选品说明完整", passed: !rationaleProblem, detail: rationaleProblem || "选品说明已填写" },
    {
      label: "基础信息完整",
      passed: basicReady,
      detail: basicReady
        ? "名称、品类、图片和1688链接已填写"
        : "需填写中文名称、品类、产品图片和1688链接",
    },
    {
      label: "成本与包装数据完整",
      passed: costReady,
      detail: costReady
        ? "采购、包装、尺寸、重量和售价有效"
        : "采购价、尺寸、重量和售价必须大于0，包装费不得为负数",
    },
    {
      label: "加拿大FBA费率有效",
      passed: calculation.fbaValid,
      detail: calculation.fbaRateLabel,
    },
    {
      label: "侵权排查通过",
      passed: complianceReady,
      detail: complianceReady
        ? "商标、专利、版权均为无风险"
        : "三项排查必须均为无风险，并填写排查依据",
    },
    {
      label: "毛利率达标",
      passed: contributionReady,
      detail: `${(calculation.completeContributionRate * 100).toFixed(2)}% / 目标 ${(rates.targetContributionRate * 100).toFixed(2)}%`,
    },
  ];
  return { passed: items.every((item) => item.passed), items };
}
