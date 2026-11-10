import test from "node:test";
import assert from "node:assert/strict";
import {
  convertUnit,
  makeRequest,
  MARKETS,
  CURRENCIES,
  EXAMPLE_PRICES,
  comparisonCsv,
  quoteMessage,
} from "../public/model.mjs";
import { calculateFbaFee } from "../src/index.mjs";
const values = {
  length: 20,
  width: 20,
  height: 10,
  dimensionUnit: "cm",
  weight: 200,
  weightUnit: "g",
  saleDate: "2026-10-31",
  lowPriceEligibility: "conservative",
  cep: false,
};

test("unit switches preserve the physical package at exact AU gram/fee boundaries", () => {
  const original = makeRequest(values, "AU", 25);
  const converted = {
    ...values,
    dimensionUnit: "in",
    weightUnit: "lb",
    length: convertUnit(20, "cm", "in"),
    width: convertUnit(20, "cm", "in"),
    height: convertUnit(10, "cm", "in"),
    weight: convertUnit(200, "g", "lb"),
  };
  const fee = calculateFbaFee(makeRequest(converted, "AU", 25));
  assert.deepEqual(fee.fee, calculateFbaFee(original).fee);
  assert.equal(fee.fee.total, 7.24);
  assert.equal(fee.classification.shippingWeight, 1);
  assert.equal(convertUnit("", "g", "kg"), "");
  assert.equal(convertUnit(1000, "g", "kg"), "1");
  assert.throws(() => convertUnit(10, "g", "cm"));
});

test("each marketplace uses its own currency and price; CEP is sent only for DE", () => {
  assert.equal(MARKETS.length, 17);
  for (const [code] of MARKETS) {
    const req = makeRequest(
      { ...values, cep: true },
      code,
      EXAMPLE_PRICES[code],
    );
    assert.equal(req.price.amount, EXAMPLE_PRICES[code]);
    assert.equal(req.price.currency, CURRENCIES[code]);
    assert.equal(
      Object.hasOwn(req.options, "centralEuropeProgram"),
      code === "DE",
    );
    assert.equal(calculateFbaFee(req).status, "estimated");
  }
});

test("CSV retains currencies and uses an empty fee for quote_required, never a zero estimate", () => {
  const req = makeRequest(values, "CA", 13.99);
  const csv = comparisonCsv([
    { request: req, result: { status: "quote_required", rateVersion: "test" } },
  ]);
  assert.ok(csv.startsWith("\uFEFF"));
  assert.match(csv, /"加拿大","CAD","13.99",""/);
  assert.match(csv, /需报价/);
  assert.match(quoteMessage("OFFICIAL_SOURCE_CONFLICT"), /不一致/);
});

test("eligibility UI exposes only supported choices and uses the API's price thresholds", async () => {
  const {lowPriceChoices,lowPricePresentation}=await import('../public/model.mjs');
  const {getMarketplaces}=await import('../src/index.mjs');
  for(const metadata of getMarketplaces()) {
    const code=metadata.marketplace;
    const view=lowPricePresentation(code,metadata);
    if(['US','CA','MX','AE','SA','JP'].includes(code)) {
      assert.equal(view,null);
      assert.deepEqual(lowPriceChoices(code),[]);
    } else {
      assert.ok(view.hint.includes(`${metadata.currency} ${metadata.lowPrice.maximum}`));
      assert.equal(view.labels.length,code==='AU'?2:3);
      assert.equal(view.labels.some(([value])=>value==='confirmed_eligible'),code!=='AU');
    }
  }
});

test("hidden eligibility choices never affect US/CA/MX and AU cannot use an extended option", () => {
  for(const code of ['US','CA','MX']) {
    const request=makeRequest({...values,lowPriceEligibility:'ineligible'},code,EXAMPLE_PRICES[code]);
    assert.equal(Object.hasOwn(request.options,'lowPriceEligibility'),false);
    assert.deepEqual(calculateFbaFee(request).fee,calculateFbaFee(makeRequest(values,code,EXAMPLE_PRICES[code])).fee);
  }
  const au=makeRequest({...values,lowPriceEligibility:'confirmed_eligible'},'AU',12.99);
  assert.equal(au.options.lowPriceEligibility,'conservative');
});

test("batch eligibility is independent per site, including when a selection is absent", () => {
  const input={...values,length:20,width:15,height:1,weight:50,
    lowPriceByMarketplace:{UK:'confirmed_eligible',DE:'ineligible',AU:'conservative'}};
  assert.equal(calculateFbaFee(makeRequest(input,'UK',15)).fee.total,1.54);
  assert.equal(calculateFbaFee(makeRequest(input,'AU',12.99)).classification.plan,'low_price');
  assert.notEqual(calculateFbaFee(makeRequest(input,'DE',10)).classification.plan,'low_price');
  assert.equal(makeRequest(input,'FR',15).options.lowPriceEligibility,'conservative');
  assert.equal(makeRequest({...input,lowPriceByMarketplace:{UK:'ineligible'}},'AU',12.99).options.lowPriceEligibility,'conservative');
});
