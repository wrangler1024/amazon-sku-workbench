// Description source is plain text. Export only Amazon's basic line-break
// markup; encode literal markup so product text never becomes active HTML.
export function descriptionHtml(value:string):string{
 return String(value||'').replace(/\r\n?/g,'\n').replace(/<\/?br\s*\/?\s*>/gi,'\n')
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
  .replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/\n/g,'<br/>');
}
