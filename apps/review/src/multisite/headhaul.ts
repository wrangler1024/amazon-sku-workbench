/** Rough headhaul follows the same max(actual, volumetric) rule as the full review. */
export function estimateHeadhaul(
  lengthCm: number, widthCm: number, heightCm: number, weightG: number,
  divisorCm3PerKg: number, cnyPerKg: number,
) {
  if (![lengthCm, widthCm, heightCm, weightG, divisorCm3PerKg, cnyPerKg]
    .every((value) => Number.isFinite(value) && value > 0)) return null;
  const volumeKg = lengthCm * widthCm * heightCm / divisorCm3PerKg;
  const billingKg = Math.max(weightG / 1000, volumeKg);
  return { volumeKg, billingKg, headhaulCny: billingKg * cnyPerKg };
}
