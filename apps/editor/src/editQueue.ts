export type EditScope = 'pending' | 'all';
export type Editing = {pending:boolean; todos:{stage:string;label:string}[]; stage:string};

export function editingFor(item:any):Editing {
  if (item.editing) return item.editing;
  // Also tolerate a cached list response during a rolling service update.
  const doc=item.document||item,task=item.task||item.tasks?.[0];
  const todos=[];
  if(!doc.facts_version) todos.push({stage:'facts',label:'待核对资料'});
  if(!doc.listing_version) todos.push({stage:'listing',label:'待确认文案'});
  if(!doc.brief_version||!task||task.state==='withdrawn'||task.brief_version!==doc.brief_version)
    todos.push({stage:'brief',label:doc.brief_version?'待提交图需':'待确认图需'});
  return {pending:todos.length>0,todos,stage:todos[0]?.stage||'listing'};
}

export function editRows(items:any[],scope:EditScope,query='',site='') {
  return items.filter(i=>(scope==='all'||editingFor(i).pending)
    &&(!query||(i.name+' '+i.code).toLowerCase().includes(query.toLowerCase()))&&(!site||i.site===site));
}
