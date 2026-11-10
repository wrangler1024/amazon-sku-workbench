export class InputError extends Error {
  constructor(message, path = '') {
    super(message);
    this.name = 'InputError';
    this.code = 'INVALID_INPUT';
    this.path = path;
  }
}

export function object(value, allowed, path = '') {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new InputError('Expected an object', path);
  const extra = Object.keys(value).find(key => !allowed.includes(key));
  if (extra) throw new InputError('Unknown field', path ? `${path}.${extra}` : extra);
}

function positive(value, path, maximum) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0 || value > maximum)
    throw new InputError(`Expected a finite number greater than 0 and at most ${maximum}`, path);
}

function oneOf(value, values, path) {
  if (!values.includes(value)) throw new InputError(`Expected one of: ${values.join(', ')}`, path);
}

export function validateRequest(input) {
  object(input, ['marketplace', 'price', 'package', 'saleDate', 'category', 'program', 'options']);
  if (typeof input.marketplace !== 'string' || !/^[A-Z]{2}$/.test(input.marketplace))
    throw new InputError('Expected a two-letter uppercase marketplace code', 'marketplace');
  object(input.price, ['amount', 'currency'], 'price');
  positive(input.price.amount, 'price.amount', 100_000_000);
  if (typeof input.price.currency !== 'string' || !/^[A-Z]{3}$/.test(input.price.currency))
    throw new InputError('Expected a three-letter uppercase currency code', 'price.currency');
  if (Math.abs(input.price.amount * 100 - Math.round(input.price.amount * 100)) > 0.00001)
    throw new InputError('Price must have at most two decimal places', 'price.amount');
  if (input.marketplace === 'JP' && !Number.isInteger(input.price.amount))
    throw new InputError('Japan prices must be whole JPY amounts', 'price.amount');
  object(input.package, ['dimensions', 'weight'], 'package');
  object(input.package.dimensions, ['length', 'width', 'height', 'unit'], 'package.dimensions');
  for (const key of ['length', 'width', 'height']) positive(input.package.dimensions[key], `package.dimensions.${key}`, 10_000);
  oneOf(input.package.dimensions.unit, ['cm', 'in'], 'package.dimensions.unit');
  object(input.package.weight, ['value', 'unit'], 'package.weight');
  positive(input.package.weight.value, 'package.weight.value', 1_000_000);
  oneOf(input.package.weight.unit, ['g', 'kg', 'lb', 'oz'], 'package.weight.unit');
  if (typeof input.saleDate !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(input.saleDate) ||
      !Number.isFinite(Date.parse(input.saleDate)) || new Date(input.saleDate).toISOString().slice(0, 10) !== input.saleDate)
    throw new InputError('Expected a real calendar date YYYY-MM-DD', 'saleDate');
  oneOf(input.category, ['general', 'apparel', 'dangerous_goods', 'unknown'], 'category');
  oneOf(input.program ?? 'domestic', ['domestic', 'efn', 'remote', 'pan_eu'], 'program');
  if (input.options !== undefined) {
    object(input.options, ['lowPriceEligibility', 'centralEuropeProgram'], 'options');
    if (input.options.lowPriceEligibility !== undefined)
      oneOf(input.options.lowPriceEligibility, ['conservative', 'confirmed_eligible', 'ineligible'], 'options.lowPriceEligibility');
    if (input.options.centralEuropeProgram !== undefined && typeof input.options.centralEuropeProgram !== 'boolean')
      throw new InputError('Expected a boolean', 'options.centralEuropeProgram');
  }
  const dimensionsCm = ['length', 'width', 'height'].map(key => input.package.dimensions[key] * (input.package.dimensions.unit === 'in' ? 2.54 : 1)).sort((a, b) => b - a);
  const weightG = input.package.weight.value * {g: 1, kg: 1000, lb: 453.59237, oz: 28.349523125}[input.package.weight.unit];
  if (weightG > 1_000_000 || dimensionsCm[0] > 10_000)
    throw new InputError('Package exceeds calculator input limits', 'package');
  return { ...input, marketplace: input.marketplace === 'GB' ? 'UK' : input.marketplace,
    program: input.program ?? 'domestic', dimensionsCm, weightG,
    options: { lowPriceEligibility: 'conservative', centralEuropeProgram: false, ...input.options } };
}
