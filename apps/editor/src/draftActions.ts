// Persist acknowledged sections in order so a failed later step can be retried
// with the current revision and the remaining local edits intact.
export async function persistDraft({data,draft,dirty,section,confirm=false,write,onSaved}:any){
 const priority=['facts','sources','keywords','selling_points','listing','brief'];
 const keys=[...new Set<string>([...dirty,...(section?[section]:[])])];
 keys.sort((a,b)=>priority.indexOf(a)-priority.indexOf(b));
 if(confirm&&section){keys.splice(keys.indexOf(section),1);keys.push(section)}
 let current=data;
 for(const key of keys){
  current=await write({revision:current.document.revision,section:key,body:draft[key],confirm:confirm&&key===section});
  onSaved(current,key);
 }
 return current;
}
