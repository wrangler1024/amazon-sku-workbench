export type Marketplace = 'AU' | 'US' | 'CA' | 'MX' | 'UK' | 'DE' | 'FR' | 'IT' | 'ES' | 'NL' | 'SE' | 'PL' | 'BE' | 'IE' | 'AE' | 'SA' | 'JP';
export interface CalculationRequest {
  marketplace: Marketplace | 'GB' | (string & {});
  /** Amount is in native currency; JP requires whole yen. */
  price: { amount: number; currency: string };
  package: {
    dimensions: { length: number; width: number; height: number; unit: 'cm' | 'in' };
    weight: { value: number; unit: 'g' | 'kg' | 'lb' | 'oz' };
  };
  /** YYYY-MM-DD, marketplace-local expected sale/fulfilment date. */
  saleDate: string;
  category: 'general' | 'apparel' | 'dangerous_goods' | 'unknown';
  program?: 'domestic' | 'efn' | 'remote' | 'pan_eu';
  options?: { lowPriceEligibility?: 'conservative' | 'confirmed_eligible' | 'ineligible'; centralEuropeProgram?: boolean };
}
export interface Source { title: string; url: string; pages?: number[] }
export interface Notice { code: string; message: string }
export interface RateCard { version: string; verifiedAt: string; effectiveFrom: string }
export interface Classification {
  tier: string; plan: 'regular' | 'peak' | 'low_price'; weightUnit: 'kg' | 'lb';
  shippingWeight: number; rawWeight: number; dimensionalWeight: number;
  weightBasis: 'unit' | 'dimensional'; roundingStep: number; priceBandIndex: number; incrementalSteps: number;
}
interface ResultBase {
  marketplace: string; currency: string; saleDate: string; category: CalculationRequest['category'];
  program: NonNullable<CalculationRequest['program']>; calculatorVersion: string; rateVersion: string;
  rateCard: RateCard | null; package: { dimensionsCm: number[]; unitWeightG: number };
  warnings: Notice[]; sources: Source[];
}
export interface EstimatedResult extends ResultBase {
  status: 'estimated'; classification: Classification;
  fee: { base: number; lowPriceDiscount: number; subtotal: number; fuelSurcharge: number;
    fuelRate: number; total: number; totalMinorUnits: number; includesTax: boolean };
  assumptions: { lowPriceEligibility: NonNullable<CalculationRequest['options']>['lowPriceEligibility'];
    centralEuropeProgram: boolean | null; includesOnly: string; excludes: string[] };
}
export interface QuoteRequiredResult extends ResultBase {
  status: 'quote_required'; fee: null; classification: null; reason: Notice;
}
export type CalculationResult = EstimatedResult | QuoteRequiredResult;
export interface MarketplaceInfo {
  marketplace: Marketplace; currency: string; currencyExponent: number; version: string; verifiedAt: string; effectiveFrom: string;
  program: 'domestic'; category: 'general'; feeIncludesTax: boolean; notes: string[]; sources: Source[];
  lowPrice: { maximum?: number; extendedMaximum?: number; inclusive?: boolean; below?: number; cents?: number } | null;
  tiers: Array<{ id: string; maxDimensions: Array<number | null> | null; dimensionUnit: 'cm' | 'in';
    maxDimensionSum: number | null; maxLongestSide: number | null; maxUnitWeight: number | null; maxShippingWeight: number | null; maxDimensionalWeight: number | null;
    weightUnit: 'kg' | 'lb'; requiresQuote: string | null }>;
}
export class InputError extends Error { code: 'INVALID_INPUT'; path: string; constructor(message: string, path?: string) }
export const VERSION: string;
export const RATE_VERSION: string;
export function calculateFbaFee(input: CalculationRequest): CalculationResult;
export function getMarketplaces(): MarketplaceInfo[];
export function calculateBatch(items: CalculationRequest[]): Array<
  { index: number; ok: true; result: CalculationResult } |
  { index: number; ok: false; error: { code: string; path: string; message: string } }
>;
