export const editorTabs=['facts','listing','brief'];
export const navigation=[
 ['facts','box','产品资料','ops'],['listing','edit','文案编辑','ops'],['brief','file','图需编辑','ops'],
 ['tasks','grid','制图任务','all'],['mine','user','我的制图','personal'],['review','check','图稿验收','review'],
 ['copyPublication','file','文案刊登','ops'],['imagePublication','upload','图片刊登','ops'],
];
export function visibleNavigation(roles:string[]){const ops=roles.some(r=>['admin','operator','supervisor'].includes(r)),artist=roles.some(r=>['artist','art_lead'].includes(r));return navigation.filter(n=>n[3]==='all'||n[3]==='ops'&&ops||n[3]==='personal'&&(artist||roles.includes('admin'))||n[3]==='review'&&(ops||artist))}
export function defaultTab(roles:string[]){return roles.includes('art_lead')?'tasks':roles.includes('artist')?'mine':'facts'}
export function legacyInFlight(i:any){const t=i.task||i.tasks?.[0];return !!t?.legacy_takeover&&['working','pending','revision'].includes(t.state)}
export function legacyArchived(i:any,section:string){const d=i.document||i,t=i.task||i.tasks?.[0],r=(d.body||d).legacy_edit_request||{};return !!t?.legacy_takeover&&t.state==='accepted'&&(r.task_id!==t.id||!r.sections?.includes(section))}
export function pendingEdit(i:any,tab:string){const d=i.document||i,t=i.task||i.tasks?.[0];if(i.completed||legacyInFlight(i)||legacyArchived(i,tab))return false;if(tab==='facts')return !d.facts_version;if(tab==='listing')return !d.listing_version;return !d.brief_version||!t||t.state==='withdrawn'||t.brief_version!==d.brief_version}
export function matchesProductQuery(i:any,query:string,names:Record<string,string>={}){const q=query.trim().toLowerCase();return !q||[i.name,i.code,i.owner_name,i.site,names[String(i.site||'').toUpperCase()]].filter(Boolean).join(' ').toLowerCase().includes(q)}
export function editorRows(items:any[],tab:string,scope:string,query='',site='',names:Record<string,string>={}){return items.filter(i=>!i.completed&&(scope==='all'||scope==='confirmed'&&(tab==='facts'?!!(i.document||i).facts_version:!pendingEdit(i,tab))||scope==='pending'&&pendingEdit(i,tab))&&matchesProductQuery(i,query,names)&&(!site||i.site===site))}
export function siteOptions(items:any[],names:Record<string,string>){return [...new Set(items.map(i=>String(i.site||'')).filter(Boolean))].sort((a,b)=>{const order=Object.keys(names),rank=(s:string)=>order.includes(s)?order.indexOf(s):order.length;return rank(a)-rank(b)||a.localeCompare(b)}).map(value=>({value,label:value+(names[value]?' · '+names[value]:'')}))}
export const copyState=(r:any)=>!r.publication?'ready':(!r.listing_version||r.listing_version!==r.published_version)?'ready':'published';
export function imageState(r:any){if(r.task?.state!=='accepted'||r.delivery?.result!=='accepted')return 'blocked';return r.images_uploaded?'uploaded':'ready'}
export function reviewVisible(r:any,user:any){return user?.roles?.some((role:string)=>['admin','operator','supervisor','art_lead'].includes(role))||r.task?.assignee_id===user?.id}
export function pageRows(items:any[],tab:string,user:any,previewArtist=''){return items.filter(r=>tab==='tasks'?r.task&&r.task.state!=='withdrawn':tab==='mine'?(previewArtist==='all'?!!r.task?.assignee_id&&r.task.state!=='pool':r.task?.assignee_id===(previewArtist||user?.id))&&r.task.state!=='withdrawn':tab==='review'?reviewVisible(r,user)&&r.delivery&&['pending','revision','accepted'].includes(r.task?.state):tab==='copyPublication'?r.listing_version||r.publication_count:tab==='imagePublication'?r.task?.state==='accepted'&&r.delivery?.result==='accepted'||r.image_history_count:false)}
export function badge(items:any[],tab:string,user:any,previewArtist=''){if(editorTabs.includes(tab))return items.filter(i=>pendingEdit(i,tab)).length;const rows=pageRows(items,tab,user,previewArtist);return rows.filter(r=>tab==='tasks'?user?.roles.some((x:string)=>['artist','art_lead'].includes(x))?r.task.state==='pool':r.task.state!=='accepted':tab==='mine'?r.task.state!=='accepted':tab==='review'?r.task.state==='pending':tab==='copyPublication'?copyState(r)!=='published':imageState(r)==='ready').length}


// A successful write is authoritative even if the following list read fails
// or briefly returns the old confirmation pointers.
export function completedEditorState(previous:any[],fresh:any[],detail:any,tab:string,scope:string,query:string,names:Record<string,string>={}){
 const d=detail.document,id=detail.item.id;
 const items=fresh.map(i=>i.id===id?{...i,...detail.item,revision:d.revision,
  facts_version:d.facts_version,listing_version:d.listing_version,brief_version:d.brief_version,
  legacy_edit_request:d.body?.legacy_edit_request,task:detail.tasks?.[0]||null}:i);
 const visible=editorRows(items,tab,scope,query,'',names);
 if(visible.some(i=>i.id===id))return {items,selected:id};
 const before=editorRows(previous,tab,scope,query,'',names),index=before.findIndex(i=>i.id===id);
 const next=index<0?undefined:before.slice(index+1).find(i=>visible.some(row=>row.id===i.id));
 return {items,selected:next?.id||visible[0]?.id||''};
}
