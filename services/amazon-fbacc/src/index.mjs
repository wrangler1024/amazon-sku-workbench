import book from '../rates/2026-10-31.2.json' with { type: 'json' };
import { InputError, validateRequest } from './validation.mjs';

export { InputError };
export const VERSION = '0.3.0';
export const RATE_VERSION = book.version;
const EPSILON = 1e-9;
const within = (value, maximum) => maximum == null || value <= maximum + EPSILON;
const ceil = value => Math.ceil(value - EPSILON);
const display = value => Math.round(value * 1e9) / 1e9;

function nativePackage(request, market) {
  return {
    dimensions: request.dimensionsCm.map(value => value / (market.dimensionUnit === 'in' ? 2.54 : 1)),
    unitWeight: request.weightG / (market.weightUnit === 'lb' ? 453.59237 : 1000),
  };
}

function classify(tier, pkg, market, lowPrice = false) {
  const dimensionalSides = pkg.dimensions.map((side, i) => Math.max(side, tier.dimensionalMinimumSides?.[i] ?? 0));
  const dimensionalWeight = dimensionalSides.reduce((a, b) => a * b, 1) / market.dimensionalDivisor;
  const useUnit = (lowPrice && market.lowPrice.weightMode === 'unit') || tier.weightMode === 'unit' ||
    (tier.weightMode === 'unit_until_half_kg' && pkg.unitWeight <= .5 + EPSILON);
  const rawWeight = useUnit ? pkg.unitWeight : Math.max(pkg.unitWeight, dimensionalWeight);
  const shippingWeight = tier.weightStep > 0 ? ceil(rawWeight / tier.weightStep) * tier.weightStep : rawWeight;
  const lengthPlusGirth = pkg.dimensions[0] + 2 * (pkg.dimensions[1] + pkg.dimensions[2]);
  const limits = (tier.maxDimensions == null || pkg.dimensions.every((value, i) => within(value, tier.maxDimensions[i]))) &&
    within(pkg.unitWeight, tier.maxUnitWeight) &&
    (lowPrice || within(dimensionalWeight, tier.maxDimensionalWeight)) &&
    within(shippingWeight, tier.maxShippingWeight) &&
    within(pkg.dimensions.reduce((a,b) => a+b, 0), tier.maxDimensionSum) &&
    within(lengthPlusGirth, tier.maxLengthPlusGirth) && within(pkg.dimensions[0], tier.maxLongestSide);
  const exclusive = tier.exclusiveMaximums &&
    (pkg.unitWeight >= tier.maxUnitWeight - EPSILON || dimensionalWeight >= tier.maxDimensionalWeight - EPSILON);
  return limits && !exclusive && !(tier.exclusiveUnitMaximum && pkg.unitWeight >= tier.maxUnitWeight - EPSILON) ? {
    tier: tier.id, shippingWeight, rawWeight, dimensionalWeight,
    weightUnit: market.weightUnit,
    weightBasis: useUnit || pkg.unitWeight >= dimensionalWeight ? 'unit' : 'dimensional',
    roundingStep: tier.weightStep,
  } : null;
}

function tableFee(rows, weight, column) {
  if (!rows) return null;
  const band = rows.find(row => within(weight, row.upTo));
  if (!band || band.cents[column] == null) return null;
  const increment = band.increment;
  const intervals = increment ? Math.max(0, ceil((weight - increment.from) / increment.step)) : 0;
  return { cents: band.cents[column] + intervals * (increment?.cents[column] ?? 0),
    bandMaximum: band.upTo, incrementalSteps: intervals,
    incrementCents: increment?.cents[column] ?? 0 };
}

function selectRate(request, market, pkg, isPeak) {
  const price = request.price.amount;
  const column = market.priceBands.findIndex(band => band.maximum == null ||
    (band.inclusive ? price <= band.maximum : price < band.maximum));
  const suffix = request.marketplace === 'DE' && request.options.centralEuropeProgram ? 'Cep' : '';
  const low = market.lowPrice;
  const lowMaximum = request.options.lowPriceEligibility === 'confirmed_eligible'
    ? (low?.extendedMaximum ?? low?.maximum) : low?.maximum;
  const lowEligible = low && request.options.lowPriceEligibility !== 'ineligible' &&
    (low.inclusive ? price <= lowMaximum : price < lowMaximum);
  if (lowEligible) {
    for (const tier of market.tiers) {
      const classification = classify(tier, pkg, market, true);
      const rate = classification && tableFee(tier[`low${suffix}`], classification.shippingWeight, column);
      if (rate) return { tier, rate, classification, plan: 'low_price', column };
    }
  }
  // Pick the size tier before looking up its fee. Missing/conflicting data may not
  // silently move the product to a larger tier or fall back from a peak table.
  const tier = market.tiers.find(t => classify(t, pkg, market));
  if (!tier) return null;
  const classification = classify(tier, pkg, market);
  const plan = isPeak && tier[`peak${suffix}`] ? 'peak' : 'regular';
  const rate = tableFee(tier[`${plan}${suffix}`], classification.shippingWeight, column);
  return rate ? { tier, rate, classification, plan, column } : null;
}

export function getMarketplaces() {
  return Object.entries(book.markets).map(([marketplace, market]) => ({
    marketplace, currency: market.currency, currencyExponent: market.currencyExponent ?? 2, version: market.version,
    verifiedAt: market.verifiedAt, effectiveFrom: market.effectiveFrom,
    program: 'domestic', category: 'general', feeIncludesTax: market.feeIncludesTax,
    tiers: market.tiers.map(t => ({ id: t.id, maxDimensions: t.maxDimensions ? [...t.maxDimensions] : null,
      dimensionUnit: market.dimensionUnit, maxUnitWeight: t.maxUnitWeight, maxDimensionSum: t.maxDimensionSum ?? null, maxLongestSide: t.maxLongestSide ?? null,
      maxShippingWeight: t.maxShippingWeight, maxDimensionalWeight: t.maxDimensionalWeight,
      weightUnit: market.weightUnit, requiresQuote: t.requiresQuote ?? null })),
    lowPrice: structuredClone(market.lowPrice ?? market.lowPriceDiscount ?? null),
    notes: [...market.notes], sources: structuredClone(market.sources),
  }));
}

export function calculateFbaFee(input) {
  const request = validateRequest(input);
  const market = book.markets[request.marketplace];
  const warnings = [];
  const common = {
    calculatorVersion: VERSION, rateVersion: RATE_VERSION,
    marketplace: request.marketplace, saleDate: request.saleDate,
    currency: market?.currency ?? request.price.currency,
    category: request.category, program: request.program,
    package: { dimensionsCm: request.dimensionsCm.map(display), unitWeightG: display(request.weightG) },
    warnings,
    sources: structuredClone(market?.sources ?? []),
    rateCard: market ? { version: market.version, verifiedAt: market.verifiedAt, effectiveFrom: market.effectiveFrom } : null,
  };
  const quote = (code, message) => ({ ...common, status: 'quote_required', reason: { code, message }, fee: null, classification: null });
  if (!market) return quote('UNSUPPORTED_MARKETPLACE', 'This marketplace has no reviewed rate card.');
  if (request.price.currency !== market.currency)
    throw new InputError(`Expected ${market.currency} for ${request.marketplace}; currency conversion is not performed`, 'price.currency');
  if (request.program !== 'domestic') return quote('UNSUPPORTED_PROGRAM', 'Only domestic FBA is supported; EFN, remote and Pan-EU fees need separate rules.');
  if (request.category !== 'general') return quote('UNSUPPORTED_CATEGORY', 'Confirm general non-apparel, non-dangerous goods before using this rate card.');
  if (request.marketplace !== 'DE' && input.options?.centralEuropeProgram !== undefined)
    throw new InputError('centralEuropeProgram applies only to DE domestic fees', 'options.centralEuropeProgram');
  if (request.saleDate < market.effectiveFrom) return quote('NO_HISTORICAL_RATE', 'The requested date precedes this rate card.');
  if (request.saleDate > market.verifiedAt) warnings.push({code:'FUTURE_DATE',message:'Forecast using published rates known on the verification date; future changes are not automatically synchronized.'});
  if (request.saleDate >= '2027-01-15') warnings.push({code:'NEW_YEAR_RATE_REVIEW',message:'Uses the current regular table after the published peak period. Review new-year rates before operational use.'});
  if (market.lowPrice?.extendedMaximum && request.options.lowPriceEligibility === 'conservative')
    warnings.push({code:'CONSERVATIVE_LOW_PRICE',message:`Without official fee-category confirmation, low-price eligibility is capped at ${market.lowPrice.maximum} ${market.currency}.`});
  if (request.marketplace === 'DE') warnings.push({code:'DE_STORAGE_ASSUMPTION',message:request.options.centralEuropeProgram ? 'Uses CEP pricing; enrollment/storage eligibility must be confirmed.' : 'Uses Germany-only pricing, including the published non-CEP difference.'});
  if (request.marketplace === 'IE') warnings.push({code:'IE_PROMOTIONAL_RATES',message:market.notes[0]});
  if (request.marketplace === 'AU' && (request.weightG > 23000 || request.dimensionsCm.some((n,i)=>n > [116,116,102][i])))
    warnings.push({code:'AU_INBOUND_ELIGIBILITY',message:'This package exceeds ordinary AU inbound limits; Amazon eligibility/approval must be checked independently of this fee estimate.'});
  if (market.ambiguousPrices?.includes(request.price.amount))
    return quote('PRICE_BOUNDARY_UNCONFIRMED', 'Official Saudi fee columns specify below/above SAR 25 without defining exactly SAR 25. Obtain an official quote.');
  const pkg = nativePackage(request, market);
  const isPeak = !!market.peak && request.saleDate >= market.peak.from && request.saleDate < market.peak.until;
  const selected = selectRate(request, market, pkg, isPeak);
  if (!selected) return quote('PACKAGE_OUT_OF_SCOPE', 'No reviewed domestic size/weight tier covers this package.');
  if (selected.tier.requiresQuote && (selected.tier.quoteAboveWeight == null ||
      selected.classification.shippingWeight > selected.tier.quoteAboveWeight + EPSILON) &&
      (selected.tier.quoteAbovePrice == null || request.price.amount > selected.tier.quoteAbovePrice))
    return quote('OFFICIAL_SOURCE_CONFLICT', selected.tier.requiresQuote);
  const guard = market.quoteRules?.find(rule => request.saleDate >= rule.from &&
    request.price.amount >= rule.minimumPrice && selected.classification.shippingWeight > rule.aboveWeight + EPSILON);
  if (guard) return quote('RATE_REVIEW_REQUIRED', guard.message);
  if (['AE','SA'].includes(request.marketplace)) warnings.push({code:'ME_UNIT_WEIGHT',message:'Fee bands use packaged unit weight; fees exclude VAT. Oversize eligibility requires separate verification.'});
  if (request.marketplace === 'JP') warnings.push({code:'JP_ORDINARY_NON_FROZEN',message:'Non-frozen ordinary goods only. Fees include 10% consumption tax; use packaged unit weight and applicable side/sum limits.'});
  const minorUnitScale = 10 ** (market.currencyExponent ?? 2);
  const baseCents = selected.rate.cents;
  const discountCents = market.lowPriceDiscount && request.price.amount < market.lowPriceDiscount.below
    ? Math.min(baseCents, market.lowPriceDiscount.cents) : 0;
  const subtotalCents = baseCents - discountCents;
  const fuelBasisPoints = market.fuel && request.saleDate >= market.fuel.from ? market.fuel.basisPoints : 0;
  // All monetary rates are integer minor units; round the surcharge once, half up.
  const fuelCents = Math.floor((subtotalCents * fuelBasisPoints + 5000) / 10000);
  return { ...common, status: 'estimated',
    classification: { ...selected.classification,
      shippingWeight: display(selected.classification.shippingWeight),
      rawWeight: display(selected.classification.rawWeight),
      dimensionalWeight: display(selected.classification.dimensionalWeight),
      plan: market.priceBandPlans?.[selected.column] ?? selected.plan, priceBandIndex: selected.column,
      incrementalSteps: selected.rate.incrementalSteps },
    assumptions: { lowPriceEligibility: request.options.lowPriceEligibility,
      centralEuropeProgram: request.marketplace === 'DE' ? request.options.centralEuropeProgram : null,
      includesOnly: 'domestic_fba_fulfillment_and_applicable_fuel_surcharge',
      excludes: ['referral','storage','inbound','returns','removal','SIPP','inventory_surcharges','additional_tax'] },
    fee: { base: baseCents/minorUnitScale, lowPriceDiscount: discountCents/minorUnitScale, subtotal: subtotalCents/minorUnitScale,
      fuelSurcharge: fuelCents/minorUnitScale, fuelRate: fuelBasisPoints/10000,
      total: (subtotalCents+fuelCents)/minorUnitScale, totalMinorUnits: subtotalCents+fuelCents,
      includesTax: market.feeIncludesTax },
  };
}

export function calculateBatch(items) {
  if (!Array.isArray(items) || items.length < 1 || items.length > 100)
    throw new InputError('Expected 1 to 100 requests', 'items');
  return items.map((input, index) => {
    try { return { index, ok: true, result: calculateFbaFee(input) }; }
    catch (error) {
      if (!(error instanceof InputError)) throw error;
      return { index, ok: false, error: { code: error.code, path: error.path, message: error.message } };
    }
  });
}
