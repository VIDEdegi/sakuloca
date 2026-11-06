// Contract tests: stdio MCP, validation, bounded results, and named-pipe transport.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {callApp,handleMessage,invokeTool,tools} from '../mcp/server.mjs';

const root=fileURLToPath(new URL('../',import.meta.url));
async function fakeApp(audio=true){
  const directory=await fs.mkdtemp(path.join(root,'tmp','mcp-contract-'));
  const pipe=`\\\\.\\pipe\\SakuLoca-${process.pid}-${Date.now().toString(16)}${Math.floor(Math.random()*65536).toString(16)}`;
  const requests=[];
  const server=net.createServer(socket=>{
    let buffer='';socket.on('error',()=>{});
    socket.on('data',chunk=>{
      buffer+=chunk;const end=buffer.indexOf('\n');if(end<0)return;
      const request=JSON.parse(buffer.slice(0,end));requests.push(request);
      socket.end(JSON.stringify(request.method==='errors'?{error:'fixture error'}:{result:{method:request.method,params:request.params,...(request.method==='status'&&audio?{audio:{jobs:{}},analysis_kind:'image'}:{})}})+'\n');
    });
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(pipe,resolve)});
  await fs.writeFile(path.join(directory,'automation.json'),JSON.stringify({version:1,pipe,pid:process.pid}));
  return {directory,requests,close:async()=>{await new Promise(resolve=>server.close(resolve));await fs.rm(directory,{recursive:true,force:true})}};
}

test('MCP initializes without starting an app and advertises only intended tools',async()=>{
  const reply=await handleMessage({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-06-18'}});
  assert.equal(reply.result.protocolVersion,'2025-06-18');assert.ok(reply.result.capabilities.tools);
  const list=await handleMessage({jsonrpc:'2.0',id:2,method:'tools/list'});
  assert.equal(list.result.tools.length,6);
  assert.equal(await handleMessage({jsonrpc:'2.0',method:'notifications/initialized'}),null);
  assert.equal((await handleMessage({jsonrpc:'2.0',id:3,method:'missing'})).error.code,-32601);
});

test('only the running app is contacted; search defaults to ten compact results',async()=>{
  const app=await fakeApp();
  try{
    const result=await invokeTool('sakuloca_search',{text:'人工画像'},{data:app.directory});
    assert.equal(result.params.limit,10);assert.equal(result.method,'search');
    await invokeTool('sakuloca_analysis',{action:'pause'},{data:app.directory});
    assert.equal(app.requests[1].method,'pause');assert.deepEqual(app.requests[1].params,{});
  }finally{await app.close()}
});

test('invalid tool arguments never reach the pipe',async()=>{
  const app=await fakeApp();
  try{
    for(const args of [{limit:61},{limit:1.5},{filename:3},{format:'exe'},{unknown:true},{kind:'video'}])await assert.rejects(invokeTool('sakuloca_search',args,{data:app.directory}));
    await assert.rejects(invokeTool('sakuloca_analysis',{action:'delete'},{data:app.directory}));
    assert.equal(app.requests.length,0);
  }finally{await app.close()}
});

test('audio tools preserve modality and timestamps without sending media',async()=>{
  const app=await fakeApp();
  try{
    const result=await invokeTool('sakuloca_search',{kind:'audio',text:'電子音',format:'wav'},{data:app.directory});
    assert.equal(result.params.kind,'audio');assert.equal(result.params.limit,10);
    await invokeTool('sakuloca_analysis',{action:'start',kind:'audio'},{data:app.directory});
    assert.deepEqual(app.requests.filter(r=>r.method!=='status')[1],{version:1,method:'start',params:{kind:'audio'}});
    await invokeTool('sakuloca_analysis',{action:'pause',kind:'audio'},{data:app.directory});
    assert.deepEqual(app.requests.filter(r=>r.method!=='status')[2].params,{});
    for(const format of ['mp3','flac']){
      const filtered=await invokeTool('sakuloca_search',{kind:'audio',format},{data:app.directory});
      assert.equal(filtered.params.format,format);assert.equal(filtered.params.kind,'audio');
    }
  }finally{await app.close()}
});

test('audio calls on an older app cannot fall back to photo analysis',async()=>{
  const app=await fakeApp(false);
  try{
    for(const action of ['start','retry_errors'])await assert.rejects(invokeTool('sakuloca_analysis',{action,kind:'audio'},{data:app.directory}),/音声に対応していません/);
    await assert.rejects(invokeTool('sakuloca_search',{kind:'audio',text:'電子音'},{data:app.directory}),/音声に対応していません/);
    assert.ok(app.requests.every(r=>r.method==='status'));
    await invokeTool('sakuloca_analysis',{action:'pause',kind:'audio'},{data:app.directory});
    assert.equal(app.requests.at(-1).method,'pause');
  }finally{await app.close()}
});

test('application errors are MCP tool errors and missing tools are protocol errors',async()=>{
  const app=await fakeApp();
  try{
    const failed=await handleMessage({jsonrpc:'2.0',id:4,method:'tools/call',params:{name:'sakuloca_errors',arguments:{}}},{data:app.directory});
    assert.equal(failed.result.isError,true);assert.match(failed.result.content[0].text,/fixture error/);
    assert.equal((await handleMessage({jsonrpc:'2.0',id:5,method:'tools/call',params:{name:'delete'}})).error.code,-32602);
  }finally{await app.close()}
});

test('connection metadata cannot redirect to a remote pipe or arbitrary file',async()=>{
  const directory=await fs.mkdtemp(path.join(root,'tmp','mcp-invalid-'));
  try{
    for(const pipe of ['\\\\remote\\pipe\\SakuLoca-1-a','C:\\file','http://localhost:9999']){
      await fs.writeFile(path.join(directory,'automation.json'),JSON.stringify({version:1,pipe}));
      await assert.rejects(callApp('status',{}, {data:directory}),/不正/);
    }
  }finally{await fs.rm(directory,{recursive:true,force:true})}
});

test('real stdio process emits only JSON-RPC and can call the same pipe',async()=>{
  const app=await fakeApp();
  try{
    const child=spawn(process.execPath,[path.join(root,'mcp/server.mjs'),'--data',app.directory],{stdio:['pipe','pipe','pipe'],windowsHide:true});
    let stdout='',stderr='';child.stdout.on('data',chunk=>stdout+=chunk);child.stderr.on('data',chunk=>stderr+=chunk);
    const messages=[{jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-11-25'}},{jsonrpc:'2.0',method:'notifications/initialized'},{jsonrpc:'2.0',id:2,method:'tools/list'},{jsonrpc:'2.0',id:3,method:'tools/call',params:{name:'sakuloca_status',arguments:{}}}];
    child.stdin.end(messages.map(value=>JSON.stringify(value)+'\n').join(''));
    const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve)});
    assert.equal(code,0,stderr);assert.equal(stderr,'');
    const replies=stdout.trim().split('\n').map(line=>JSON.parse(line));
    assert.deepEqual(replies.map(reply=>reply.id),[1,2,3]);
    assert.equal(JSON.parse(replies[2].result.content[0].text).method,'status');
  }finally{await app.close()}
});
