export type PluginConfiguration={service:string;baseId:string;authTable:string;proofField:string;origin:string};
declare const __WORKBENCH_PLUGIN__:PluginConfiguration|null;
export const pluginConfiguration=typeof __WORKBENCH_PLUGIN__==='undefined'?null:__WORKBENCH_PLUGIN__;
export const nativePlugin=!!pluginConfiguration;
const sessionKey='amazon-workbench-session:'+ (pluginConfiguration?.service||'web');
let token=nativePlugin?sessionStorage.getItem(sessionKey)||'':'';
export function sessionToken(){return token}
export function storeSession(value:string){token=value;if(nativePlugin)sessionStorage.setItem(sessionKey,value)}
export function apiUrl(path:string){if(!/^\/(api|auth)\//.test(path))throw new Error('请求路径无效');return (pluginConfiguration?.service||'')+path}

let previewArtist='';
export function previewContext(){return previewArtist}
export function setPreviewContext(value:string){previewArtist=value}
