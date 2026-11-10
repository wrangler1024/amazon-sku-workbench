import {calculateFbaFee, type CalculationRequest, type CalculationResult} from '@samforo/fba-fee-calculator';

const request: CalculationRequest = {
  marketplace:'CA', price:{amount:13.99,currency:'CAD'},
  package:{dimensions:{length:30,width:20,height:5,unit:'cm'},weight:{value:200,unit:'g'}},
  saleDate:'2026-10-31', category:'general',
};
const result: CalculationResult = calculateFbaFee(request);
if(result.status==='estimated') console.log(result.fee.total, result.classification.shippingWeight);
else console.log(result.reason.code);
