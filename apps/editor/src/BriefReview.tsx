import React from 'react';
import {type BriefIssue,issueLabel} from './briefPolicy';
export default function BriefReview({issues,candidate=false}:{issues:BriefIssue[];candidate?:boolean}){
 if(!issues.length)return null;
 return <div className="note-box brief-review" role="status" aria-label="图需内容核对"><strong>发现疑似来源标识，请人工核对</strong><p>{candidate?'可以先采用到编辑稿，再按下列位置修改。':'请在现有图需中核对修改。'}内容未被自动改写，保存和确认流程保持不变；相关标识处理后再复制制作要求。</p><ul>{issues.slice(0,12).map((issue,i)=><li key={i}><strong>{issueLabel(issue.field)}</strong>：{issue.message}</li>)}</ul>{issues.length>12&&<p>另有 {issues.length-12} 处，请一并核对。</p>}</div>;
}
