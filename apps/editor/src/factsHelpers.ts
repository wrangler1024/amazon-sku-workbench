export function validComponent(row:any){return !!row.id&&!!String(row.name||'').trim()&&String(row.name).length<=200&&!!String(row.specification||'').trim()&&String(row.specification).length<=2000&&!!String(row.unit||'').trim()&&String(row.unit).length<=30&&typeof row.quantity!=='boolean'&&Number.isFinite(Number(row.quantity))&&Number(row.quantity)>0&&Number(row.quantity)<=100000}
export function quantitySummary(rows:any[]){
 const totals=new Map<string,number>();let invalid=false;
 for(const row of rows){const q=Number(row.quantity),unit=String(row.unit||'').trim();if(!Number.isFinite(q)||q<=0||!unit){invalid=true;continue}totals.set(unit,(totals.get(unit)||0)+q)}
 const text=[...totals].map(([u,n])=>Number(n.toPrecision(12)).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:12})+u).join(' + ');
 return (text||'待填写数量')+(invalid&&text?' · 有数量待补齐':'');
}
export function summarizeContents(rows:any[]){
 if(!rows.length||rows.some(r=>!validComponent(r)))return '';
 const groups=new Map<string,any[]>();for(const r of rows){const name=String(r.name).trim();groups.set(name,[...(groups.get(name)||[]),r])}
 const detailed=[...groups].map(([name,items])=>`${name}：${items.map(x=>`${x.specification} × ${x.quantity}${x.unit}`).join('；')}`).join('\n');
 if(detailed.length<=4000)return detailed;
 const compact=[...groups].map(([name,items])=>`${name} ${quantitySummary(items)}（${items.length}种规格，详见组成明细）`).join('\n');
 return compact.length<=4000?compact:`共${rows.length}项组成，${quantitySummary(rows)}；按组成明细中各项规格及用量配套`;
}
export function usageSuggestions(sources:any[],snapshots:any[]){
 const seen=new Set<string>();const result:{text:string;origin:string}[]=[];
 for(const source of sources){const snapshot=snapshots.find(x=>x.url===source.url);for(const attr of snapshot?.body?.specifications||[]){const match=String(attr).match(/^(主要用途|用途|适用场景|使用场景|使用用途)\s*[:：]\s*(.+)$/);if(!match)continue;for(const value of match[2].split(/[;；、,，]/)){const text=value.trim();if(!text||text.length>100||/^(通用|所有|其他|无|暂无|默认|不详|见详情|详见描述|可定制)$/.test(text)||seen.has(text))continue;seen.add(text);result.push({text,origin:`${match[1]} · ${source.url}`})}}}
 return result.slice(0,12);
}
export function appendChoice(current:string,value:string){const parts=String(current||'').split(/[\n；;]+/).map(x=>x.trim()).filter(Boolean);return parts.includes(value)?current:[...parts,value].join('；')}
export function syncGeneratedContents(current:string,previous:string,next:string,linked:string|null){
 const basis=[linked,previous].find(b=>b&&(current===b||current.startsWith(b+'\n')||current.startsWith(b+'；')));
 if(!basis)return {text:current,basis:null};
 return {text:next?next+current.slice(basis.length):current,basis:next||basis};
}
export function registeredSupplierUrls(snapshot:any){
 const found:string[]=[];const visit=(value:any)=>{if(typeof value==='string'){for(const match of value.matchAll(/https?:\/\/(?:[a-zA-Z0-9-]+\.)*1688\.com\/offer\/\d+\.html(?:\?[^\s<>"，；]*)?/g)){const url=match[0].replace(/^http:/,'https:');if(!found.includes(url))found.push(url)}}else if(Array.isArray(value))value.forEach(visit);else if(value&&typeof value==='object')['link','url','text'].forEach(k=>visit(value[k]))};
 for(const key of ['1688货源','1688链接'])visit(snapshot?.[key]);return found.slice(0,20);
}
export function defaultSupplierUrl(sources:any[],snapshot:any){for(const s of sources){if(s.kind==='supplier'){const url=registeredSupplierUrls({'1688货源':s.url})[0];if(url)return url}}return registeredSupplierUrls(snapshot)[0]||''}

const measurementNumber=(n:number)=>Number(n.toPrecision(12)).toLocaleString('zh-CN',{useGrouping:false,maximumFractionDigits:12});
export const packageWeight=(p:any)=>Number.isFinite(p?.weight_kg)&&p.weight_kg>0?`${measurementNumber(p.weight_kg*1000)} g（${measurementNumber(p.weight_kg)} kg）`:'未提供';
export const packageSize=(p:any)=>p&&[p.length,p.width,p.height].some(v=>Number.isFinite(v)&&v>0)?[p.length,p.width,p.height].map(v=>Number.isFinite(v)&&v>0?measurementNumber(v):'—').join(' × ')+(p.dimension_unit?' '+p.dimension_unit:'（单位未注明）'):'未提供';

export function artworkFacts(data:any,task:any){
 const brief=data.versions?.find((v:any)=>v.id===task?.brief_version);
 return data.versions?.find((v:any)=>v.id===brief?.facts_version)?.body||{};
}

export const componentFromSource=(row:any,source:any)=>row.source_id?row.source_id===source.id:!!source.url&&row.url===source.url;
export function supplierRemoval(facts:any,source:any){
 const rows=facts.components||[],remaining=rows.filter((r:any)=>!componentFromSource(r,source));
 const before=summarizeContents(rows),after=summarizeContents(remaining),current=facts.included||'';
 const linked=before&&(current===before||current.startsWith(before+'\n')||current.startsWith(before+'；'));
 const suffix=linked?current.slice(before.length):'';
 return {count:rows.length-remaining.length,included:linked?(after?after+suffix:suffix.replace(/^[\n；]+/,'')):current};
}
