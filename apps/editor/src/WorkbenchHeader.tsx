import React from 'react';
import {Icon,themes} from './prototype/ui';

export default function WorkbenchHeader({user,role,theme,setTheme,connected,refreshing,busy,onRefresh,admin,onAdmin}:any){
 const scope=user.groups?.length?user.groups.join('、'):['管理员','美工负责人','内部美工'].includes(role)?'全部小组':'已授权商品';
 const embedded=window.self!==window.top;
 return <header className="hero workbench-hero">
  <div className="workbench-heading"><p className="eyebrow">AMAZON · EDITOR WORKSPACE</p><h1>亚马逊编辑工作台</h1><p className="hero-copy">从商品资料到图文交付，一处完成。</p>
   {!embedded&&admin&&<nav className="hero-shortcuts" aria-label="Web管理入口"><button type="button" onClick={onAdmin}>管理后台<Icon name="arrow"/></button></nav>}
  </div>
  <section className="hero-status workbench-status" aria-label="连接与工作身份"><span className={'status-dot '+(connected?'online':'offline')}/><strong>{refreshing?'正在刷新数据':connected?'数据已连接':'数据连接异常'}</strong><small className="hero-identity" title={`${user.name} · ${role} · ${scope}`}>{user.name} · {role} · {scope}</small><button type="button" className="ghost-button" onClick={onRefresh} disabled={busy||refreshing}><Icon name="refresh"/>{refreshing?'刷新中…':'刷新数据'}</button><div className="theme-picker"><span>主题色</span><div className="theme-options" role="group" aria-label="主题色">{themes.map(([id,label,color])=><button type="button" className="theme-option" key={id} style={{background:color}} title={label} aria-label={label} aria-pressed={theme===id} onClick={()=>setTheme(id)}/>)}</div></div></section>
 </header>
}
