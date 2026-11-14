import React from 'react';
import {type BriefIssue,issueLabel} from './briefPolicy';
export default function ListingReview({issues,candidate=false}:{issues:BriefIssue[];candidate?:boolean}){
 if(!issues.length)return null;
 return <div className="note-box brief-review" role="status" aria-label="文案内容核对"><strong>以下内容需要人工核对</strong><p>{candidate?'可以先采用到编辑稿，再检查下列位置。':'请结合已确认的本品资料核对下列内容。'}核对提示仅供运营参考。请先处理文案中的来源编号、内部核对说明及待确认数值，再复制用于刊登。</p><ul>{issues.slice(0,12).map((issue,i)=><li key={i}><strong>{issueLabel(issue.field)}</strong>：{issue.message}</li>)}</ul>{issues.length>12&&<p>另有 {issues.length-12} 处，请一并核对。</p>}</div>;
}
