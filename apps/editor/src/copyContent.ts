export class CopyPermissionError extends Error {}
export async function copyContent(text:string,{allowed,write,legacy}:{allowed:()=>Promise<boolean>;write:(text:string)=>Promise<void>;legacy:(text:string)=>boolean}){
 if(!await allowed())throw new CopyPermissionError('当前飞书角色尚未获得复制权限，请刷新工作台后重试。');
 try{await write(text);return true}catch{return legacy(text)}
}
// Browser fallback still dispatches the normal copy event, so Feishu's copy
// permission enforcement remains in force. Never intercept or suppress it.
export function browserCopy(text:string){
 const previous=document.activeElement as HTMLElement|null;
 const input=previous instanceof HTMLInputElement||previous instanceof HTMLTextAreaElement?previous:null;
 const selection=input?[input.selectionStart,input.selectionEnd]:null;
 const area=document.createElement('textarea');area.value=text;area.readOnly=true;area.setAttribute('aria-label','复制图需');area.style.cssText='position:fixed;left:0;top:0;width:1px;height:1px;opacity:0';
 (document.querySelector('dialog[open]')||document.body).append(area);
 try{area.focus({preventScroll:true});area.select();return document.execCommand('copy')}catch{return false}finally{area.remove();previous?.focus({preventScroll:true});if(input&&selection)try{input.setSelectionRange(selection[0],selection[1])}catch{}}
}
