import React from 'react';
import {Button,Icon} from './prototype/ui';

export default function ActionFeedback({error,notice,progress,onClose,inside=false}:any){
 const text=error||progress||notice;
 if(!text)return null;
 return <div className={`action-feedback ${inside?'inside-dialog':'viewport-feedback'} ${error?'is-error':progress?'is-progress':'is-success'}`} role={error?'alert':'status'} aria-live={error?'assertive':'polite'} aria-atomic="true">
  {progress&&!error?<span className="spinner"/>:<Icon name={error?'info':'check'}/>}<div><strong>{error?'操作未完成':progress?'处理中':'操作完成'}</strong><p>{text}</p></div>
  {!progress&&<Button kind="icon-only" icon="close" aria-label="关闭操作提示" onClick={onClose}/>}
 </div>
}
