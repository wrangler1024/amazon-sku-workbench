import React from 'react';
import {Button} from './prototype/ui';
export default function Pagination({page,onPage,loading=false,compact=false}:any){
 const current=page?.page||1,total=page?.total||0,size=page?.page_size||(compact?20:50),pages=page?.pages||1;
 return <div className={'query-pagination '+(compact?'compact':'')} aria-label="列表分页"><span>{loading&&!page?'加载中…':total?`${(current-1)*size+1}–${Math.min(current*size,total)} / ${total} 条`:'0 条'}</span><div><Button kind="small" aria-label="上一页" disabled={loading||current<=1} onClick={()=>onPage(current-1)}>{compact?'‹':'上一页'}</Button><span>{current} / {pages}</span><Button kind="small" aria-label="下一页" disabled={loading||current>=pages} onClick={()=>onPage(current+1)}>{compact?'›':'下一页'}</Button></div></div>
}
