export const briefFields=[['product','产品与销售组合'],['instructions','使用说明'],['selling_points','卖点总结'],['visual_focus','产品表现重点'],['requirements','特殊要求与核实项']];
export const briefImageCount=(brief:any)=>brief?.image_count??(brief?.plans?.length||7);
export const blankPlan=(i:number)=>({purpose:i===0?'白底主图':'副图 '+i,composition:'',text:''});
export const emptyBrief={product:'',instructions:'',selling_points:'',visual_focus:'',requirements:'',references:[],image_count:7,plans:Array.from({length:7},(_,i)=>blankPlan(i))};
export function withPlans(brief:any,plans:any[]){return {...brief,plans,image_count:plans.length||briefImageCount(brief)}}
export function planText(plan:any,i:number){return [`${String(i+1).padStart(2,'0')} ${plan.purpose||'图片 '+(i+1)}`,plan.composition?'画面要求：'+plan.composition:'',plan.text?'图中文字：'+plan.text:''].filter(Boolean).join('\n')}
export function plansText(plans:any[]=[]){return plans.length?'【套图策划建议】\n'+plans.map(planText).join('\n\n'):''}
export function briefText(brief:any){return [...briefFields.map(([k,l])=>`【${l}】\n${brief[k]||''}`),'策划建议数量：'+briefImageCount(brief)+'张（交付按实际内容确定）',plansText(brief.plans)].filter(Boolean).join('\n\n')}

export function artworkPacket(name:string,brief:any,sources:any[]=[],legacyBrief='',identity:{task_number?:string;code?:string}={}){
 const links=new Map<string,{kind:string;notes:string[]}>();
 for(const source of [...sources,...(brief?.references||[]).map((url:string)=>({url,kind:'reference'}))]){
  const url=String(source.url||'').trim();if(!url)continue;
  const row=links.get(url)||{kind:source.kind,notes:[]};const note=String(source.manual_text||'').trim();
  if(note&&!row.notes.includes(note))row.notes.push(note);links.set(url,row);
 }
 return [identity.task_number?'任务编号：'+identity.task_number:'',identity.code?'SKU：'+identity.code:'','销售商品：'+name,legacyBrief||briefText(brief||{}),...Array.from(links,([url,row])=>[
  row.kind==='supplier'?'1688货源链接：':row.kind==='competitor'?'竞品参考链接：':'参考链接：',url,
  ...(row.notes.length?[(row.kind==='competitor'?'参考重点：':'补充资料：'),...row.notes]:[])
 ].join('\n'))].filter(Boolean).join('\n\n');
}
