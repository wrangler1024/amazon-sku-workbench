export function safeDownloadShortName(value:string){
 return Array.from(value.normalize('NFC').replace(/[\\/:*?"<>|_\u0000-\u001f\u007f\s]+/g,'-').replace(/^[. -]+|[. -]+$/g,'')).slice(0,24).join('').replace(/[. -]+$/g,'');
}
