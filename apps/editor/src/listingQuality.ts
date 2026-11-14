import {textIssues,type BriefIssue,type BriefPolicy} from './briefPolicy.ts';
type Measurement={family:string;value:number;text:string;scale:number;resolution:number;fact?:string};
export type ListingPolicy=BriefPolicy&{confirmed:boolean;facts:{id:string;label:string;text:string;uncertain?:boolean}[];measurements:Measurement[];unconfirmed_measurements?:Measurement[]};
const units:Record<string,[string,number]>={mm:['length',1],毫米:['length',1],cm:['length',10],厘米:['length',10],m:['length',1000],米:['length',1000],in:['length',25.4],inch:['length',25.4],inches:['length',25.4],g:['weight',1],克:['weight',1],kg:['weight',1000],千克:['weight',1000],公斤:['weight',1000],oz:['weight',28.349523125],lb:['weight',453.59237],lbs:['weight',453.59237],ml:['volume',1],毫升:['volume',1],l:['volume',1000],升:['volume',1000],v:['voltage',1],伏:['voltage',1],w:['power',1],瓦:['power',1],mah:['charge',1]};
const number='\\d+(?:[.,]\\d+)?';
const unitPattern=Object.keys(units).sort((a,b)=>b.length-a.length).join('|');
function measurement(value:string,unit:string,text:string):Measurement{const [family,scale]=units[unit.toLowerCase()],n=value.replace(',','.'),precision=n.includes('.')?n.split('.')[1].length:0;return {family,value:Number(n)*scale,text,scale,resolution:scale*10**(-precision)}}
export function measurements(value:string):Measurement[]{
 const text=String(value||'').normalize('NFKC'),result:Measurement[]=[],spans:[number,number][]=[];
 for(const m of text.matchAll(new RegExp(`(${number})\\s*[x×*]\\s*(${number})(?:\\s*[x×*]\\s*(${number}))?\\s*(${unitPattern})(?![A-Za-z])`,'giu'))){for(const n of m.slice(1,4).filter(Boolean))result.push(measurement(n,m[4],m[0]));spans.push([m.index!,m.index!+m[0].length])}
 for(const m of text.matchAll(new RegExp(`(?<![A-Za-z0-9])(${number})\\s*(${unitPattern})(?![A-Za-z])`,'giu'))){if(spans.some(([a,b])=>a<=m.index!&&m.index!<b))continue;result.push(measurement(m[1],m[2],m[0]))}
 return result;
}
function equivalent(a:Measurement,b:Measurement){const tolerance=a.scale!==b.scale?a.resolution/2+1e-6:1e-6;return a.family===b.family&&!(a.value===0&&b.value>0)&&Math.abs(a.value-b.value)<=tolerance}
export function listingFields(value:any):[string,string][]{return [...['title','highlights','description','terms'].map(k=>[k,String(value?.[k]||'')] as [string,string]),...(value?.bullets||[]).map((b:string,i:number)=>[`bullets.${i}`,b] as [string,string])]}
export function listingIssues(value:any,policy?:ListingPolicy,modelReview?:any):BriefIssue[]{
 if(!policy)return [];
 const fields=listingFields(value),issues=fields.flatMap(([k,v])=>textIssues(v,policy,k)),actual:Measurement[]=[];
 (value?.bullets||[]).forEach((b:string,i:number)=>{if(b.trim()&&!/^[^:：\n]{1,160}[:：]\s*\S/u.test(b))issues.push({field:`bullets.${i}`,code:'bullet_format',message:'请按“小标题: 说明”核对本条格式'});if(/[.!?。！？…]$/u.test(b.trim()))issues.push({field:`bullets.${i}`,code:'bullet_ending',message:'请按SOP去除五点末尾的句尾标点'})});
 if(policy.confirmed){for(const [field,text] of fields){if(field==='terms')continue;for(const m of measurements(text)){actual.push(m);if(!policy.measurements.some(k=>equivalent(m,k))){const uncertain=(policy.unconfirmed_measurements||[]).some(k=>equivalent(m,k));issues.push({field,code:uncertain?'unconfirmed_measurement':'unsupported_measurement',message:(uncertain?'仍含待确认数值，核实前请勿用于刊登：':'本品事实未确认此数值或单位：')+m.text})}}}
  for(const fact of policy.facts){if(fact.uncertain){issues.push({field:'facts.'+fact.id,code:'fact_needs_confirmation',message:fact.label+'含待确认信息，核实后再补入文案'})}const expected=policy.measurements.filter(m=>m.fact===fact.id);if(expected.some(m=>!actual.some(a=>equivalent(a,m))))issues.push({field:'facts.'+fact.id,code:'missing_detail',message:fact.label+'中的已确认数值尚未完整覆盖，请核对补充'})}}
 if(modelReview&&JSON.stringify(modelReview.policy?.facts)===JSON.stringify(policy.facts))issues.push(...(modelReview.issues||[]).filter((i:BriefIssue)=>['coverage_unverified','operator_review'].includes(i.code)));
 return [...new Map(issues.map(i=>[i.field+':'+i.code+':'+i.message,i])).values()];
}
export function matchingListingReview(value:any,jobs:any[]=[]){const key=JSON.stringify(listingFields(value));return jobs.find(j=>j.task==='listing'&&j.result?.candidate&&JSON.stringify(listingFields(j.result.candidate))===key)?.result?.listing_review}
export async function copyReviewedListing(text:string,policy:BriefPolicy|undefined,write:(text:string)=>Promise<unknown>){
 if(!policy)throw new Error('文案检查信息尚未就绪，请刷新后再复制');
 const visible=text.replace(/<[^>]*>/gu,' ').replace(/&nbsp;|&#160;|&#xa0;/giu,' ');
 const issues=listingIssues({description:visible},policy as ListingPolicy).filter(i=>['source_identifier','internal_review','unconfirmed_measurement'].includes(i.code));
 if(issues.length)throw new Error('文案含来源型号、货号、内部核对说明或待确认数值，请在编辑稿中处理后再复制：'+issues.slice(0,3).map(i=>i.message).join('；'));
 return write(text);
}
