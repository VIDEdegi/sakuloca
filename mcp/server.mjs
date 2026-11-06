// Dependency-free stdio MCP adapter / CLI. Connects to the running app only.
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const empty={type:'object',properties:{},additionalProperties:false};
const string={type:'string'};
const kind={enum:['image','audio']};
const integer=(minimum,maximum)=>({type:'integer',minimum,...(maximum===undefined?{}:{maximum})});
const tool=(name,description,inputSchema,readOnlyHint=false)=>({name,description,inputSchema,annotations:{readOnlyHint,destructiveHint:!readOnlyHint,openWorldHint:false}});
export const tools=[
  tool('sakuloca_status','起動中のSakuLocaの解析進捗・件数・保存先・同一WorkerのPIDを取得。メディア本体は取得しない。',empty,true),
  tool('sakuloca_folders','登録済みフォルダーと有効状態・解析件数を取得。',empty,true),
  tool('sakuloca_errors','現在のモデルの解析エラーを最大50件取得。kindは既定image、音声はaudio。パスを含む。',{...empty,properties:{offset:integer(0),kind}},true),
  tool('sakuloca_search','ローカル写真またはWAV/MP3/FLAC音声の区間を検索。kindは既定image、音声はaudio。メディア本体は返さず名前・パス・時刻・スコア等だけ返す。',{...empty,properties:{kind,text:string,image:{type:'string',description:'写真の類似検索に使うローカル画像のパス。画像は送信しない。'},filename:string,format:{enum:['','jpg','jpeg','png','webp','wav','mp3','flac']},folder:string,after:string,before:string,sort:{enum:['date_desc','date_asc','name_asc','name_desc','size_desc','size_asc']},offset:integer(0),limit:integer(1,60)}},true),
  tool('sakuloca_analysis','解析を開始・一時停止・失敗分を再試行。kindで写真image/音声audioを指定。開始時の省略は前回の解析対象を再開。一時停止は実行中の対象共通。利用者の指示があるときだけ変更する。',{...empty,properties:{action:{enum:['start','pause','retry_errors']},kind},required:['action']}),
  tool('sakuloca_desktop','アプリの表示・Worker再起動・安全な完全終了。再起動/終了は解析停止後だけ。',{...empty,properties:{action:{enum:['show','restart_worker','quit']}},required:['action']}),
];

export async function dataDirectory(explicit){
  if(explicit)return path.resolve(explicit);
  if(!process.env.LOCALAPPDATA)throw Error('LOCALAPPDATAがありません。--dataでSakuLocaの保存先を指定してください。');
  const pointer=JSON.parse(await fs.readFile(path.join(process.env.LOCALAPPDATA,'LocalPhotoSearch','storage.json'),'utf8'));
  if(typeof pointer.data_path!=='string'||!path.isAbsolute(pointer.data_path))throw Error('SakuLocaの保存先設定が不正です。');
  return pointer.data_path;
}

export async function callApp(method,params={},options={}){
  const data=await dataDirectory(options.data);
  let endpoint;
  try{endpoint=JSON.parse(await fs.readFile(path.join(data,'automation.json'),'utf8'))}
  catch{throw Error('操作APIが見つかりません。MCP対応版のSakuLocaを起動してください。')}
  if(endpoint.version!==1||typeof endpoint.pipe!=='string'||!/^\\\\\.\\pipe\\SakuLoca-\d+-[a-f0-9]+$/.test(endpoint.pipe))throw Error('SakuLocaの操作API情報が不正です。');
  const payload=JSON.stringify({version:1,method,params})+'\n';
  if(Buffer.byteLength(payload)>64*1024)throw Error('リクエストが大きすぎます。');
  return new Promise((resolve,reject)=>{
    const socket=net.createConnection(endpoint.pipe);let buffer=Buffer.alloc(0),settled=false;
    const finish=(error,result)=>{if(settled)return;settled=true;clearTimeout(timer);socket.destroy();error?reject(error):resolve(result)};
    const timer=setTimeout(()=>finish(Error('SakuLocaの応答がタイムアウトしました。操作が完了している場合があるため状態を確認してください。')),options.timeout??180000);
    socket.on('connect',()=>socket.write(payload));
    socket.on('data',chunk=>{
      buffer=Buffer.concat([buffer,chunk]);
      if(buffer.length>4*1024*1024){finish(Error('応答が大きすぎます。'));return}
      const end=buffer.indexOf(10);if(end<0)return;
      try{const reply=JSON.parse(buffer.subarray(0,end).toString('utf8'));if(reply.error)finish(Error(String(reply.error)));else if(!Object.hasOwn(reply,'result'))finish(Error('操作APIの応答が不正です。'));else finish(null,reply.result)}catch(error){finish(error)}
    });
    socket.on('error',error=>finish(Error(`SakuLocaに接続できません（${error.code||'connection error'}）。アプリを起動し、同じWindowsユーザーで接続してください。${error.code==='EPERM'?'CodexのシェルからのCLIは、サンドボックス外のユーザー権限で実行してください。':''}`)));
    socket.on('close',()=>{if(!settled)finish(Error('SakuLocaとの接続が途中で切れました。操作後の状態を確認してください。'))});
  });
}

export function validateArguments(definition,args){
  if(!args||typeof args!=='object'||Array.isArray(args))throw Error('引数はオブジェクトで指定してください。');
  for(const key of Object.keys(args)){
    const schema=definition.inputSchema.properties[key],value=args[key];
    if(!schema)throw Error(`未対応の引数: ${key}`);
    if(schema.type==='string'&&typeof value!=='string')throw Error(`${key}は文字列で指定してください。`);
    if(schema.type==='integer'&&(!Number.isInteger(value)||value<schema.minimum||(schema.maximum!==undefined&&value>schema.maximum)))throw Error(`${key}が範囲外です。`);
    if(schema.enum&&!schema.enum.includes(value))throw Error(`${key}が不正です。`);
  }
  for(const key of definition.inputSchema.required||[])if(!Object.hasOwn(args,key))throw Error(`${key}を指定してください。`);
}

export async function invokeTool(name,args={},options={}){
  const definition=tools.find(item=>item.name===name);if(!definition)throw Error('未対応のツールです。');
  validateArguments(definition,args);
  let method=name.replace('sakuloca_',''),params=args;
  if(method==='analysis'){method=args.action;params=method==='pause'||args.kind===undefined?{}:{kind:args.kind}}
  if(method==='search')params={limit:10,...args};
  if(args.kind==='audio'&&method!=='pause'){
    const status=await callApp('status',{},options);
    if(!status.audio||!['image','audio'].includes(status.analysis_kind))throw Error('起動中のアプリは音声に対応していません。音声対応版のアプリとWorkerを使用してください。写真の解析は開始しません。');
  }
  return callApp(method,params,options);
}

export async function handleMessage(request,options={}){
  const response=(result)=>({jsonrpc:'2.0',id:request.id,result});
  const error=(code,message)=>({jsonrpc:'2.0',id:request?.id??null,error:{code,message}});
  if(!request||Array.isArray(request)||request.jsonrpc!=='2.0'||typeof request.method!=='string')return error(-32600,'Invalid Request');
  if(!Object.hasOwn(request,'id'))return null; // Initialized/cancel notifications have no reply.
  if(request.method==='initialize'){
    const supported=['2025-11-25','2025-06-18','2025-03-26','2024-11-05'];
    const requested=request.params?.protocolVersion;
    return response({protocolVersion:supported.includes(requested)?requested:supported[0],capabilities:{tools:{listChanged:false}},serverInfo:{name:'sakuloca-local',version:'1.0.0'},instructions:'起動中のSakuLocaだけを操作します。通常操作はこのMCPか同梱CLIを優先し、見た目の確認時だけ画面操作を使ってください。検索は既定10件。画像本体を返しませんが、結果の名前・パス・エラー文は会話に渡ります。'});
  }
  if(request.method==='ping')return response({});
  if(request.method==='tools/list')return response({tools});
  if(request.method==='tools/call'){
    if(!tools.some(tool=>tool.name===request.params?.name))return error(-32602,'Unknown tool');
    try{return response({content:[{type:'text',text:JSON.stringify(await invokeTool(request.params.name,request.params.arguments??{},options))}]})}
    catch(failure){return response({isError:true,content:[{type:'text',text:String(failure.message||failure)}]})}
  }
  return error(-32601,'Method not found');
}

export async function runStdio(options={}){
  let pending=Buffer.alloc(0),chain=Promise.resolve();
  const send=message=>{if(message)process.stdout.write(JSON.stringify(message)+'\n')};
  for await(const chunk of process.stdin){
    pending=Buffer.concat([pending,chunk]);
    if(pending.length>64*1024&&!pending.includes(10)){send({jsonrpc:'2.0',id:null,error:{code:-32700,message:'Message too large'}});process.exitCode=1;break}
    let end;
    while((end=pending.indexOf(10))>=0){
      const line=pending.subarray(0,end);pending=pending.subarray(end+1);
      chain=chain.then(async()=>{
        let request;
        try{if(line.length>64*1024)throw Error();request=JSON.parse(line.toString('utf8'))}catch{send({jsonrpc:'2.0',id:null,error:{code:-32700,message:'Parse error'}});return}
        send(await handleMessage(request,options));
      });
    }
  }
  await chain;
}

async function main(){
  const args=process.argv.slice(2),options={};
  const at=args.indexOf('--data');if(at>=0){if(!args[at+1])throw Error('--dataに保存先を指定してください。');options.data=args[at+1];args.splice(at,2)}
  if(args[0]==='--call'){
    const name=args[1]?.startsWith('sakuloca_')?args[1]:`sakuloca_${args[1]||'status'}`;
    console.log(JSON.stringify(await invokeTool(name,JSON.parse(args[2]||'{}'),options)));return;
  }
  if(args.length)throw Error('使い方: node server.mjs [--data 保存先] [--call status|search|folders|errors|analysis|desktop JSON引数]');
  await runStdio(options);
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{console.error(String(error.message||error));process.exitCode=1});
