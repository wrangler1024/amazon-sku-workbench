export type BriefIssue={field:string;code:string;message:string};
export type BriefPolicy={version:string;checks:{code:string;pattern:string;message:string;skip_negative?:boolean;brand_context_only?:boolean}[]};
const fields=['product','instructions','selling_points','visual_focus','requirements'];
const labels:Record<string,string>={product:'产品与销售组合',instructions:'使用说明',selling_points:'卖点总结',visual_focus:'产品表现重点',requirements:'特殊要求与核实项',purpose:'主题',composition:'画面要求',text:'图中文字',copy:'待复制内容'};
export function issueLabel(field:string){const parts=field.split('.');if(parts[0]==='bullets')return `第 ${Number(parts[1])+1} 条五点`;if(parts[0]==='facts')return ({sales_name:'产品名称',quantity:'销售数量',specification:'规格与功能',material:'材质',dimensions:'产品尺寸与重量',included:'实际包含物',excluded:'不包含物与限制',uses:'用途',components:'组成明细'} as Record<string,string>)[parts[1]]||parts[1];return parts[0]==='plans'?`第 ${Number(parts[1])+1} 张 · ${labels[parts[2]]||parts[2]}`:({title:'商品标题',highlights:'商品亮点',description:'长描述',terms:'搜索词'} as Record<string,string>)[field]||labels[field]||field}
export function textIssues(value:string,policy?:BriefPolicy,field='copy'):BriefIssue[]{
 if(!policy)return [];
 const text=String(value||'').normalize('NFKC');const issues:BriefIssue[]=[];
 for(const check of policy.checks){
  for(const match of text.matchAll(new RegExp(check.pattern,'giu'))){
   const clause=text.slice(0,match.index).split(/[。；;\n,，]/).at(-1)+match[0];
   const fullClause=clause+text.slice((match.index||0)+match[0].length).split(/[。；;\n,，]/)[0];
   if(check.skip_negative&&/(?:不|勿|禁止|去除|移除|忽略|无需|不得|不要|without|remove|ignore|do not)/iu.test(clause))continue;
   if(check.brand_context_only&&!/品牌|商标|logo|brand/iu.test(fullClause))continue;
   issues.push({field,code:check.code,message:check.message});break;
  }
 }
 return issues;
}
export function briefIssues(brief:any,policy?:BriefPolicy):BriefIssue[]{
 return [...fields.flatMap(key=>textIssues(brief?.[key]||'',policy,key)),...(brief?.plans||[]).flatMap((plan:any,i:number)=>['purpose','composition','text'].flatMap(key=>textIssues(plan[key]||'',policy,`plans.${i}.${key}`)))];
}
export function combinedPolicy(...policies:(BriefPolicy|undefined)[]):BriefPolicy|undefined{
 const valid=policies.filter((p):p is BriefPolicy=>!!p);if(!valid.length)return;
 return {version:valid[0].version,checks:[...new Map(valid.flatMap(p=>p.checks).map(c=>[c.code+':'+c.pattern,c])).values()]};
}

export async function copyReviewedBrief(text:string,policy:BriefPolicy|undefined,write:(text:string)=>Promise<unknown>){
 if(!policy)throw new Error('图需检查信息尚未就绪，请刷新后再复制');
 const issues=textIssues(text,policy);
 if(issues.length)throw new Error('图需含疑似来源标识，请在编辑稿中核对修改后再复制：'+issues.slice(0,3).map(i=>i.message).join('；'));
 return write(text);
}
