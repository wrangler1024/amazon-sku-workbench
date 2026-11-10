import { VERSION } from './index.mjs';

const strictObject = (properties, required = Object.keys(properties)) => ({type:'object',additionalProperties:false,properties,required});
const positive = {type:'number',exclusiveMinimum:0};
const request = strictObject({
  marketplace:{type:'string',pattern:'^[A-Z]{2}$',description:'AU, US, CA, MX, UK (GB alias), DE, FR, IT, ES, NL, SE, PL, BE, IE, AE, SA, JP'},
  price:strictObject({amount:{...positive,maximum:100000000,multipleOf:.01},currency:{type:'string',pattern:'^[A-Z]{3}$'}}),
  package:strictObject({
    dimensions:strictObject({length:{...positive,maximum:10000},width:{...positive,maximum:10000},height:{...positive,maximum:10000},unit:{enum:['cm','in']}}),
    weight:strictObject({value:{...positive,maximum:1000000},unit:{enum:['g','kg','lb','oz']}}),
  }),
  saleDate:{type:'string',format:'date',description:'Expected sale/fulfilment date in the marketplace, not inbound shipment date.'},
  category:{enum:['general','apparel','dangerous_goods','unknown'],description:'Only general non-apparel non-dangerous goods currently receive an estimate.'},
  program:{enum:['domestic','efn','remote','pan_eu'],default:'domestic',description:'Only domestic currently supported.'},
  options:strictObject({lowPriceEligibility:{enum:['conservative','confirmed_eligible','ineligible'],default:'conservative'},centralEuropeProgram:{type:'boolean',default:false,description:'DE only. false uses Germany-only fee column.'}},[]),
},['marketplace','price','package','saleDate','category']);
const warning = strictObject({code:{type:'string'},message:{type:'string'}});
const rateCard = {type:['object','null'],properties:{version:{type:'string'},verifiedAt:{type:'string',format:'date'},effectiveFrom:{type:'string',format:'date'}}};
const result = {
  type:'object',required:['status','marketplace','currency','saleDate','fee','classification','sources','warnings','rateVersion','calculatorVersion','rateCard'],
  properties:{
    status:{enum:['estimated','quote_required']},marketplace:{type:'string'},currency:{type:'string'},saleDate:{type:'string',format:'date'},
    calculatorVersion:{type:'string'},rateVersion:{type:'string'},rateCard,
    category:{type:'string'},program:{type:'string'},package:{type:'object'},
    fee:{type:['object','null'],properties:{base:{type:'number'},lowPriceDiscount:{type:'number'},subtotal:{type:'number'},fuelSurcharge:{type:'number'},fuelRate:{type:'number'},total:{type:'number'},totalMinorUnits:{type:'integer'},includesTax:{type:'boolean'}}},
    classification:{type:['object','null'],properties:{tier:{type:'string'},plan:{enum:['regular','low_price','peak']},weightUnit:{enum:['kg','lb']},shippingWeight:{type:'number'},dimensionalWeight:{type:'number'},rawWeight:{type:'number'},weightBasis:{enum:['unit','dimensional']},roundingStep:{type:'number'},priceBandIndex:{type:'integer'},incrementalSteps:{type:'integer'}}},
    reason:warning,warnings:{type:'array',items:warning},sources:{type:'array',items:{type:'object',required:['url','title'],properties:{url:{type:'string',format:'uri'},title:{type:'string'},pages:{type:'array',items:{type:'integer'}}}}},assumptions:{type:'object'},
  },
};
const json = schema => ({'application/json':{schema}});
const errors = Object.fromEntries([400,401,413,415,422,500].map(code=>[code,{description:'Structured error',content:json({$ref:'#/components/schemas/Error'})}]));

request.allOf = [{if:{properties:{marketplace:{const:'JP'}},required:['marketplace']},then:{properties:{price:{properties:{amount:{type:'integer',description:'JPY prices must be whole yen.'}}}}}}];

export const openapi = {
  openapi:'3.1.0',info:{title:'Samforo FBA Fee Calculator',version:VERSION,description:'Versioned domestic FBA fee estimates for custom packages. No ASIN required. Independent of Feishu and SKU costs.'},
  servers:[{url:'http://127.0.0.1:4318'}],
  paths:{
    '/healthz':{get:{operationId:'health',security:[],responses:{200:{description:'Service and rate version',content:json({type:'object'})}}}},
    '/readyz':{get:{operationId:'readiness',security:[],responses:{200:{description:'All marketplace adapters ready',content:json({type:'object'})},503:{description:'Not ready or draining',content:json({type:'object'})}}}},
    '/v1/fba/marketplaces':{get:{operationId:'getMarketplaces',responses:{200:{description:'17 marketplaces with published limits, sources and assumptions',content:json({type:'object'})},...errors}}},
    '/v1/fba/calculate':{post:{operationId:'calculateFbaFee',requestBody:{required:true,content:json({$ref:'#/components/schemas/CalculationRequest'})},responses:{200:{description:'Estimate or explicit quote_required with null fee',content:json({$ref:'#/components/schemas/CalculationResult'})},...errors}}},
    '/v1/fba/calculate-batch':{post:{operationId:'calculateBatch',requestBody:{required:true,content:json(strictObject({items:{type:'array',minItems:1,maxItems:100,items:{$ref:'#/components/schemas/CalculationRequest'}}}))},responses:{200:{description:'Ordered, independently validated results',content:json({type:'object',required:['results'],properties:{results:{type:'array',items:{oneOf:[strictObject({index:{type:'integer'},ok:{const:true},result:{$ref:'#/components/schemas/CalculationResult'}}),strictObject({index:{type:'integer'},ok:{const:false},error:{$ref:'#/components/schemas/ErrorDetail'}})]}}}})},...errors}}},
    '/openapi.json':{get:{operationId:'getOpenApi',responses:{200:{description:'This OpenAPI 3.1 document',content:json({type:'object'})}}}},
  },
  components:{schemas:{CalculationRequest:request,CalculationResult:result,ErrorDetail:strictObject({code:{type:'string'},message:{type:'string'},path:{type:'string'}},['code','message']),Error:strictObject({error:{$ref:'#/components/schemas/ErrorDetail'}})},securitySchemes:{bearerAuth:{type:'http',scheme:'bearer',description:'Required when FBA_CALCULATOR_API_KEY is configured; healthz remains public.'}}},
};
