// End-to-end control of a real packaged app/Worker using artificial fixtures only.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {callApp,invokeTool,handleMessage} from '../mcp/server.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
const data=path.resolve(process.argv[2]||'');
const testRoot=path.join(root,'data','release-tests')+path.sep;
assert.ok(data.startsWith(testRoot)&&data!==testRoot,'Use an isolated data/release-tests subdirectory');
const exe=path.join(root,'target','release','SakuLoca.exe');
const options={data};
const report={isolated_data:data,computer_use:false};
async function wait(check,timeout=180000){
  const deadline=Date.now()+timeout;let last;
  while(Date.now()<deadline){try{const value=await check();if(value)return value}catch(error){last=error}await new Promise(resolve=>setTimeout(resolve,300))}
  throw Error(`Timed out: ${last?.message||'condition not reached'}`);
}
const app=spawn(exe,[],{env:{...process.env,MEDIA_SEARCH_DATA:data},stdio:'ignore',windowsHide:true});
let exited=false;app.on('exit',()=>{exited=true});
try{
  const initial=await wait(()=>callApp('status',{},options));
  console.log('Connected to real app');
  assert.equal(path.resolve(initial.data_path),data);
  assert.equal(initial.automation.app_pid,app.pid);
  assert.equal(initial.running,false);
  const worker=initial.automation.worker_pid;
  const folders=await invokeTool('sakuloca_folders',{},options);assert.equal(folders.length,2);
  await assert.rejects(callApp('configure',{backend:'cpu'},options),/許可/);
  await assert.rejects(invokeTool('sakuloca_desktop',{action:'erase'},options));
  report.allowlist_enforced=true;
  const started=await invokeTool('sakuloca_analysis',{action:'start'},options);assert.equal(started.running,true);
  await assert.rejects(invokeTool('sakuloca_desktop',{action:'restart_worker'},options),/一時停止/);
  await invokeTool('sakuloca_analysis',{action:'pause'},options);
  await wait(async()=>!(await callApp('status',{},options)).running);
  report.pause_and_running_guard=true;
  console.log('Pause and running-state guard passed');
  await invokeTool('sakuloca_analysis',{action:'start'},options);
  const indexed=await wait(async()=>{const status=await callApp('status',{},options);return !status.running&&status});
  assert.equal(indexed.jobs.done,4);assert.equal(indexed.automation.worker_pid,worker);
  const result=await invokeTool('sakuloca_search',{text:'海辺の風景',limit:2},options);
  assert.equal(result.items[0].name,'coast.png');assert.equal(result.items.length,2);
  assert.ok(!('thumbnail' in result.items[0]));assert.ok(!('vector' in result.items[0]));
  const same=await invokeTool('sakuloca_status',{},options);assert.equal(same.automation.worker_pid,worker);
  report.same_worker_for_analysis_and_search=true;report.packaged_gpu_japanese_search=true;
  console.log('Same-Worker GPU search passed');
  const rpc=await handleMessage({jsonrpc:'2.0',id:7,method:'tools/call',params:{name:'sakuloca_errors',arguments:{}}},options);
  assert.equal(JSON.parse(rpc.result.content[0].text).total,0);report.mcp_tools_call=true;
  await invokeTool('sakuloca_desktop',{action:'restart_worker'},options);
  const recovered=await callApp('status',{},options);
  assert.notEqual(recovered.automation.worker_pid,worker);assert.equal(recovered.total,4);
  assert.equal((await invokeTool('sakuloca_search',{filename:'coast'},options)).items[0].name,'coast.png');
  report.worker_restart_preserves_index=true;
  console.log('Normal Worker restart passed');
  // Simulate a Worker crash, then recover through the API without opening UI.
  const crash=spawn('taskkill.exe',['/PID',String(recovered.automation.worker_pid),'/T','/F'],{stdio:'ignore',windowsHide:true});
  assert.equal(await new Promise(resolve=>crash.once('exit',resolve)),0);
  await invokeTool('sakuloca_desktop',{action:'restart_worker'},options);
  assert.equal((await callApp('status',{},options)).total,4);report.dead_worker_recovery=true;
  console.log('Dead Worker recovery passed');
  await invokeTool('sakuloca_desktop',{action:'quit'},options);
  await wait(()=>exited,30000);report.api_graceful_quit=true;
  await assert.rejects(callApp('status',{},options),/接続/);
  await fs.writeFile(path.join(data,'automation-test.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
}finally{
  if(!exited){try{await invokeTool('sakuloca_desktop',{action:'quit'},options)}catch{} }
}
