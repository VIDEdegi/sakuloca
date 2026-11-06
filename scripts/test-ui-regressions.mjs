// Exercise asynchronous UI behavior with controlled IPC replies, without user data.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';

const root=new URL('../',import.meta.url);
function fixture(){
  const requests=[];
  const context={
    console,setTimeout,clearTimeout,
    localStorage:{getItem:()=>null,setItem(){},removeItem(){}},
    matchMedia:()=>({matches:false}),styles:'',nothing:null,
    html:(strings,...values)=>({strings,values}),css:()=>'',
    customElement:()=>value=>value,state:()=>()=>{},property:()=>()=>{},
    rpc:(method,params)=>new Promise((resolve,reject)=>requests.push({method,params,resolve,reject})),
    LitElement:class{isConnected=true;renderRoot={querySelector:()=>null};updateComplete=Promise.resolve()},
  };
  vm.createContext(context);
  for(const [path,names] of [['src/app.ts',['Thumbnail','App']],['src/release-controls.ts',['ReleaseControls']]]){
    const source=fs.readFileSync(new URL(path,root),'utf8').replace(/^import .*$/mg,'').replace(/export class/g,'class');
    const expose=names.map(name=>`globalThis.${name}=${name}`).join(';');
    vm.runInContext(ts.transpileModule(source+'\n'+expose,{compilerOptions:{target:ts.ScriptTarget.ES2022,experimentalDecorators:true}}).outputText,context,{filename:fileURLToPath(new URL(path,root))});
  }
  return {context,requests};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

for(const failOld of [false,true]){
  const {context,requests}=fixture();
  const thumb=new context.Thumbnail();thumb.fileId=1;thumb.visible=true;
  const first=thumb.load();
  thumb.fileId=2;thumb.updated(new Map([['fileId',1]]));
  assert.equal(requests.length,1);
  const old=requests.shift();failOld?old.reject(Error('old image failed')):old.resolve({url:'old'});
  await first;
  assert.equal(requests.length,1);assert.equal(requests[0].params.id,2);
  requests.shift().resolve({url:'new'});await tick();
  assert.equal(thumb.url,'new');assert.equal(thumb.loading,false);
}
console.log('PASS thumbnail follows changed file after success or failure');

for(const failOld of [false,true]){
  const {context,requests}=fixture();const app=new context.App();
  app.query='old';const search=app.search();
  app.query='intermediate';await app.search(60);
  app.query='latest';app.pageSize=30;await app.search(90);
  assert.equal(requests.length,1);
  const old=requests.shift();failOld?old.reject(Error('obsolete error')):old.resolve({items:[{id:1}],total:1,offset:0});
  await tick();assert.equal(app.results.total,0);assert.equal(app.error,'');
  assert.equal(requests.length,1);
  const current=requests.shift();
  assert.equal(current.params.text,'latest');assert.equal(current.params.offset,90);assert.equal(current.params.limit,30);
  current.resolve({items:[{id:2}],total:200,offset:90});await search;
  assert.equal(app.results.items[0].id,2);assert.equal(app.busy,false);
}
console.log('PASS searches coalesce to latest conditions and page size');

{
  const {context,requests}=fixture();const app=new context.App(),controls=new context.ReleaseControls();
  controls.mode='assistant';controls.wizard=()=> 'wizard';controls.help=()=> 'help';controls.errorDialog=()=> 'errors';
  assert.ok(controls.render().values.includes('errors'));
  let opened=false;
  controls.shadowRoot={querySelector:selector=>selector==='#errors'?{open:false,showModal(){opened=true}}:null};
  controls.run=async action=>action();
  app.renderRoot={querySelector:()=>controls};
  app.openErrors();assert.equal(requests[0].method,'errors');
  requests.shift().resolve({total:1,items:[]});await tick();assert.equal(opened,true);
}
console.log('PASS footer error route has an assistant error dialog');

{
  const {context}=fixture();const app=new context.App();
  app.status={running:true,jobs:{done:99,error:2},progress:{phase:'indexing'}};
  app.refresh=async()=>{app.status={running:false,jobs:{done:100,error:2},progress:{phase:'idle',processed:100,errors:2}}};
  let message='',kind='';app.notify=(text,type)=>{message=text;kind=type};app.search=async()=>{};app.schedulePoll=()=>{};
  await app.poll();assert.ok(message.includes('100枚'));assert.ok(message.includes('2件'));assert.equal(kind,'error');
}
console.log('PASS completion notice uses full run totals');

{
  const {context,requests}=fixture();const app=new context.App();
  app.mediaKind='audio';app.query='電子音';
  const pending=app.search();assert.equal(requests[0].params.kind,'audio');
  requests.shift().resolve({items:[],total:0,offset:0});await pending;
  app.refresh=async()=>{};
  const start=app.index();assert.equal(requests[0].method,'start');assert.equal(requests[0].params.kind,'audio');
  requests.shift().resolve({});await start;
  const controls=new context.ReleaseControls();controls.mediaKind='audio';controls.run=async action=>action();
  controls.shadowRoot={querySelector:()=>null};
  const errors=controls.showErrors();assert.equal(requests[0].params.kind,'audio');
  requests.shift().resolve({total:0,items:[]});await errors;
  assert.equal(controls.errorKind,'audio');
}
console.log('PASS audio search, analysis and errors retain their modality');

{
  const {context,requests}=fixture();const app=new context.App();app.mediaKind='audio';
  let paused=false;app.renderRoot={querySelector:selector=>selector==='#audio-preview audio'?{pause(){paused=true}}:null};
  const pending=app.showPreview({id:3,kind:'audio',segment_id:8});
  assert.deepEqual({...requests[0].params},{id:3,segment_id:8});
  app.stopAudio();requests.shift().resolve({url:'obsolete',file:{id:3,kind:'audio',start_ms:25000}});await pending;
  assert.equal(app.audioPreview,undefined);assert.equal(paused,true);
  app.status={running:true,analysis_kind:'audio'};
  app.refresh=async()=>{app.status={running:false,analysis_kind:'audio',progress:{phase:'idle',processed:4,errors:0}}};
  let message='';app.notify=text=>message=text;app.search=async()=>{};app.schedulePoll=()=>{};
  await app.poll();assert.ok(message.includes('4区間'));assert.ok(!message.includes('4枚'));
}
console.log('PASS closed audio previews ignore late responses and report interval units');

{
  const {context,requests}=fixture();const app=new context.App();
  let paused=false;app.renderRoot={querySelector:selector=>selector==='#audio-preview audio'?{pause(){paused=true}}:null};
  app.audioPreview={url:'old',file:{id:3,kind:'audio',segment_id:8,next_segment_id:9}};
  const moved=app.moveAudio('next');assert.equal(paused,true);
  assert.deepEqual({...requests[0].params},{id:3,segment_id:9});
  requests.shift().resolve({url:'next',file:{id:3,kind:'audio',segment_id:9,start_ms:50000}});await moved;
  assert.equal(app.audioPreview.file.segment_id,9);assert.equal(app.audioPosition,50000);
  await app.moveAudio('next');assert.equal(requests.length,0);
}
console.log('PASS neighboring audio windows retain the same source and stop old playback');

{
  const {context,requests}=fixture();const app=new context.App();
  app.status={jobs:{}};await app.changeKind('audio');
  assert.equal(app.mediaKind,'image');assert.equal(requests.length,0);assert.ok(app.error.includes('音声対応版'));
  app.status={audio:{jobs:{}}};const changed=app.changeKind('audio');
  assert.equal(requests[0].params.kind,'audio');
  requests.shift().resolve({items:[],total:0,offset:0});await changed;
  assert.equal(app.mediaKind,'audio');
}
console.log('PASS old Workers cannot accidentally start photo work from audio mode');
