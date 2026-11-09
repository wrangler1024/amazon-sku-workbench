import React,{useEffect,useId,useRef} from "react";
const icons={
    edit:'<path d="m16 3 5 5-12 12-6 1 1-6zM14 5l5 5"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    check:'<path d="m5 12 4 4L19 6"/>', circle:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    copy:'<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M15 8V3H3v13h5"/>',
    sparkle:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5zM20 2v4M18 4h4"/>',
    refresh:'<path d="M20 7a8 8 0 1 0 0 10M20 3v5h-5"/>',
    download:'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    upload:'<path d="M12 16V4m-5 5 5-5 5 5M4 16v5h16v-5"/>',
    file:'<path d="M14 3H5v18h14V8zM14 3v6h5M8 13h8M8 17h6"/>',
    image:'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 5 5 3-3 4 4"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>',
    save:'<path d="M4 3h13l3 3v15H4zM8 3v6h8V3M8 21v-7h8v7"/>',
    box:'<path d="m12 3 9 5-9 5-9-5zM3 8v10l9 5 9-5V8M12 13v10M7 6l10 5"/>',
    link:'<path d="m9 15 6-6M8 17l-1 1a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0M16 7l1-1a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(0 -1) scale(.95)"/>',
    person:'<circle cx="12" cy="7" r="4"/><path d="M4 21v-3a8 8 0 0 1 16 0v3"/>',
    flag:'<path d="M5 21V3m0 1c5-4 9 4 15 0v10c-6 4-10-4-15 0"/>',
    list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    external:'<path d="M14 3h7v7M10 14 21 3M11 3H3v18h18v-8"/>',
    history:'<path d="M3 11a9 9 0 1 1 2 7M3 3v8h7M12 7v5l4 2"/>',
  };

export function Icon({name="file"}:any){return <svg className="icon" viewBox="0 0 24 24" aria-hidden="true" dangerouslySetInnerHTML={{__html:icons[name]||icons.file}}/>}
export function Button({children,icon,kind='',...props}:any){return <button type="button" className={'button '+kind} {...props}>{icon&&<Icon name={icon}/>} {children}</button>}
export function Help({text,label='说明'}:any){
 const trigger=useRef<HTMLSpanElement>(null),bubble=useRef<HTMLSpanElement>(null),id=useId();
 function hide(){const node=bubble.current;if(node?.matches(':popover-open'))node.hidePopover()}
 function show(){const node=bubble.current,anchor=trigger.current;if(!node||!anchor)return;node.showPopover();const r=anchor.getBoundingClientRect(),width=Math.min(280,innerWidth-24);node.style.width=width+'px';const height=node.getBoundingClientRect().height;node.style.left=Math.max(12,Math.min(r.left,innerWidth-width-12))+'px';node.style.top=(r.top>=height+10?r.top-height-8:Math.min(r.bottom+8,innerHeight-height-12))+'px'}
 useEffect(()=>{window.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);return()=>{window.removeEventListener('scroll',hide,true);window.removeEventListener('resize',hide)}},[]);
 return <span ref={trigger} className="help-tip live-help" tabIndex={0} role="note" aria-label={label} aria-describedby={id} onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide} onKeyDown={e=>{if(e.key==='Escape'){hide();e.stopPropagation()}}}>?<span ref={bubble} id={id} popover="manual" role="tooltip" className="live-help-bubble">{text}</span></span>
}

export function Field({label,value,onChange,rows=0,placeholder='',type='text',className='',disabled=false}:any){return <label className={className}>{label}{rows?<textarea rows={rows} disabled={disabled} value={value??''} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/>:<input disabled={disabled} type={type} value={value??''} placeholder={placeholder} onChange={e=>onChange(e.target.value)}/>}</label>}
export function Modal({title,children,footer,onClose,wide=false,className=''}:any){const ref=useRef<HTMLDialogElement>(null),closeRef=useRef(onClose);closeRef.current=onClose;useEffect(()=>{ref.current?.showModal();const onKey=(e:KeyboardEvent)=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeRef.current()}};const node=ref.current;node?.addEventListener('keydown',onKey);return()=>node?.removeEventListener('keydown',onKey)},[]);return <dialog className={[wide?'live-wide-modal':'',className].join(' ')} ref={ref} onCancel={e=>{e.preventDefault();e.stopPropagation();onClose()}} onClick={e=>{if(e.target===ref.current)onClose()}}><div className="modal-head"><h2>{title}</h2><Button icon="close" kind="icon-only" aria-label="关闭弹窗" onClick={onClose}/></div><div className="modal-body">{children}</div>{footer&&<div className="modal-footer">{footer}</div>}</dialog>}
export const themes=[['forest','森林绿','#2b654c'],['ocean','深海蓝','#3a73aa'],['amber','暖橙','#ab652e'],['violet','紫罗兰','#7e58a9'],['slate','石墨青','#547982'],['dark','暗黑','#25322e']];
export const siteNames:any={US:'美国',CA:'加拿大',UK:'英国',AU:'澳大利亚',MX:'墨西哥',AE:'阿联酋',SA:'沙特',JP:'日本'};
export const languages:any={US:'美式英语',CA:'加拿大英语',UK:'英式英语',AU:'澳式英语',MX:'西班牙语',AE:'英语',SA:'英语',JP:'日语'};
export const labels:any={pool:'待认领',waiting:'待制图',working:'制图中',pending:'待验收',revision:'待返修',accepted:'已验收',withdrawn:'已撤回'};
