import type {
  CalculationResult,
  CalculatorInput,
  FbaRateTier,
  FbaSizeTier,
  RateSnapshot,
} from "./types";

const EPSILON = 1e-9;

function finiteNonNegative(value: number): number {
  return Number.isFinite(value) && value >= 0 ? value : 0;
}

export function classifyFbaSize(input: CalculatorInput): FbaSizeTier {
  const dimensions = [input.lengthCm, input.widthCm, input.heightCm]
    .map(finiteNonNegative)
    .sort((left, right) => right - left);
  const [longest, middle, shortest] = dimensions;
  const weight = finiteNonNegative(input.weightG);
  if (
    longest <= 38 + EPSILON &&
    middle <= 27 + EPSILON &&
    shortest <= 2 + EPSILON &&
    weight <= 500 + EPSILON &&
    weight > 0
  ) {
    return "信封Envelope";
  }
  if (
    longest <= 45 + EPSILON &&
    middle <= 35 + EPSILON &&
    shortest <= 20 + EPSILON &&
    weight <= 9_000 + EPSILON &&
    weight > 0
  ) {
    return "标准Standard";
  }
  return "不支持测算";
}

export function volumeWeightKg(input: CalculatorInput): number {
  return (
    finiteNonNegative(input.lengthCm) *
    finiteNonNegative(input.widthCm) *
    finiteNonNegative(input.heightCm) /
    6_000
  );
}

export function chargeableWeightKg(input: CalculatorInput): number {
  return Math.max(finiteNonNegative(input.weightG) / 1_000, volumeWeightKg(input));
}

function findFbaBaseFee(
  sizeTier: FbaSizeTier,
  chargeableKg: number,
  tiers: FbaRateTier[],
): { fee: number; valid: boolean; label: string } {
  if (sizeTier === "不支持测算" || chargeableKg <= 0) {
    return { fee: 0, valid: false, label: "尺寸或重量超出一期支持范围" };
  }
  const chargeableG = chargeableKg * 1_000;
  if (sizeTier === "标准Standard" && chargeableG > 1_500 + EPSILON) {
    if (chargeableG > 9_000 + EPSILON) {
      return { fee: 0, valid: false, label: "计费重量超过9kg" };
    }
    const increments = Math.ceil((chargeableG - 1_500 - EPSILON) / 100);
    return {
      fee: 10.32 + increments * 0.09,
      valid: true,
      label: `1500g以上，每100g加CAD 0.09（${increments}档）`,
    };
  }
  const tier = tiers.find(
    (item) =>
      item.sizeTier === sizeTier &&
      chargeableG > item.lowerWeightG + EPSILON &&
      chargeableG <= item.upperWeightG + EPSILON,
  );
  return tier
    ? { fee: tier.feeCad, valid: tier.feeCad > 0, label: tier.label }
    : { fee: 0, valid: false, label: "未匹配到加拿大FBA费率" };
}

export function calculateSelection(
  input: CalculatorInput,
  rates: RateSnapshot,
  tiers: FbaRateTier[],
): CalculationResult {
  const sizeTier = classifyFbaSize(input);
  const volumeKg = volumeWeightKg(input);
  const chargeableKg = chargeableWeightKg(input);
  const fba = findFbaBaseFee(sizeTier, chargeableKg, tiers);
  const discount =
    fba.valid && input.targetPriceCad < rates.lowPriceThresholdCad
      ? Math.min(rates.lowPriceDiscountCad, fba.fee)
      : 0;
  const discountedFee = Math.max(fba.fee - discount, 0);
  const surcharge = discountedFee * rates.fbaSurchargeRate;
  const finalFba = discountedFee + surcharge;
  const procurement =
    (finiteNonNegative(input.purchasePriceCny) +
      finiteNonNegative(input.processingCostCny)) *
    rates.cadPerCny;
  const headhaul =
    chargeableKg * rates.headhaulCnyPerKg * rates.cadPerCny;
  const price = finiteNonNegative(input.targetPriceCad);
  const estimatedProfit =
    price -
    procurement -
    headhaul -
    finalFba -
    price * (rates.referralFeeRate + rates.adRate);
  const additionalLossRate =
    rates.returnLossRate +
    rates.storageRemovalDefectRate +
    rates.promotionRate +
    rates.fxSettlementLossRate;
  const completeContribution = estimatedProfit - price * additionalLossRate;
  const completeContributionRate =
    price > 0 ? completeContribution / price : 0;
  const upfrontCapital = procurement + headhaul;
  const upfrontCapitalRoi =
    upfrontCapital > 0 ? completeContribution / upfrontCapital : 0;

  return {
    sizeTier,
    volumeWeightKg: volumeKg,
    chargeableWeightKg: chargeableKg,
    fbaBaseFeeCad: fba.fee,
    lowPriceDiscountCad: discount,
    fbaSurchargeCad: surcharge,
    fbaFinalFeeCad: finalFba,
    procurementAndProcessingCad: procurement,
    headhaulCad: headhaul,
    estimatedProfitCad: estimatedProfit,
    completeContributionCad: completeContribution,
    completeContributionRate,
    upfrontCapitalRoi,
    fbaValid: fba.valid,
    fbaRateLabel: fba.label,
  };
}

export function calculationMatches(
  preview: CalculationResult,
  actual: Pick<
    CalculationResult,
    | "sizeTier"
    | "volumeWeightKg"
    | "chargeableWeightKg"
    | "fbaBaseFeeCad"
    | "lowPriceDiscountCad"
    | "fbaSurchargeCad"
    | "fbaFinalFeeCad"
    | "headhaulCad"
    | "estimatedProfitCad"
    | "completeContributionCad"
    | "completeContributionRate"
    | "upfrontCapitalRoi"
  >,
  rateTolerances: Partial<Record<"completeContributionRate" | "upfrontCapitalRoi", number>> = {},
): boolean {
  const moneyKeys = [
    "fbaBaseFeeCad",
    "lowPriceDiscountCad",
    "fbaSurchargeCad",
    "fbaFinalFeeCad",
    "headhaulCad",
    "estimatedProfitCad",
    "completeContributionCad",
  ] as const;
  const rateKeys = [
    "completeContributionRate",
    "upfrontCapitalRoi",
  ] as const;
  return (
    preview.sizeTier === actual.sizeTier &&
    Math.abs(preview.volumeWeightKg - actual.volumeWeightKg) <= 0.001001 &&
    Math.abs(preview.chargeableWeightKg - actual.chargeableWeightKg) <= 0.001001 &&
    moneyKeys.every((key) => Math.abs(preview[key] - actual[key]) <= 0.010001) &&
    rateKeys.every(
      (key) =>
        Math.abs(preview[key] - actual[key]) <=
        (rateTolerances[key] ?? 0.00010001),
    )
  );
}
