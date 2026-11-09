// Only image metadata may change: never adopt document/task/selection fields.
export function mergeThumbnailRows(rows:any[],updates:any[]){
 const byId=new Map(updates.map(x=>[x.id,x]));let changed=false;
 const result=rows.map(row=>{
  const patch=byId.get(row.id);
  if(!patch||!row.thumbnail_pending||row.thumbnail_id)return row;
  const pending=patch.thumbnail_pending===true,id=patch.thumbnail_id||null;
  if(row.thumbnail_id===id&&row.thumbnail_pending===pending)return row;
  changed=true;return {...row,thumbnail_id:id,thumbnail_pending:pending};
 });
 return changed?result:rows;
}
