import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
const configPath=process.env.WORKBENCH_PLUGIN_CONFIG;
const plugin=configPath?JSON.parse(readFileSync(resolve(configPath),'utf8')):null;
if(plugin){
 for(const key of ['service','baseId','authTable','proofField','origin'])if(!plugin[key])throw new Error('Plugin configuration missing '+key);
 if(!/^https:\/\/blk_[a-f0-9]+\.feishupkg\.com$/.test(plugin.origin))throw new Error('Exact Feishu plugin origin required');
 if(!plugin.service.startsWith('https://'))throw new Error('Plugin API must use HTTPS');
}
export default defineConfig({plugins:[react()],base:plugin?'./':'/',
 define:{__WORKBENCH_PLUGIN__:JSON.stringify(plugin)},
 server:{proxy:{'/api':'http://127.0.0.1:8798','/auth':'http://127.0.0.1:8798'}}});
