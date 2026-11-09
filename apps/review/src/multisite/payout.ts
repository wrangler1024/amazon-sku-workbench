/** 预计到账资金占买家标价的比例；只扣平台结算和按销售额估算的费用，不扣采购与头程。 */
export function estimatedPayoutRate(input: {
  listedPrice: number;
  netRevenue: number | null;
  fbaCharged: number | null;
  referralCharged: number | null;
  withholdingCharged: number | null;
  salesDeductions: number | null;
}): number | null {
  const values = [input.listedPrice, input.netRevenue, input.fbaCharged,
    input.referralCharged, input.withholdingCharged, input.salesDeductions];
  if (input.listedPrice <= 0 || values.some((value) => value === null ||
    !Number.isFinite(value) || value < 0)) return null;
  return (input.netRevenue! - input.fbaCharged! - input.referralCharged! -
    input.withholdingCharged! - input.salesDeductions!) / input.listedPrice;
}
