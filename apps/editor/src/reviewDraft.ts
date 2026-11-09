export const REVIEW_SAVE_DELAY=3000;
export type ReviewValue={note:string;image_notes:Record<string,string>};
export type ReviewSaveState={dirty:boolean;saving:boolean;error:string};

// Only acknowledged snapshots are marked saved. A late response never replaces
// newer keystrokes, and flush serializes the final snapshot before navigation.
export function createReviewDraft({value,revision,write,changed=()=>{}}:{
 value:ReviewValue;revision:number;
 write:(revision:number,value:ReviewValue,idempotencyKey:string)=>Promise<{revision:number}>;
 changed?:(state:ReviewSaveState)=>void;
}){
 let current=structuredClone(value),saved=JSON.stringify(value),pending:Promise<boolean>|undefined,error='',stopped=false;
 let attempt:{value:ReviewValue;revision:number;key:string}|undefined;
 const state=()=>({dirty:JSON.stringify(current)!==saved,saving:!!pending,error});
 const emit=()=>{if(!stopped)changed(state())};
 async function save(){
  if(stopped)return false;
  if(pending)return pending;
  if(!state().dirty)return true;
  attempt??={value:structuredClone(current),revision,key:crypto.randomUUID()};
  const sent=attempt.value,expected=attempt.revision,key=attempt.key;
  pending=(async()=>{
   try{
    const result=await write(expected,sent,key);
    if(stopped)return false;
    revision=result.revision;saved=JSON.stringify(sent);attempt=undefined;error='';return true;
   }catch(e:any){if(!stopped)error=e?.message||'意见暂未保存，请重试';return false}
   finally{pending=undefined;emit()}
  })();
  emit();return pending;
 }
 return {
  state,value:()=>structuredClone(current),revision:()=>revision,
  update(value:ReviewValue){current=structuredClone(value);error='';emit()},
  save,
  async flush(){while(!stopped&&(pending||state().dirty)){if(!await save())return false}return !stopped},
  reset(value:ReviewValue,nextRevision:number){if(state().dirty||pending||stopped)return false;current=structuredClone(value);saved=JSON.stringify(value);revision=nextRevision;error='';emit();return true},
  stop(){stopped=true},
 };
}
