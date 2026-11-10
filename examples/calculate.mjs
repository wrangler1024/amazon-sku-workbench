import { calculateFbaFee, calculateBatch } from '../src/index.mjs';

const item = {
  marketplace: 'CA', price: { amount: 13.99, currency: 'CAD' },
  package: { dimensions: { length: 30, width: 20, height: 5, unit: 'cm' }, weight: { value: 200, unit: 'g' } },
  category: 'general', saleDate: '2026-10-31',
};
console.log(JSON.stringify(calculateFbaFee(item), null, 2));
console.log(JSON.stringify(calculateBatch([item, { ...item, price: {amount: 14, currency: 'CAD'} }]), null, 2));
