import {apiUrl,nativePlugin,pluginConfiguration,storeSession} from './connection';
import {createProofWhenReady,initializeProof} from './baseProof';
export const isBaseEmbedded=nativePlugin;
export function notifyBaseView(_ready:boolean){}
let pending:Promise<void>|null=null;
// Start loading the SDK with the app module, before React's startup effect.
const sdk=nativePlugin?import('@lark-opdev/block-bitable-api'):null;
if(sdk)void sdk.catch(()=>{}); // initializeBaseAccess reports any failure.
async function request(path:string,body:any={}){
 const response=await fetch(apiUrl(path),{method:'POST',credentials:nativePlugin?'omit':'same-origin',headers:{'Content-Type':'application/json','X-Requested-With':'amazon-workbench'},body:JSON.stringify(body),signal:AbortSignal.timeout(25000)});
 const result=await response.json();if(!response.ok)throw Object.assign(new Error(result.detail||'飞书身份识别暂未完成，请重试'),{status:response.status});return result;
}
export function initializeBaseAccess():Promise<void>{
 if(!nativePlugin)return Promise.resolve();
 if(pending)return pending;
 pending=(async()=>{
  const config=pluginConfiguration!;
  if(location.origin!==config.origin)throw new Error('请从飞书数据表中的编辑工作台打开');
  const {bitable,IOpenSegmentType,PermissionEntity,OperationType}=await sdk!;
  const ready=()=>bitable.base.getPermission({entity:PermissionEntity.Record,type:OperationType.Addable,param:{tableId:config.authTable}});
  const token=await initializeProof({baseId:config.baseId,origin:config.origin,
   selection:()=>bitable.base.getSelection(),
   table:()=>bitable.base.getTableById(config.authTable),
   permission:()=>createProofWhenReady(ready,async()=>true),
   challenge:()=>request('/auth/base/challenge'),
   write:(table,proof)=>table.addRecord({fields:{[config.proofField]:[{type:IOpenSegmentType.Text,text:proof}]}}),
   exchange:body=>request('/auth/base/exchange',body),
   stage:name=>{performance.mark('workbench:identity:'+name);if(name!=='start')performance.measure('workbench:identity:'+name,'workbench:identity:start','workbench:identity:'+name)},
  });
  storeSession(token);
 })().finally(()=>{pending=null});
 return pending;
}

export async function canCopyContent(){
 if(!nativePlugin)return true;
 const {bitable,PermissionEntity,OperationType}=await import('@lark-opdev/block-bitable-api');
 return bitable.base.getPermission({entity:PermissionEntity.Base,type:OperationType.Copyable});
}
