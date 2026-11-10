import {useWorkbenchPage,listParameters} from './useWorkbenchPage';
import {setPreviewContext} from './connection';
import {copyContent,browserCopy,CopyPermissionError} from './copyContent';
import ImageViewer,{type ViewerSelection} from './ImageViewer';
import React,{Suspense,lazy,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {createListRefresher,LIST_REFRESH_INTERVAL,reviewSucceeded} from './listRefresh';
import {createRoot} from 'react-dom/client';
import './prototype/theme.css';
import './prototype/styles.css';
import './service.css';
import './workbench-tasks.css';
import PublicationPage from './PublicationPage';
const Admin=lazy(()=>import('./Admin'));
import {initializeBaseAccess,isBaseEmbedded,notifyBaseView,canCopyContent} from './baseAccess';
import ActionFeedback from './ActionFeedback';
import {briefText,artworkPacket} from './briefHelpers';
import {supplierRemoval,componentFromSource} from './factsHelpers';
import {persistDraft} from './draftActions';
import SupplierPairing from './SupplierPairing';
import VisionPanel from './VisionPanel';
import CollectionPanel,{jobLabel,sourceLabel} from './CollectionPanel';
import WorkbenchHeader from './WorkbenchHeader';
import {completedEditorState,editorTabs,visibleNavigation,defaultTab,pendingEdit,legacyArchived,editorRows,pageRows,badge,matchesProductQuery} from './workbenchNavigation';
import WorkList from './WorkList';
import {api,apiRead,apiFetch,briefFields,briefImageCount,dateText,emptyBrief,emptyListing,fullCopy} from './api';
import {BriefPage,FactsPage,ListingView,ModelBar} from './EditorPages';
import {ArtworkPage,Gallery,QueueList,PreviewThumb} from './TaskPages';
import {Button,Field,Help,Icon,Modal,labels,languages,siteNames} from './prototype/ui';

const embedded=window.self!==window.top;

function App(){
 const [me,setMe]=useState<any>(null),[login,setLogin]=useState(false),[items,setItems]=useState<any[]>([]),[people,setPeople]=useState<any[]>([]),[data,setData]=useState<any>(null),[draft,setDraft]=useState<any>(null),[dirty,setDirty]=useState<string[]>([]),[selected,setSelected]=useState(''),[tab,setTab]=useState(!embedded&&location.hash==='#admin'?'admin':'facts'),[stage,setStage]=useState('facts'),[query,setQuery]=useState(''),[site,setSite]=useState(''),[filter,setFilter]=useState(''),[busy,setBusy]=useState(false),[notice,setNotice]=useState(''),[error,setError]=useState(''),[provider,setProvider]=useState('deepseek'),[modal,setModal]=useState(''),[zoom,setZoom]=useState<ViewerSelection|null>(null),[uploadIds,setUploadIds]=useState<string[]>([]),[note,setNote]=useState(''),[urgent,setUrgent]=useState(false),[due,setDue]=useState(''),[publicationVersion,setPublicationVersion]=useState('confirmed'),[registerNow,setRegisterNow]=useState(0);
 const openZoom=(id:string,ids:string[]=[id])=>setZoom({images:ids.map(value=>({src:'/api/assets/'+value,name:data?.assets?.find((a:any)=>a.id===value)?.name||'商品原图'})),index:Math.max(0,ids.indexOf(id))});
 const [factsOpen,setFactsOpen]=useState(false);
 const [previewArtist,setPreviewArtist]=useState('');
 const previewOnly=tab==='mine'&&!!previewArtist;
 const views=useRef<Record<string,any>>({});
 const [listViews,setListViews]=useState<Record<string,any>>({});
 const [manualCopy,setManualCopy]=useState('');
 const actionRunning=useRef(false);
 const listEpoch=useRef(0),listScroll=useRef<{x:number,y:number}|null>(null),returnScroll=useRef<Record<string,{x:number,y:number}>>({});
 const refreshScope=useRef<()=>string|null>(()=>null);
 const [listRefreshFailed,setListRefreshFailed]=useState(false);
 const pendingReviewSave=useRef<any>(null);
 const [bootReady,setBootReady]=useState(false);
 useEffect(()=>{notifyBaseView(bootReady||login)},[bootReady,login]);
 useEffect(()=>{const receive=(e:Event)=>setError((e as CustomEvent).detail);window.addEventListener('workbench-error',receive);return()=>window.removeEventListener('workbench-error',receive)},[]);
 const [progress,setProgress]=useState('');
 const [pairSource,setPairSource]=useState(''),[pairDirty,setPairDirty]=useState(false);
 const [jobRefreshError,setJobRefreshError]=useState('');
 const [editScope,setEditScope]=useState<string>('pending');
 const [editPage,setEditPage]=useState(1),[timeRange,setTimeRange]=useState('7'),[dateFrom,setDateFrom]=useState(''),[dateTo,setDateTo]=useState(''),[detailLoading,setDetailLoading]=useState(false);
 const editorDrafts=useRef(new Map<string,any>());
 const [historyLoading,setHistoryLoading]=useState(false);
 const [theme,setTheme]=useState(()=>localStorage.getItem('amazon-workbench-theme')||'forest');const selectionToken=useRef(0),editToken=useRef(0);
 const user=me?.user,roles=user?.roles||[],admin=roles.includes('admin'),lead=admin||roles.includes('art_lead'),artist=roles.includes('artist')||roles.includes('art_lead'),operator=admin||roles.includes('operator')||roles.includes('supervisor');
 const task=data?.tasks.find((t:any)=>!['accepted','withdrawn'].includes(t.state))||data?.tasks[0],locked=!!task&&!['accepted','withdrawn'].includes(task.state),canManage=!!data&&(admin||data.item.owner_id===user?.id),publishing=tab.endsWith('Publication'),editing=editorTabs.includes(tab),archived=!!data&&editing&&legacyArchived(data,tab),canEdit=canManage&&!archived;
 const [refreshing,setRefreshing]=useState(false),[connected,setConnected]=useState(true);
 const actorLabel=roles.includes('art_lead')?'美工负责人':artist?'内部美工':admin?'管理员':roles.includes('supervisor')?'运营主管':'运营';
 const pageQuery=listParameters(tab,{scope:editScope,query,range:timeRange,from:dateFrom,to:dateTo,page:editPage},listViews[tab]||{},user,previewArtist);
 const listing=useWorkbenchPage(bootReady&&tab!=='admin',JSON.stringify([user?.id,user?.roles,user?.groups]),pageQuery);
 const peopleReadAt=useRef(Date.now());
 const pageRef=useRef(listing);pageRef.current=listing;
 useEffect(()=>{if(listing.page)setItems(old=>[...listing.page.items,...old.filter(i=>i.id===selected&&!listing.page.items.some((r:any)=>r.id===i.id))])},[listing.page]);
 useEffect(()=>{if(bootReady&&editing&&listing.page&&!selected&&!detailLoading&&!busy&&listing.page.items.length)void select(listing.page.items[0].id,true,true)},[listing.page,selected,bootReady,editing]);

 useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),6000);return()=>clearTimeout(timer)},[notice]);
 useEffect(()=>{document.documentElement.dataset.amazonTheme=theme;localStorage.setItem('amazon-workbench-theme',theme)},[theme]);
 function adopt(d:any){setData(d);setDraft({...structuredClone(d.document.body),listing:structuredClone(d.document.body.listing||emptyListing),brief:structuredClone(d.document.body.brief||emptyBrief)});setDirty([]);setUploadIds([]);const current=d.tasks.find((t:any)=>t.state!=='withdrawn');setUrgent(current?.urgent||false);setDue(current?.due_date||'');}
 async function reload(){listEpoch.current++;const [result,artists]=await Promise.all([pageRef.current.refresh(false,undefined,true),apiRead('/api/people?refresh=true')]);if(!result&&tab!=='admin')throw new Error('列表刷新失败');setPeople(artists);return result?.items||[]}
 useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(dirty.length||uploadIds.length||[...editorDrafts.current.values()].some(x=>x.dirty.length)){e.preventDefault();e.returnValue=''}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn)},[dirty,uploadIds]);
 function stashEditor(){if(selected&&draft&&dirty.length&&editing)editorDrafts.current.set(selected,{data,draft,dirty,urgent,due})}
 useEffect(()=>{
  let alive=true;
  async function start(){
   setPreviewContext('');
   await initializeBaseAccess();
   // Identity gates all business data; team statistics do not gate the editor.
   const identity=apiRead('/api/me');
   void apiRead('/api/people').then(artists=>{if(alive)setPeople(artists)}).catch(()=>{if(alive)setError('团队成员暂未加载，请点击刷新数据重试')});
   const m=await identity;
   if(!embedded&&!m.user.roles.includes('admin'))throw new Error('独立管理后台仅供管理员使用，请从飞书数据表打开编辑工作台');
   if(!alive)return;
   const initialTab=!embedded&&m.user.roles.includes('admin')?'admin':defaultTab(m.user.roles);
   if(m.user.roles.includes('admin'))setPreviewArtist('all');
   setMe(m);setProvider(m.providers.deepseek?'deepseek':'openai');
   setPreviewContext(initialTab==='mine'&&m.user.roles.includes('admin')?'all':'');setTab(initialTab);
   const wanted=new URLSearchParams(location.search).get('item');
   if(wanted&&initialTab!=='admin')void select(wanted,true,editorTabs.includes(initialTab));
   setBootReady(true);
  }
  start().catch(e=>{if(alive){setLogin(true);setError(e.message)}});
  return()=>{alive=false};
 },[]);
 // Never adopt detail data from a background refresh.
 refreshScope.current=()=>bootReady&&['tasks','mine','review'].includes(tab)&&!selected&&!data&&!dirty.length&&!busy&&!refreshing&&!actionRunning.current&&!modal&&!factsOpen&&!zoom&&!manualCopy&&!document.hidden&&!document.querySelector('dialog[open]')?`${tab}:${selectionToken.current}:${listEpoch.current}:${previewArtist}:${listing.key}`:null;
 useEffect(()=>{
  if(!bootReady)return;
  const poll=createListRefresher({scope:()=>refreshScope.current(),
   read:async signal=>{const result=await pageRef.current.refresh(true,signal);if(!result)throw new Error('列表读取失败');return result},
   apply:()=>{listScroll.current={x:window.scrollX,y:window.scrollY};setListRefreshFailed(false);if(Date.now()-peopleReadAt.current>30000){peopleReadAt.current=Date.now();void apiRead('/api/people').then(setPeople).catch(()=>{})}},
   failed:()=>setListRefreshFailed(true)});
  const wake=()=>{void poll.refresh()};
  const timer=setInterval(wake,LIST_REFRESH_INTERVAL);
  window.addEventListener('focus',wake);window.addEventListener('pageshow',wake);document.addEventListener('visibilitychange',wake);
  wake();
  return()=>{poll.stop();clearInterval(timer);window.removeEventListener('focus',wake);window.removeEventListener('pageshow',wake);document.removeEventListener('visibilitychange',wake)};
 },[bootReady,tab,selected]);
 useLayoutEffect(()=>{const pos=listScroll.current;if(pos){listScroll.current=null;window.scrollTo({left:pos.x,top:pos.y,behavior:'instant'})}},[items,data]);
 function closeDetail(){selectionToken.current++;listEpoch.current++;setSelected('');setData(null);setDraft(null);setDirty([]);setUploadIds([]);setNote('');setZoom(null);setModal('');listScroll.current=returnScroll.current[tab]||{x:0,y:0}}
 // Job polling updates job state only, preserving unsaved edits and confirmed versions.
 const jobVersion=useRef('');
 async function refreshJobResults(){const id=selected;try{const d=await api('/api/items/'+id+'?compact=true');setData((old:any)=>old?.item.id===id?{...old,jobs:d.jobs,sources:d.sources,assets:d.assets}:old);setJobRefreshError('')}catch{setJobRefreshError('任务状态暂时读取失败，请点击刷新结果；后台任务会继续执行。')}}
 useEffect(()=>{const missing=modal.startsWith('job:')&&!data?.jobs.some((j:any)=>j.id===modal.slice(4));if(!selected||(!missing&&!data?.jobs.some((j:any)=>['waiting_sources','queued','running'].includes(j.state))))return;let alive=true,running=false;const id=selected;jobVersion.current='';const timer=setInterval(async()=>{if(running||document.hidden)return;running=true;try{const status=await api('/api/items/'+id+'/jobs/status');const signature=JSON.stringify(status);if(alive&&signature!==jobVersion.current){jobVersion.current=signature;const d=await api('/api/items/'+id+'?compact=true');if(alive)setData((old:any)=>old?.item.id===id?{...old,jobs:d.jobs,sources:d.sources,assets:d.assets}:old)}}catch{if(alive)setJobRefreshError('任务状态暂时读取失败，可点击刷新结果')}finally{running=false}},2500);return()=>{alive=false;clearInterval(timer)}},[selected,modal,data?.jobs.map((j:any)=>j.state).join(',')]);
 async function loadHistory(){if(historyLoading||!selected)return;const id=selected;setHistoryLoading(true);try{const full=await api('/api/items/'+id);setData((old:any)=>old?.item.id===id?{...old,versions:full.versions,jobs:full.jobs,history:full.history,compact:false}:old)}catch(e:any){setError(e.message)}finally{setHistoryLoading(false)}}
 async function refreshData(){if(actionRunning.current||refreshing)return;const selection=selectionToken.current,edit=editToken.current;listEpoch.current++;setRefreshing(true);setError('');try{const [identity,_,artists,current]=await Promise.all([apiRead('/api/me'),pageRef.current.refresh(),apiRead('/api/people?refresh=true'),selected&&!dirty.length&&!uploadIds.length&&!pendingReviewSave.current?api('/api/items/'+selected+(editing?'?compact=true':'')):Promise.resolve(null)]);setMe(identity);setPeople(artists);const unchanged=selection===selectionToken.current&&edit===editToken.current;if(current&&unchanged)adopt(current);setConnected(true);setNotice(current&&unchanged?'数据已刷新':'列表已刷新，当前编辑内容已保留')}catch(e:any){setConnected(false);setError(e.message)}finally{setRefreshing(false)}}
 function update(k:string,v:any){editToken.current++;setDraft((d:any)=>({...d,[k]:v}));setDirty(d=>Array.from(new Set([...d,k])))}
 function part(k:string,key:string,v:any){update(k,{...draft[k],[key]:v})}
 async function act(fn:()=>Promise<any>,message='操作已保存',working='正在处理，请稍候…',completeEditor=false){
  if(actionRunning.current)return false;
  actionRunning.current=true;listEpoch.current++;setBusy(true);setProgress(working);setError('');setNotice('');
  try{
   const r=await fn();if(r?.document){editorDrafts.current.delete(r.item.id);adopt(r)}
   let nextRows=items,listFailed=false;
   try{nextRows=await reload()}catch{listFailed=true}
   if(completeEditor&&r?.document){
    const next=completedEditorState(items,nextRows,r,tab,editScope,query,siteNames);setItems(next.items);
    if(next.selected!==selected){
     closeDetail();setFactsOpen(false);setPairSource('');setPairDirty(false);
     if(next.selected)await select(next.selected,true);
    }
   }
   if(listFailed)setError('操作已完成，但列表刷新失败；请点击刷新数据。');
   setNotice(message);return r||true;
  }catch(e:any){setError(e.message);return false}finally{actionRunning.current=false;setBusy(false);setProgress('')}
 }
 async function select(id:string,skipSave=false,targetEditor=editing){
  if(actionRunning.current&&!skipSave)return;
  if(!skipSave&&pendingReviewSave.current&&!await pendingReviewSave.current())return;
  // Automatic selection follows a successful save (or an already-stashed tab
  // switch); never put that render's pre-save draft back into the cache.
  if(!skipSave)stashEditor();const token=++selectionToken.current;setDetailLoading(true);setError('');setNotice('');
  try{const cached=targetEditor?editorDrafts.current.get(id):null;const d=cached?.data||await api('/api/items/'+id+(targetEditor?'?compact=true':''));if(token===selectionToken.current){setSelected(id);if(cached){setData(cached.data);setDraft(cached.draft);setDirty(cached.dirty);setUrgent(cached.urgent);setDue(cached.due)}else adopt(d);setNote('');setRegisterNow(0);return true}}catch(e:any){if(token===selectionToken.current)setError(e.message)}finally{if(token===selectionToken.current)setDetailLoading(false)}
 }
 async function changeTab(t:string,id?:string){
  if(actionRunning.current||t==='admin'&&(embedded||!admin))return;
  if(t!=='admin'&&!visibleNavigation(roles).some(n=>n[0]===t))return;
  if(pendingReviewSave.current&&!await pendingReviewSave.current())return;
  stashEditor();views.current[tab]={query,site,scope:editScope,selected,page:editPage,range:timeRange,from:dateFrom,to:dateTo};
  const v=views.current[t]||{query:'',site:'',scope:'pending',selected:'',page:1,range:'7',from:'',to:''};
  const target=id||(editorTabs.includes(t)?v.selected:'');
  const targetPreview=t==='mine'&&admin?(previewArtist||(!artist?'all':'')):'';setPreviewContext(targetPreview);if(t==='mine')setPreviewArtist(targetPreview);
  selectionToken.current++;setTab(t);if(editorTabs.includes(t))setStage(t);setQuery(id?'':v.query);setSite(v.site);setEditScope(id&&editorTabs.includes(t)?'all':v.scope);setTimeRange(id?'all':v.range);setDateFrom(v.from);setDateTo(v.to);setEditPage(id?1:v.page);setModal('');setFactsOpen(false);setNotice('');setSelected('');setData(null);setDraft(null);setDirty([]);
  if(target)await select(target,true,editorTabs.includes(t));
 }
 async function changePreviewArtist(id:string){
  if(busy||refreshing||!admin)return;
  if(id&&id!=='all'&&!people.some(p=>p.eligible&&p.id===id))return;
  if(!id&&!artist)return;
  if(pendingReviewSave.current&&!await pendingReviewSave.current())return;
  selectionToken.current++;setPreviewContext(id);setPreviewArtist(id);setListViews(old=>({...old,mine:{...old.mine,group:'',owner:''}}));setSelected('');setData(null);setDraft(null);setDirty([]);setUploadIds([]);setModal('');setZoom(null);setNotice('');setError('');
 }
 useEffect(()=>{if(previewArtist&&previewArtist!=='all'&&!people.some(p=>p.id===previewArtist&&p.eligible)){setPreviewArtist('');if(tab==='mine'){setPreviewContext('');setSelected('');setData(null);setDraft(null)}}},[people,previewArtist,tab]);
 async function persist(section?:string,confirm=false){
  return persistDraft({data,draft,dirty,section,confirm,
   write:(body:any)=>api('/api/items/'+selected+'/document','PUT',body),
   onSaved:(d:any,key:string)=>{setData(d);setDraft((old:any)=>({...old,[key]:structuredClone(d.document.body[key])}));setDirty(old=>old.filter(k=>k!==key))}
  });
 }
 const save=(s?:string,c=false)=>act(()=>persist(s,c),c?'资料已确认':'草稿已保存',c?'正在保存并确认资料…':'正在保存草稿…',c&&s===tab&&!factsOpen);
 const command=(action:string,payload:any={})=>act(()=>api('/api/items/'+selected+'/'+action,'POST',{revision:data.document.revision,data:payload}));
 const artwork=async(action:string,payload:any={},reviewRevision?:number)=>{const r=await act(()=>api('/api/artwork/'+task.id+'/'+action,'POST',{revision:reviewRevision??task.revision,data:payload}));if(reviewSucceeded(action,r)){const updated=r.tasks.find((t:any)=>t.id===task.id);if(updated)setItems(old=>old.map(i=>i.id===r.item.id?{...i,task:updated,delivery:r.deliveries.find((d:any)=>d.id===updated.current_delivery_id)}:i));closeDetail();setNotice(payload.result==='accepted'?'验收通过，已返回列表':'已退回返修，已返回列表')}if(r&&action==='withdraw'){setTab('brief');setEditScope('pending');setQuery('');setSite('');setStage('brief');setNotice('图需已撤回，商品已回到待编辑')}return r};
 async function reopen(section:string){const r=await act(async()=>{const d=await persist();return api('/api/items/'+selected+'/reopen-edit','POST',{revision:d.document.revision,data:{section}})},'已进入编辑','正在保存并进入编辑…');if(r){setTab(section);setEditScope('pending');setQuery('');setSite('');setStage(section);setModal('');setNotice('已进入编辑，商品已回到待编辑；历史版本和刊登记录已保留')}}
 async function changeEditScope(scope:string){setEditScope(scope);setEditPage(1);setQuery('')}
 function changeSearch(value:string){setQuery(value);setEditPage(1)}
 function changeTime(value:string,from='',to=''){setTimeRange(value);setDateFrom(from);setDateTo(to);setEditPage(1)}

 async function listTaskCommand(t:any,action:string,payload:any={}){return act(async()=>{await api('/api/artwork/'+t.id+'/'+action,'POST',{revision:t.revision,data:payload});return selected?api('/api/items/'+selected):true},'任务已更新')}
 async function claim(tasks:any[]){return act(async()=>{await api('/api/artwork/claim-batch','POST',{tasks:tasks.map(t=>({id:t.id,revision:t.revision}))});return selected?api('/api/items/'+selected):true},'已认领，可在我的制图中开工')}
 async function openWork(id:string,version='confirmed'){returnScroll.current[tab]={x:window.scrollX,y:window.scrollY};setPublicationVersion(version);return select(id)}
 async function copyListing(r:any,registered=false){await act(async()=>{const d=await api('/api/items/'+r.id);const current=d.publications.find((p:any)=>!p.withdrawn_at&&p.target_id===d.document.publication_target_id);const id=registered&&current?current.listing_version:d.document.listing_version;await copy(fullCopy(d.versions.find((v:any)=>v.id===id)?.body));return true},'文案已复制')}
 async function registerListing(r:any){if(await openWork(r.id))setRegisterNow(Date.now())}
 async function assign(assignee_id:string,tasks:any[]){return act(async()=>{await api('/api/artwork/assign','POST',{assignee_id,tasks:tasks.map(t=>({id:t.id,revision:t.revision}))});return selected?api('/api/items/'+selected):true},'任务已分配')}
 async function copy(text:string){setError('');try{if(await copyContent(text,{allowed:canCopyContent,write:value=>navigator.clipboard.writeText(value),legacy:browserCopy}))setNotice('已复制');else setManualCopy(text)}catch(e:any){setError(e instanceof CopyPermissionError?e.message:'暂时无法核验复制权限，请稍后重试')}}
 function version(id:string){return data?.versions.find((v:any)=>v.id===id)}
 function packet(){const b=version(task?.brief_version)?.body||draft?.brief||{};return artworkPacket(data.item.name,b,data.document.body.sources||[],task?.legacy_takeover?data.legacy_brief||'':'',data.item)}
 async function generate(kind:string){setJobRefreshError('');return act(async()=>{
  const d=await persist();
  if(!d.document.facts_version){setFactsOpen(true);setModal('');throw new Error('请核对并确认本品资料后，再生成文案或图需。草稿已保留。')}
  const job=await api('/api/items/'+selected+'/jobs/create','POST',{kind:'model',revision:d.document.revision,payload:{task:kind,provider}});
  setModal('job:'+job.job_id);return api('/api/items/'+selected);
 },'已提交：先采集来源资料，再生成候选，状态会自动刷新','正在保存草稿并提交生成…')}
 async function publishBrief(){return act(async()=>{
  const d=await persist('brief',true);
  return api('/api/items/'+selected+'/publish-brief','POST',{revision:d.document.revision,data:{urgent,due_date:due||null}});
 },'图需已确认并提交公共待认领','正在确认图需并提交制作…',tab==='brief'&&!factsOpen)}
 function updateManual(source:any,text:string){const rows=draft.sources||[];const found=rows.some((x:any)=>x.url===source.url);update('sources',found?rows.map((x:any)=>x.url===source.url?{...x,manual_text:text}:x):[...rows,{id:source.source_id,kind:source.kind,url:source.url,manual_text:text}])}
 async function removeSupplier(sourceId:string){const result=await act(async()=>{const d=await persist();const source=d.document.body.sources.find((s:any)=>s.id===sourceId);if(!source)throw new Error('基础商品已移除，请刷新数据');const preview=supplierRemoval(d.document.body.facts||{},source);return api('/api/items/'+selected+'/remove-supplier','POST',{revision:d.document.revision,data:{source_id:sourceId,included:preview.included}})},'基础商品已移除，请核对实际包含物并重新确认资料','正在保存并移除基础商品…');if(result)setModal('');return result}
 async function parseSupplier(url:string){const id=await readSupplier(url);if(id){setPairSource(id);setModal('pairing')}return id}
 async function openPairing(id=''){if(dirty.length){const r=await act(()=>persist(),'草稿已保存，可继续选择规格','正在保存草稿并打开规格…');if(!r)return}setPairSource(id);setModal('pairing')}
 async function readSupplier(url:string){try{const u=new URL(url);if(u.protocol!=='https:'||u.hostname!=='detail.1688.com'||!/^\/offer\/\d+\.html$/.test(u.pathname))throw new Error()}catch{setError('请输入完整的1688商品详情链接，例如 https://detail.1688.com/offer/数字.html');return false}let sourceId='';const ok=await act(async()=>{let d=await persist();let source=d.document.body.sources?.find((s:any)=>s.kind==='supplier'&&s.url===url);if(!source){source={id:crypto.randomUUID(),kind:'supplier',url};d=await api('/api/items/'+selected+'/document','PUT',{revision:d.document.revision,section:'sources',body:[...(d.document.body.sources||[]),source]});adopt(d)}sourceId=source.id;await api('/api/items/'+selected+'/jobs/create','POST',{kind:'source',revision:d.document.revision,payload:{...source,source_id:source.id}});return api('/api/items/'+selected)},'','正在保存草稿并解析基础商品…');return ok?sourceId:false}
 async function savePairing(payload:any){const r=await act(async()=>{const d=await persist();return api('/api/items/'+selected+'/supplier-pair','POST',{revision:d.document.revision,data:payload})},payload.apply_to_facts?'规格已带入，请重新核对并确认本品资料':'1688配对已保存');if(r&&payload.apply_to_facts){setModal('');if(tab!=='facts')setFactsOpen(true)}return r}
 async function resumeGeneration(mode:string){if(!candidateJob)return;await act(async()=>{const d=await persist();adopt(d);await api('/api/items/'+selected+'/jobs/'+candidateJob.id+'/resume','POST',{revision:d.document.revision,data:{mode}});return api('/api/items/'+selected)},mode==='retry'?'正在重试失败或不完整的链接':mode==='retry_images'?'正在重试图片':mode==='text_only'?'已选择仅用文字继续':'资料已提交，继续生成候选')}
 async function upload(files:File[]){await act(async()=>{const ids=[];for(const file of files){const form=new FormData();form.append('file',file);const a=await api('/api/items/'+selected+'/assets/upload','POST',form);ids.push(a.id)}const d=await api('/api/items/'+selected);setUploadIds(old=>[...old,...ids]);setData(d);return true},'原图已上传')}
 const mineItems=items;
 const rows=listing.page?.items||[];
 const tabs=visibleNavigation(roles);
 useEffect(()=>{if(site&&!items.some(i=>i.site===site))setSite('')},[items,site]);
 useEffect(()=>{const nav=document.querySelector('.tabs');if(!nav)return;const observer=new ResizeObserver(()=>document.documentElement.style.setProperty('--workbench-nav-height',nav.getBoundingClientRect().height+'px'));observer.observe(nav);return()=>observer.disconnect()},[bootReady]);
 const modelBar=(kind:string)=><ModelBar provider={provider} setProvider={setProvider} providers={me.providers} generate={generate} kind={kind} disabled={busy||!canEdit||!data.document.facts_version||dirty.includes('facts')||(kind==='brief'&&locked)}/>;
 const lockBanner=locked?<div className="note-box live-lock"><div><strong>本批次资料已锁定 · {labels[task.state]}</strong><p>{['pool','waiting'].includes(task.state)?'任务尚未开工，可撤回图需后修改产品事实和图需。':'已开工的制作依据保持锁定，请完成当前批次后再修改。'}</p></div>{canEdit&&['pool','waiting'].includes(task.state)&&<Button kind="small" disabled={busy} onClick={()=>{if(confirm('撤回后任务将退出当前制图队列，修改完成需重新提交。是否撤回图需并编辑？'))artwork('withdraw')}}>撤回图需并编辑</Button>}</div>:null;
 const removingSource=modal.startsWith('remove-source:')?draft?.sources?.find((s:any)=>s.id===modal.slice(14)):null;
 const candidateJob=modal.startsWith('job:')?data?.jobs.find((j:any)=>j.id===modal.slice(4)):null;

 function factsPage(){return <FactsPage key={selected} data={data} parseSupplier={parseSupplier} draft={draft} part={part} update={update} locked={locked} busy={busy} canEdit={canEdit} confirmed={!!data.document.facts_version&&!dirty.includes('facts')} save={save} openSources={()=>setModal('sources')} openCompetitors={()=>setModal('competitors')} openPairing={openPairing} openKeywords={()=>setModal('keywords')} openReferences={()=>setModal('references')} onRemoveSupplier={(id:string)=>setModal('remove-source:'+id)}/>}
 function sourcesModal(){const competitor=modal==='competitors',kind=competitor?'competitor':'supplier';return <><div className="section-divider"><h3>{competitor?'竞品参考链接':'1688货源资料'}</h3><Button kind="small" disabled={busy||!canEdit} onClick={()=>update('sources',[...(draft.sources||[]),{id:crypto.randomUUID(),kind,url:''}])}>＋添加{competitor?'竞品':'货源'}链接</Button></div>{competitor&&<p className="note-box neutral">参考竞品的作图风格、排版，以及标题和五点的表达重点；涉及本品材质、规格、数量和功能，仍以已确认产品资料为准。</p>}
 {(draft.sources||[]).map((s:any,i:number)=>{if(s.kind!==kind)return null;const job=data.jobs.find((j:any)=>j.kind==='source'&&j.source_url===s.url);return <article className="collection-source" key={s.id}><div className="live-source-row"><strong className="source-kind-label">{competitor?'竞品链接':'1688货源'}</strong><input aria-label={'来源链接 '+(i+1)} disabled={!canEdit||busy||(s.kind==='supplier'&&(locked||(draft.facts?.components||[]).some((r:any)=>componentFromSource(r,s))))} placeholder="https://…" value={s.url} onChange={e=>update('sources',draft.sources.map((x:any,j:number)=>i===j?{...x,url:e.target.value}:x))}/><Button kind="small" disabled={busy||!canEdit||!s.url.trim()} onClick={()=>act(async()=>{const d=await persist();await api('/api/items/'+selected+'/jobs/create','POST',{kind:'source',revision:d.document.revision,payload:{...s,source_id:s.id}});return api('/api/items/'+selected)},'资料读取已提交','正在保存草稿并读取资料…')}>读取</Button><a href={/^https:\/\//.test(s.url)?s.url:undefined} target="_blank" rel="noreferrer">打开</a><Button kind="text small" aria-label={'删除来源链接 '+(i+1)} disabled={busy||!canEdit||(s.kind==='supplier'&&locked)} onClick={()=>s.kind==='supplier'?setModal('remove-source:'+s.id):update('sources',draft.sources.filter((_:any,j:number)=>i!==j))}>删除</Button></div>
 {job?<p className="subtext">{sourceLabel({...job,status:job.result?.status})} · {job.error||job.result?.title||'状态将自动更新'}</p>:<p className="subtext">尚未读取，生成文案或图需时会自动采集</p>}
 <details className="disclosure" open={job?.state==='failed'||undefined}><summary>{competitor?'参考重点与手动补充':'手动补充资料'}</summary><textarea aria-label={'来源补充 '+(i+1)} rows={4} maxLength={12000} disabled={busy||!canEdit} value={s.manual_text||''} onChange={e=>updateManual({url:s.url,kind:s.kind,source_id:s.id},e.target.value)} placeholder={competitor?'例如：参考主图构图、配色与信息排版；借鉴标题关键词和五点重点。读取失败可粘贴竞品标题、五点及参考说明。':'读取失败可粘贴供应商资料；保存后会作为参考传入模型。'}/></details>
 {job?.result&&<details className="disclosure"><summary>查看已读取资料</summary><p>{job.result.specifications?.join('；')}</p><p>{job.result.bullets?.join('；')}</p><p>{job.result.description}</p><Gallery ids={(job.result.downloads||[]).map((a:any)=>a.asset_id).filter(Boolean)} assets={data.assets} zoom={openZoom}/></details>}</article>})}
 <div className="live-actions"><Button kind="primary" disabled={busy||!canEdit} onClick={()=>save('sources')}>保存{competitor?'竞品参考':'货源资料'}</Button></div></>}
 if(login)return <main className="workbench-loading"><p role="alert">{error||'暂时无法连接，请重试。'}</p><div className="workbench-loading-actions">{!isBaseEmbedded&&<a className="button primary" href="/auth/login" target="_blank" rel="noreferrer">飞书登录</a>}<Button onClick={()=>location.reload()}>{isBaseEmbedded?'重新连接':'已登录，刷新页面'}</Button></div></main>;
 if(!bootReady)return <main className="workbench-loading" role="status" aria-label="连接亚马逊编辑工作台"><span>连接亚马逊编辑工作台<span className="loading-dots" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span></span></main>;
 return <main className="app-shell"><WorkbenchHeader user={user} role={actorLabel} theme={theme} setTheme={setTheme} connected={connected} refreshing={refreshing} busy={busy} onRefresh={refreshData} admin={admin&&!embedded} onAdmin={()=>changeTab('admin')}/>
 <nav className="tabs" aria-label="工作区">{tabs.map(([id,icon,label])=><button className={tab===id?'active':''} key={id} disabled={busy} aria-current={tab===id?'page':undefined} onClick={()=>changeTab(id)}><Icon name={icon}/>{label}<span className="count">{listing.summary?.badges?.[id]??0}</span></button>)}</nav>

 {!modal&&<ActionFeedback error={error} notice={notice} progress={progress||(refreshing?'正在刷新数据…':'')} onClose={()=>{setError('');setNotice('')}}/>}
 {tab==='admin'&&admin&&!embedded?<section className="panel live-admin"><Suspense fallback={<p>正在加载管理后台…</p>}><Admin api={api} onUpdated={async()=>setMe(await apiRead('/api/me'))}/></Suspense></section>:<div inert={busy||undefined} aria-busy={busy} className={'workspace '+(!editing?'list-mode':'')}>
 {tab==='review'&&!operator&&<section className="panel admin-artwork-preview"><div><strong>验收进度与返修意见</strong><p>{lead?'查看团队交付':'查看本人交付'}；验收通过、退回返修和取消验收由内容负责人或管理员处理。</p></div></section>}
 {editing&&<QueueList items={rows} selected={selected} select={select} query={query} setQuery={changeSearch} scope={query.trim()?'all':editScope} onScopeChange={changeEditScope} pendingCount={listing.summary?.badges?.[tab]??'—'} confirmedCount={listing.summary?.facts_confirmed??'—'} mode={tab} totalCount={listing.summary?.editor_total??'—'} busy={busy} page={listing.page} loading={listing.loading} error={listing.error} onRetry={()=>listing.refresh()} onPage={setEditPage} timeRange={timeRange} dateFrom={dateFrom} dateTo={dateTo} onTime={changeTime} historySearch={()=>{const term=query;changeTab('copyPublication');setListViews(old=>({...old,copyPublication:{...old.copyPublication,state:'',query:term,page:1}}))}}/>}
 {!editing&&<section className="panel" hidden={!!data}>{['tasks','mine','review'].includes(tab)&&<p className="list-refresh-status" role="status">{listRefreshFailed?'自动更新暂时失败，稍后重试；也可点击刷新数据。':'每10秒自动更新 · 切回页面立即更新'}</p>}{listing.error&&<p className="note-box">{listing.error} <Button kind="text small" onClick={()=>listing.refresh()}>重试</Button></p>}<WorkList page={listing.page} loading={listing.loading} previewArtist={previewArtist} onPreviewArtist={changePreviewArtist} readOnly={previewOnly||tab==='mine'&&admin&&!artist||tab==='review'&&!operator} error={error} key={tab} tab={tab} view={listViews[tab]||{}} setView={(v:any)=>setListViews(old=>({...old,[tab]:v}))} onJump={changeTab} rows={rows} people={people} open={openWork} lead={lead} artist={artist} busy={busy} assign={assign} claim={claim} taskCommand={listTaskCommand} user={user} copyListing={copyListing} register={registerListing}/></section>}

 <section className="panel editor-shell" inert={detailLoading||undefined} hidden={!editing&&!data} aria-label="内容编辑工作区">{!data||!draft?editing?<div className="empty"><Icon name="box"/><h3>{detailLoading||listing.loading?'正在加载商品资料…':rows.length?'选择左侧商品，开始编辑':editScope==='pending'&&!query&&!site?'待编辑任务已完成':'暂无匹配商品'}</h3><p>{editScope==='pending'?'未完结商品可在全部任务中查看，刊登历史在文案刊登和图片刊登中查询。':'可按商品名称、SKU或站点查找。'}</p>{editScope==='pending'&&<Button kind="soft" onClick={()=>changeEditScope('all')}>查看全部任务</Button>}</div>:null:<>
 <div className={editing?'editor-toolbar':undefined}><div className="product-header"><div className="product-ident"><PreviewThumb id={items.find(x=>x.id===selected)?.thumbnail_id} name={data.item.name}/><div><p className="record-code">{data.item.task_number&&<span className="task-number">任务编号 {data.item.task_number}</span>}{data.item.code}{previewOnly&&<span className="pill neutral">只读预览 · {people.find(p=>p.id===task?.assignee_id)?.name}</span>}<span className="pill green"><Icon name="check"/>选品已通过</span></p><h2>{data.item.name} {task?.urgent&&<span className="pill amber">加急</span>}</h2><div className="product-meta"><span>{data.item.site} {siteNames[data.item.site]} · {languages[data.item.site]}</span><span>{data.item.group_name}</span><span>内容负责人 {items.find(i=>i.id===selected)?.owner_name||data.item.review_snapshot?.['提交运营']?.[0]?.name||'已绑定运营'}</span></div></div></div><div className="header-actions">{!editing&&<Button icon="list" onClick={async()=>{if(pendingReviewSave.current&&!await pendingReviewSave.current())return;closeDetail()}}>返回列表</Button>}{editing?<>{canManage&&<Button icon="edit" disabled={busy} onClick={()=>setModal('reopen')}>修改</Button>}<Button icon="history" onClick={()=>{setModal('history');void loadHistory()}}>版本记录</Button><Button icon="save" disabled={busy||!canEdit} onClick={()=>save()}>保存草稿</Button></>:publishing&&canEdit?<Button icon="edit" disabled={busy} onClick={()=>reopen('listing')}>修改文案</Button>:null}</div></div>
 {editing?<div className="editor-context-bar"><span className={'pill '+(archived||data.document.facts_version&&!dirty.includes('facts')?'green':'amber')}>{archived?'原流程 · 已验收':data.document.facts_version&&!dirty.includes('facts')?'产品资料已确认':'产品资料待核对'}</span>{tab!=='facts'&&<strong>{tab==='listing'?'文案编辑':'图需编辑'}</strong>}<div className="context-links">{tab!=='listing'&&<Button kind="text small" onClick={()=>changeTab('listing',selected)}>文案编辑 →</Button>}{tab!=='brief'&&<Button kind="text small" onClick={()=>changeTab('brief',selected)}>图需编辑 →</Button>}{data.document.listing_version&&<Button kind="text small" onClick={()=>changeTab('copyPublication',selected)}>文案刊登 →</Button>}{task&&<Button kind="text small" onClick={()=>changeTab('tasks',selected)}>制图任务 →</Button>}</div></div>:<div className="locked-content-bar"><Icon name="check"/>{publishing?'刊登内容与操作记录':'图需已确认 · 制作批次 '+task?.batch}</div>}

 </div><div className="editor-content">{editing?<>{selected&&!rows.some((r:any)=>r.id===selected)&&!listing.loading&&<div className="note-box neutral">当前商品不在本页，编辑内容已保留。<Button kind="text small" onClick={()=>{setQuery(data.item.code);setEditScope('all');setEditPage(1)}}>查找当前任务</Button><Button kind="text small" onClick={()=>changeTab('copyPublication',selected)}>查看刊登记录</Button></div>}{archived&&<p className="note-box neutral">本商品原流程图稿已验收，资料保留在全部任务中。需要重新编辑时，点击顶部“修改”选择对应内容。</p>}{['facts','brief'].includes(stage)&&lockBanner}{stage==='facts'&&factsPage()}{stage==='listing'&&<><div className="section-head"><h2><Icon name="edit"/>Listing 文案编辑<Help text="手动填写或粘贴后可直接确认文案；使用AI生成前需先确认产品资料。"/></h2><a className="sop-link" href={import.meta.env.VITE_LISTING_SOP_URL||"./listing-sop.html"} target="_blank" rel="noreferrer"><Icon name="file"/>编辑 SOP</a></div>{!archived&&modelBar('listing')}<ListingView compact={archived} value={draft.listing} editable={canEdit} onChange={(k:string,v:any)=>part('listing',k,v)} copy={copy} generate={generate} facts={draft.facts} keywords={draft.keywords} confirmed={!!data.document.facts_version}/><section className="submission-card"><div><h3>图片制作</h3><p className="subtext">{labels[task?.state]||'尚未提交图需'}</p></div><Button kind="soft" onClick={()=>changeTab('brief',selected)}>编辑图需 / 提交制作</Button></section></>}{stage==='brief'&&<BriefPage archived={archived} brief={draft.brief} part={part} onChange={(value:any)=>update('brief',value)} item={data.item} locked={locked} busy={busy} canEdit={canEdit} copy={copy} save={save} confirmed={!!data.document.brief_version&&!dirty.includes('brief')} urgent={urgent} setUrgent={setUrgent} due={due} setDue={setDue} modelBar={!archived&&modelBar('brief')} publish={publishBrief}/>}
 {data.jobs.some((j:any)=>j.kind==='model')&&<details className="live-candidates candidate-history" onToggle={e=>{if(e.currentTarget.open&&data.compact)void loadHistory()}}><summary>生成记录 · {data.jobs.filter((j:any)=>j.kind==='model').length} 次</summary><div className="candidate-history-body"><div className="section-divider"><h3>历史候选</h3><Button kind="small" onClick={refreshJobResults}>刷新结果</Button></div>{data.jobs.filter((j:any)=>j.kind==='model').map((j:any)=><div className="live-candidate-row" key={j.id}><strong>{j.task==='brief'?'图需':'文案'}</strong><span className={'pill '+(j.state==='failed'?'amber':'blue')}>{jobLabel(j.state)}</span><span className="subtext">{dateText(j.created_at)}</span><Button kind="small" onClick={()=>setModal('job:'+j.id)}>查看结果</Button></div>)}</div></details>}</>:publishing?<PublicationPage mode={tab} onJump={(t:string)=>changeTab(t,selected)} error={error} key={selected} data={data} task={task} canEdit={canEdit} busy={busy} command={command} copy={copy} zoom={openZoom} initialVersion={publicationVersion} registerNow={registerNow} openArtwork={()=>changeTab('tasks',selected)}/>:<ArtworkPage key={selected+':'+task?.id+':'+task?.current_delivery_id} readOnly={previewOnly||tab==='mine'&&admin&&!artist||tab==='review'&&!operator} mode={tab} onJump={changeTab} error={error} beforeLeave={pendingReviewSave} data={data} task={task} packet={packet()} people={people} user={user} canEdit={canEdit} lead={lead} artist={artist} busy={busy} command={artwork} assign={assign} upload={upload} uploadIds={uploadIds} setUploadIds={setUploadIds} note={note} setNote={setNote} copy={copy} zoom={openZoom}/>}</div>
 {editing&&!archived&&<footer className={'footer-actions sticky-editor-actions '+(stage==='brief'?'brief-submit-footer':'')}><p><Icon name={dirty.length?'edit':'check'}/>{dirty.length?'有未保存的修改':data.sync?.state==='failed'?'飞书同步失败，资料已保存到后台':data.sync?.state==='succeeded'?'已保存并同步飞书':'资料已保存到后台'}</p><div className="footer-buttons"><Button disabled={busy||!canEdit||(locked&&stage!=='listing')} onClick={()=>save(stage)}>保存草稿</Button>{stage==='listing'&&<Button kind="primary" disabled={busy||!canEdit} onClick={()=>save('listing',true)}>确认文案，准备刊登</Button>}{stage==='facts'&&<Button kind="primary" disabled={busy||!canEdit||locked} onClick={()=>save('facts',true)}>确认本品资料</Button>}{stage==='brief'&&<><label className="footer-due">交期（选填）<input aria-label="交付截止日期（选填）" type="date" value={due} disabled={busy||!canEdit||locked} onChange={e=>setDue(e.target.value)}/></label><label className="review-check"><input type="checkbox" checked={urgent} disabled={busy||!canEdit||locked} onChange={e=>setUrgent(e.target.checked)}/>加急</label><Button kind="primary" disabled={busy||!canEdit||locked} onClick={publishBrief}>确认图需并提交制作</Button></>}</div></footer>}</>}
 </section></div>}
 <footer className="page-foot"><span>亚马逊编辑工作台</span><span>{me.environment==='production'?'正式版 · 已连接生产表':'联调版 · 已连接测试表'}</span></footer>
 {factsOpen&&data&&!modal&&<Modal title="产品资料" wide className="shared-facts-modal" onClose={()=>setFactsOpen(false)} footer={<><Button onClick={()=>setFactsOpen(false)}>返回编辑</Button><Button disabled={busy||!canEdit||locked} onClick={()=>save('facts')}>保存草稿</Button><Button kind="primary" disabled={busy||!canEdit||locked} onClick={async()=>{if(await save('facts',true))setFactsOpen(false)}}>确认本品资料</Button></>}><ActionFeedback inside error={error} notice={notice} progress={progress} onClose={()=>setError('')}/>{lockBanner}{factsPage()}</Modal>}
 {modal&&data&&<Modal className={modal==='pairing'?'supplier-modal':''} title={removingSource?'移除基础商品':modal==='pairing'?'1688 商品规格选择':modal==='reopen'?'修改内容':modal==='sources'?'1688货源资料':modal==='competitors'?'竞品参考':modal==='keywords'?'关键词表':modal==='references'?'产品参考资料':modal==='history'?'版本记录':modal==='publication'?'登记刊登结果':modal==='confirmed'?'本批次确认内容':'对比并采用候选版本'} onClose={()=>{if(busy)return;if(modal==='pairing'&&pairDirty&&!confirm('有未保存的规格选择或链接，关闭将放弃修改。继续吗？'))return;setModal('')}} wide={modal==='pairing'||modal==='sources'||modal==='competitors'||modal.startsWith('job:')}>
 <ActionFeedback inside error={error} notice={notice} progress={progress} onClose={()=>{setError('');setNotice('')}}/><div inert={busy||undefined} aria-busy={busy}>
 {removingSource&&<><p>移除该基础商品及其 <strong>{supplierRemoval(draft.facts||{},removingSource).count}</strong> 条组成明细。</p><p className="subtext">其他基础商品及手动补充资料保留；移除后请核对实际包含物并重新确认产品资料。</p><div className="live-actions"><Button disabled={busy} onClick={()=>setModal('')}>取消</Button><Button kind="primary" disabled={busy||!canEdit||locked} onClick={()=>removeSupplier(removingSource.id)}>确认移除</Button></div></>}
 {modal==='reopen'&&<><p>选择本次要修改的内容，商品将回到待编辑。已刊登文案和历史制作版本保留。</p><div className="reopen-options">{[['listing','修改文案','修改后重新确认文案，制图继续进行。'],['brief','修改图需',locked?'将撤回未开工任务，修改后重新提交制作。':'修改后重新确认图需并提交制作。'],['facts','修改产品资料',locked?'将撤回未开工任务；资料、文案与图需均需重新确认。':'资料、文案与图需均需重新确认。']].map(([section,label,hint])=><button key={section} type="button" disabled={busy||!canManage||(section!=='listing'&&locked&&!['pool','waiting'].includes(task.state))} onClick={()=>reopen(section)}><strong>{label}</strong><span>{section!=='listing'&&locked&&!['pool','waiting'].includes(task.state)?'当前批次已开工，请完成制作与验收后再修改。':hint}</span></button>)}</div></>}
 {modal==='pairing'&&<SupplierPairing data={data} initialSourceId={pairSource} busy={busy} disabled={!canEdit||locked} onRead={readSupplier} onSave={savePairing} onDirty={setPairDirty} onManual={()=>{if(!pairDirty||confirm('未保存的规格选择将放弃，继续吗？')){setModal('');if(tab!=='facts')setFactsOpen(true);setTimeout(()=>document.getElementById('sales-product-info')?.scrollIntoView({behavior:'smooth',block:'start'}),0)}}}/>}
 {['sources','competitors'].includes(modal)&&sourcesModal()}
 {modal==='keywords'&&<><p>粘贴关键词，每行一个词组；或导入 TXT、CSV、TSV 文件。</p><input type="file" accept=".txt,.csv,.tsv" aria-label="导入关键词文件" onChange={async e=>{const file=e.target.files?.[0];if(file){if(file.size>500000){setError('关键词文件请控制在500KB内');return}update('keywords',await file.text())}}}/><textarea aria-label="关键词表" rows={12} value={draft.keywords||''} onChange={e=>update('keywords',e.target.value)}/><div className="live-actions"><Button kind="primary" disabled={busy||!canEdit} onClick={async()=>{if(await save('keywords'))setModal('')}}>保存关键词</Button></div></>}
 {modal==='references'&&<><h3>已保存的人工补充资料</h3><p className="subtext">来自运营录入或历史资料带入，1688解析结果另存于各基础商品。</p><div className="candidate-block">{draft.facts?.source_text||'尚未补充使用说明；1688采集原文可在基础商品的属性与素材中查看'}</div><Button onClick={()=>setModal('sources')}>管理来源链接</Button></>}
 {modal==='history'&&<>{historyLoading&&<p>正在加载历史版本…</p>}{data.versions.map((v:any)=><details className="version-row" key={v.id}><summary>{({facts:'产品事实',listing:'Listing文案',brief:'制图需求'} as any)[v.kind]} · {dateText(v.created_at)}</summary>{v.kind==='listing'?<ListingView value={v.body} copy={copy} compact/>:<div className="candidate-block">{v.kind==='brief'?briefText(v.body):[v.body.sales_name,v.body.specification,v.body.quantity+' '+(v.body.unit||''),v.body.material,v.body.dimensions,v.body.included,v.body.uses].filter(Boolean).join('\n')}</div>}</details>)}{!data.versions.length&&<p>尚无已确认版本，确认后会自动保留版本记录。</p>}</>}
 {modal==='confirmed'&&<><div className="candidate-block">{packet()}</div><Button icon="copy" onClick={()=>copy(packet())}>复制图需</Button></>}

 {modal.startsWith('job:')&&Array.isArray(candidateJob?.collection)&&<><CollectionPanel rows={candidateJob?.collection||[]} draft={draft} updateManual={updateManual} resume={resumeGeneration} busy={busy} editable={canEdit} waiting={candidateJob?.state==='waiting_input'&&candidateJob?.result?.vision?.state!=='waiting_images'}/></>}
 {modal.startsWith('job:')&&<VisionPanel job={candidateJob} busy={busy} editable={canEdit} resume={resumeGeneration}/>}
 {modal.startsWith('job:')&&(!candidateJob||['waiting_sources','queued','running'].includes(candidateJob.state)?<div className="generation-loading"><span className="spinner"/><div>{candidateJob?.state==='waiting_sources'?'正在采集货源与竞品资料…':candidateJob?.state==='queued'?(candidateJob.error||'正在排队，等待空闲生成通道…'):candidateJob?.result?.vision?.state==='preparing'?'正在准备图片…':candidateJob?.result?.vision?.state==='submitted'?'正在识别图片并生成内容…':'正在生成候选内容…'}<p className="subtext">{jobRefreshError||'可关闭窗口，任务继续在后台执行。'}</p><Button kind="small" onClick={refreshJobResults}>刷新结果</Button></div></div>:candidateJob.state==='waiting_input'?<p className="subtext">可关闭窗口，稍后从“生成候选”继续此任务。</p>:candidateJob.state==='failed'?<><div className="note-box"><strong>生成失败</strong><p>{candidateJob.error}</p><p className="subtext">{dateText(candidateJob.finished_at)} · 本次没有可采用的完整候选</p></div><div className="live-actions"><Button onClick={refreshJobResults}>刷新结果</Button><Button kind="primary" disabled={busy||!canEdit} onClick={()=>generate(candidateJob.task||'brief')}>重新生成</Button></div></>:<><div className="note-box neutral">{candidateJob.result.provider} · {candidateJob.result.model} · 候选内容请核对后采用</div>{candidateJob.result.candidate.bullets?<ListingView value={candidateJob.result.candidate} copy={copy} compact/>:<div className="candidate-block">{briefText(candidateJob.result.candidate)}</div>}<div className="live-actions"><Button onClick={()=>setModal('')}>保留当前版本</Button><Button kind="primary" disabled={!canEdit||candidateJob.result.document_revision!==data.document.revision} onClick={()=>{const k=candidateJob.result.candidate.bullets?'listing':'brief';if(k==='brief'&&locked){setError('请先撤回未开工图需，再采用新图需');return}update(k,candidateJob.result.candidate);setStage(k);setTab(k);setModal('');setNotice('候选已放入编辑稿，请核对后保存确认')}}>采用候选版本</Button></div>{candidateJob.result.document_revision!==data.document.revision&&<p className="subtext">产品资料已更新，请重新生成候选。</p>}</>)}
 </div></Modal>}
 {manualCopy&&<Modal title="复制内容" onClose={()=>setManualCopy('')} footer={<Button onClick={()=>setManualCopy('')}>关闭</Button>}><textarea aria-label="待复制内容" className="copy-packet" readOnly rows={16} value={manualCopy} onFocus={e=>e.target.select()}/></Modal>}
 {zoom&&<ImageViewer {...zoom} onClose={()=>setZoom(null)}/>}
 </main>
}
createRoot(document.getElementById('root')!).render(<App/>);
