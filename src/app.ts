import {LitElement,html,css,nothing} from 'lit';
import {customElement,state,property} from 'lit/decorators.js';
import {styleMap} from 'lit/directives/style-map.js';
import {getCurrentWindow} from '@tauri-apps/api/window';
import type {UnlistenFn} from '@tauri-apps/api/event';
import {choose,rpc,openFile,bytes,date,unixDate,basename,timecode,desktop,copyText,type Folder,type MediaFile,type SearchResponse,type Status,type SearchSort,type MediaKind} from './api';
import {styles} from './styles';
import {initWindowState} from './window-state';
import './release-controls';
import './update-controls';
import type {ReleaseControls} from './release-controls';

@customElement('media-thumbnail')
class Thumbnail extends LitElement {
  @property({type:Number}) fileId=0;
  @state() private url='';
  @state() private failed=false;
  private observer?:IntersectionObserver;
  private loading=false;
  private attempts=0;
  private visible=false;
  static styles=css`:host{display:block;overflow:hidden}img{width:100%;height:100%;object-fit:cover;display:block}span{height:100%;display:grid;place-items:center;color:#7f8b9b;font:12px 'Segoe UI',sans-serif}span[role=button]{cursor:pointer;padding:8px;text-align:center}`;
  connectedCallback(){
    super.connectedCallback();
    this.observer=new IntersectionObserver(entries=>{
      if(entries.some(e=>e.isIntersecting)){
        this.visible=true;this.observer?.disconnect();void this.load()
      }
    },{rootMargin:'250px'});
    this.observer.observe(this)
  }
  disconnectedCallback(){this.observer?.disconnect();this.visible=false;super.disconnectedCallback()}
  updated(changed:Map<string,unknown>){
    if(changed.has('fileId')&&changed.get('fileId')!==undefined){
      this.url='';this.failed=false;this.attempts=0;
      if(this.visible)void this.load()
    }
  }
  private async load(){
    if(this.loading||!this.fileId)return;
    this.loading=true;
    const id=this.fileId;
    try{
      const result=await rpc<{url:string}>('preview',{id,thumbnail:true});
      if(this.isConnected&&this.fileId===id){this.url=result.url;this.failed=false;this.attempts=0}
    }catch{
      if(!this.isConnected||this.fileId!==id)return;
      if(this.attempts<2){
        this.attempts++;
        const expected=this.attempts;
        setTimeout(()=>{if(this.isConnected&&this.fileId===id&&expected===this.attempts)void this.load()},1200*expected)
      }else this.failed=true
    }finally{
      this.loading=false;
      if(this.isConnected&&this.visible&&this.fileId!==id)void this.load()
    }
  }
  render(){
    if(this.url)return html`<img src=${this.url} alt="" loading="lazy" decoding="async">`;
    if(this.failed)return html`<span role="button" tabindex="0" title="クリックで再試行" @click=${(event:Event)=>{event.stopPropagation();this.failed=false;this.attempts=0;void this.load()}}>画像を読み込めません<br>再試行</span>`;
    return html`<span>読み込み中</span>`
  }
}

@customElement('media-search-app')
class App extends LitElement {
  static styles=styles;
  @state() private folders:Folder[]=[];
  @state() private status?:Status;
  @state() private results:SearchResponse={items:[],total:0,offset:0,semantic:false};
  @state() private query='';
  @state() private imageQuery='';
  @state() private mediaKind:MediaKind='image';
  @state() private audioPreview?:{url:string;file:MediaFile};
  @state() private audioPosition=0;
  private audioRequest=0;
  @state() private referenceUrl='';
  @state() private filename='';
  @state() private format='';
  @state() private dateAfter='';
  @state() private dateBefore='';
  @state() private folderFilter='';
  @state() private sort:SearchSort='date_desc';
  @state() private pageSize=(()=>{const n=Number(localStorage.getItem('display-page-size'));return [30,60,120,200].includes(n)?n:60})();
  @state() private pageInput='';
  @state() private view:'grid'|'list'='grid';
  @state() private error='';
  @state() private dismissedError='';
  @state() private busy=false;
  @state() private preview?:{url:string;file:MediaFile};
  @state() private previewIndex=-1;
  @state() private zoom=1;
  @state() private previewBaseWidth=0;
  @state() private theme=localStorage.getItem('theme')||'system';
  @state() private recent=this.loadRecent();
  @state() private toast='';
  @state() private toastKind:'info'|'error'='info';
  @state() private notice?:{version:string;notes:string};
  @state() private dragging=false;
  private pollTimer?:ReturnType<typeof setTimeout>;
  private toastTimer?:ReturnType<typeof setTimeout>;
  private polling=false;
  private pendingSearch?:number;
  private searched=false;
  private windowCleanup?:()=>void;
  private dragCleanup?:UnlistenFn;
  private themeMedia=matchMedia('(prefers-color-scheme: dark)');
  private themeListener=()=>{if(this.theme==='system')this.applyTheme()};
  private keyboard=(event:KeyboardEvent)=>{
    const preview=this.renderRoot.querySelector<HTMLDialogElement>('#preview');
    if(preview?.open){
      if(event.key==='ArrowLeft'){event.preventDefault();void this.movePreview(-1);return}
      if(event.key==='ArrowRight'){event.preventDefault();void this.movePreview(1);return}
      if(event.key==='+'||event.key==='='){event.preventDefault();this.zoom=Math.min(4,this.zoom+.25);return}
      if(event.key==='-'){event.preventDefault();this.zoom=Math.max(.25,this.zoom-.25);return}
      if(event.key==='0'){event.preventDefault();this.zoom=1;return}
    }
    if((event.ctrlKey||event.metaKey)&&event.key==='k'){event.preventDefault();this.renderRoot.querySelector<HTMLInputElement>('#query')?.focus()}
    else if(event.ctrlKey&&event.key==='w'){event.preventDefault();void this.run(()=>desktop('close'))}
    else if(event.key==='F1'){event.preventDefault();void this.help()}
  };
  private loadRecent(){
    try{const value=JSON.parse(localStorage.getItem('recent-searches')||'[]');return Array.isArray(value)?value.filter(item=>typeof item==='string').slice(0,8):[]}catch{return[]}
  }
  connectedCallback(){
    super.connectedCallback();
    window.addEventListener('keydown',this.keyboard);
    this.themeMedia.addEventListener('change',this.themeListener);
    void this.initialize();
    this.schedulePoll();
    this.applyTheme();
    void initWindowState().then(cleanup=>{if(this.isConnected)this.windowCleanup=cleanup;else cleanup()}).catch(()=>{});
    void this.watchDrop();
  }
  disconnectedCallback(){
    super.disconnectedCallback();
    window.removeEventListener('keydown',this.keyboard);
    this.themeMedia.removeEventListener('change',this.themeListener);
    clearTimeout(this.pollTimer);clearTimeout(this.toastTimer);
    this.windowCleanup?.();this.dragCleanup?.();
    this.stopAudio();
  }
  private applyTheme(){const dark=this.theme==='dark'||this.theme==='system'&&this.themeMedia.matches;this.setAttribute('theme',dark?'dark':'light');localStorage.setItem('theme',this.theme)}
  private async watchDrop(){
    try{
      this.dragCleanup=await getCurrentWindow().onDragDropEvent(event=>{
        if(event.payload.type==='enter'||event.payload.type==='over'){this.dragging=true;return}
        this.dragging=false;
        if(event.payload.type==='drop'){
          const path=event.payload.paths[0];
          if(path)void this.dropPath(path)
        }
      });
      if(!this.isConnected){this.dragCleanup();this.dragCleanup=undefined}
    }catch{}
  }
  private async dropPath(path:string){
    const extension=path.split('.').pop()?.toLowerCase()||'';
    if(['jpg','jpeg','png','webp'].includes(extension)){await this.run(()=>this.setImageQuery(path));return}
    await this.run(async()=>{
      if(!confirm(`「${basename(path)}」を検索対象フォルダーとして追加しますか？`))return;
      this.folders=await rpc<Folder[]>('add_folder',{path});
      await this.refresh();this.notify('フォルダーを追加しました')
    })
  }
  private async initialize(){try{await this.refresh();await this.search()}catch(e){this.error=String(e)}}
  private async run(action:()=>Promise<unknown>){try{await action()}catch(e){this.error=String(e);this.dismissedError=''}}
  private async refresh(){const [status,folders]=await Promise.all([rpc<Status>('status'),rpc<Folder[]>('folders')]);this.status=status;this.folders=folders}
  private schedulePoll(delay?:number){
    clearTimeout(this.pollTimer);
    const running=!!this.status?.running||this.status?.progress.phase==='pausing';
    this.pollTimer=setTimeout(()=>void this.poll(),delay??(running?1500:5000))
  }
  private async poll(){
    if(this.polling||this.busy){this.schedulePoll();return}
    this.polling=true;
    try{
      const wasRunning=!!this.status?.running;
      await this.refresh();
      if(wasRunning&&!this.status?.running){
        const added=this.status?.progress.processed||0;
        const failures=this.status?.progress.errors||0;
        if(this.status?.progress.phase==='error'){
          this.notify(this.status.progress.last_error||'解析でエラーが発生しました。設定のエラー一覧を確認してください。','error')
        }else if(failures>0){
          this.notify(`解析が終了しました。${added}${this.status?.analysis_kind==='audio'?'区間':'枚'}を追加し、${failures}件のエラーがあります。`,'error')
        }else if(added>0){
          this.notify(`解析が完了しました。${added}${this.status?.analysis_kind==='audio'?'区間':'枚'}を追加しました。`)
        }else{
          this.notify('解析が終了しました。')
        }
        await this.search(this.results.offset)
      }
    }catch(e){this.error=String(e);this.dismissedError=''}
    finally{this.polling=false;this.schedulePoll()}
  }
  private notify(message:string,kind:'info'|'error'='info'){
    this.toast=message;this.toastKind=kind;
    clearTimeout(this.toastTimer);
    this.toastTimer=setTimeout(()=>this.toast='',8000)
  }
  private async addFolder(){await this.run(async()=>{const path=await choose('folder');if(!path)return;this.folders=await rpc<Folder[]>('add_folder',{path});await this.refresh()})}
  private async folderAction(folder:Folder,action:string){await this.run(async()=>{if(action==='remove'&&!confirm(`「${folder.path}」を検索対象から外します。元のファイルは変更しません。`))return;const disabling=action==='enable'&&!!folder.enabled;await rpc('folder_action',{id:folder.id,action,enabled:!folder.enabled});if((action==='remove'||disabling)&&this.folderFilter===String(folder.id))this.folderFilter='';await this.refresh();await this.search()})}
  private async index(){await this.run(async()=>{await rpc('start',{kind:this.mediaKind});await this.refresh()})}
  private async pause(){await this.run(async()=>{await rpc('pause');await this.refresh()})}
  private async search(offset=0){
    this.pendingSearch=offset;
    if(this.busy)return;
    this.busy=true;this.error='';
    try{
      while(this.pendingSearch!==undefined){
        const nextOffset=this.pendingSearch;
        this.pendingSearch=undefined;
        try{
          const result=await rpc<SearchResponse>('search',{
            kind:this.mediaKind,text:this.query,image:this.imageQuery,filename:this.filename,format:this.format,
            after:this.dateAfter,before:this.dateBefore,folder:this.folderFilter,sort:this.sort,
            offset:nextOffset,limit:this.pageSize
          });
          if(this.pendingSearch===undefined){
            this.results=result;this.searched=true;
            this.renderRoot.querySelector('.results')?.scrollTo({top:0})
          }
        }catch(e){if(this.pendingSearch===undefined){this.error=String(e);this.dismissedError=''}}
      }
    }finally{this.busy=false}
  }
  private async setImageQuery(path:string){
    this.mediaKind='image';this.format='';this.stopAudio();this.closeDialog('#audio-preview');
    this.imageQuery=path;this.query='';this.referenceUrl='';
    try{this.referenceUrl=(await rpc<{url:string}>('reference_preview',{path})).url}catch{}
    await this.search()
  }
  private async imageSearch(){await this.run(async()=>{const path=await choose('image');if(path)await this.setImageQuery(path)})}
  private clearImageQuery(){this.imageQuery='';this.referenceUrl='';void this.search()}
  private rememberSearch(text:string){
    const value=text.trim();if(!value)return;
    this.recent=[value,...this.recent.filter(item=>item!==value)].slice(0,8);
    localStorage.setItem('recent-searches',JSON.stringify(this.recent))
  }
  private useRecent(value:string){this.query=value;this.imageQuery='';this.referenceUrl='';void this.search()}
  private clearRecent(){this.recent=[];localStorage.removeItem('recent-searches')}
  private async changeKind(kind:MediaKind){
    if(kind===this.mediaKind)return;
    if(kind==='audio'&&!this.status?.audio){this.error='音声対応版のアプリとWorkerを使用してください。';this.dismissedError='';return}
    this.stopAudio();this.closeDialog('#audio-preview');this.closeDialog('#preview');
    this.mediaKind=kind;this.imageQuery='';this.referenceUrl='';this.format='';
    this.results={items:[],total:0,offset:0,semantic:false};
    await this.search()
  }
  private stopAudio(){
    ++this.audioRequest;
    this.renderRoot.querySelector<HTMLAudioElement>('#audio-preview audio')?.pause();
    this.audioPreview=undefined;
  }
  private hasFilters(){return!!(this.query||this.imageQuery||this.filename||this.format||this.dateAfter||this.dateBefore||this.folderFilter)}
  private async clearFilters(){
    this.query='';this.imageQuery='';this.referenceUrl='';this.filename='';this.format='';this.dateAfter='';this.dateBefore='';this.folderFilter='';this.sort='date_desc';
    await this.search()
  }
  private chips(){
    const result:{label:string;clear:()=>void}[]=[];
    if(this.query)result.push({label:`検索語「${this.query}」`,clear:()=>{this.query='';void this.search()}});
    if(this.imageQuery)result.push({label:`参照画像 ${basename(this.imageQuery)}`,clear:()=>this.clearImageQuery()});
    if(this.filename)result.push({label:`名前「${this.filename}」`,clear:()=>{this.filename='';void this.search()}});
    if(this.format)result.push({label:`形式 ${this.format.toUpperCase()}`,clear:()=>{this.format='';void this.search()}});
    if(this.dateAfter)result.push({label:`更新日 ${this.dateAfter} 以降`,clear:()=>{this.dateAfter='';void this.search()}});
    if(this.dateBefore)result.push({label:`更新日 ${this.dateBefore} まで`,clear:()=>{this.dateBefore='';void this.search()}});
    if(this.folderFilter){const folder=this.folders.find(item=>String(item.id)===this.folderFilter);result.push({label:`フォルダー ${folder?basename(folder.path):this.folderFilter}`,clear:()=>{this.folderFilter='';void this.search()}})}
    return result
  }
  private goPage(page:number){
    const pages=Math.max(1,Math.ceil(this.results.total/this.pageSize));
    const target=Math.min(Math.max(1,Math.floor(page)),pages);
    void this.search((target-1)*this.pageSize)
  }
  private changePageSize(value:number){
    this.pageSize=value;localStorage.setItem('display-page-size',String(value));void this.search(0)
  }
  private async showPreview(file:MediaFile){
    if(file.kind==='audio'){
      this.renderRoot.querySelector<HTMLAudioElement>('#audio-preview audio')?.pause();
      await this.run(async()=>{
        const request=++this.audioRequest;
        const result=await rpc<{url:string;file:MediaFile}>('preview',{id:file.id,segment_id:file.segment_id});
        if(request!==this.audioRequest||!this.isConnected)return;
        this.audioPreview=result;this.audioPosition=result.file.start_ms||0;
        await this.updateComplete;
        if(request!==this.audioRequest||!this.isConnected)return;
        const dialog=this.renderRoot.querySelector<HTMLDialogElement>('#audio-preview');
        if(!dialog?.open)dialog?.showModal()
      });return;
    }
    await this.run(async()=>{
      this.previewIndex=this.results.items.findIndex(item=>item.id===file.id);
      await this.loadPreview(file)
    })
  }
  private async moveAudio(direction:'previous'|'next'){
    const file=this.audioPreview?.file;
    const segment=file?.[direction==='previous'?'previous_segment_id':'next_segment_id'];
    if(file&&segment)await this.showPreview({...file,segment_id:segment})
  }
  private async loadPreview(file:MediaFile){
    this.preview=await rpc<{url:string;file:MediaFile}>('preview',{id:file.id});
    this.zoom=1;this.previewBaseWidth=0;
    await this.updateComplete;
    const dialog=this.renderRoot.querySelector<HTMLDialogElement>('#preview');
    if(!dialog?.open)dialog?.showModal();
    requestAnimationFrame(()=>{const image=this.renderRoot.querySelector<HTMLImageElement>('#preview img');if(image?.complete)this.fitPreview(image)})
  }
  private async movePreview(delta:number){
    const items=this.results.items;
    if(items.length<2)return;
    const index=(this.previewIndex+delta+items.length)%items.length;
    this.previewIndex=index;
    await this.run(()=>this.loadPreview(items[index]))
  }
  private fitPreview(image:HTMLImageElement){const container=this.renderRoot.querySelector<HTMLElement>('.preview-image');if(container&&image.naturalWidth&&container.clientHeight){this.previewBaseWidth=Math.min(image.naturalWidth,container.clientWidth,container.clientHeight*image.naturalWidth/image.naturalHeight)}}
  private async settings(){await this.updateComplete;this.renderRoot.querySelector<HTMLDialogElement>('#settings')?.showModal()}
  private async help(){await this.updateComplete;await this.renderRoot.querySelector<ReleaseControls>('release-controls[mode=assistant]')?.openHelp()}
  private openErrors(){void this.renderRoot.querySelector<ReleaseControls>('release-controls[mode=assistant]')?.showErrors(0,this.mediaKind)}
  private closeDialog(id:string){this.renderRoot.querySelector<HTMLDialogElement>(id)?.close()}
  private phase(){const audio=this.status?.analysis_kind==='audio';const phases:Record<string,string>={waiting_idle:'PCの操作が止まるのを待機中',idle:'解析待機中',loading:'モデルを読み込み中',scanning:'追加・変更を確認中',indexing:audio?'音声の区間を解析中':'写真を解析中',pausing:audio?'現在の区間を保存して停止中':'現在の写真を保存して停止中',paused:'解析を一時停止',error:'解析でエラーが発生'};return phases[this.status?.progress.phase||'idle']}
  private sortLabel(){const labels:Record<SearchSort,string>={date_desc:'更新日（新しい順）',date_asc:'更新日（古い順）',name_asc:'名前（A→Z）',name_desc:'名前（Z→A）',size_desc:'サイズ（大きい順）',size_asc:'サイズ（小さい順）'};return labels[this.sort]}
  render(){
    const s=this.status;
    const start=this.results.offset;
    const total=this.results.total;
    const audio=this.mediaKind==='audio';
    const itemUnit=audio?'区間':'枚';
    const viewJobs=(audio?s?.audio?.jobs:s?.jobs)||{};
    const jobs=(s?.running?(s.analysis_kind==='audio'?s.audio?.jobs:s.jobs):viewJobs)||{};
    const progressUnit=(s?.running?s.analysis_kind==='audio':audio)?'区間':'枚';
    const done=(viewJobs.done||0)+(viewJobs.skipped||0);
    const pending=(viewJobs.pending||0)+(viewJobs.processing||0);
    const progressDone=(jobs.done||0)+(jobs.skipped||0);
    const progressPending=(jobs.pending||0)+(jobs.processing||0);
    const pages=Math.max(1,Math.ceil(total/this.pageSize));
    const page=Math.floor(start/this.pageSize)+1;
    const bannerError=this.error||s?.progress.last_error||'';
    const chips=this.chips();
    const filtersActive=this.hasFilters();
    const enabledFolders=this.folders.filter(folder=>folder.enabled);
    return html`
<div class="layout">
<aside><div class="brand">SakuLoca<small>サクロカ · このPCで処理</small></div>
<div class="side-label">検索フォルダー <button class="quiet" @click=${this.addFolder} ?disabled=${s?.running} aria-label="フォルダーを追加">＋</button></div>
<div class="folders">${this.folders.map(f=>html`<div class="folder"><div class="folder-line"><input type="checkbox" aria-label=${`${f.path} を検索対象にする`} .checked=${!!f.enabled} ?disabled=${s?.running} @change=${()=>this.folderAction(f,'enable')}><span class="folder-name" title=${f.path}>${basename(f.path)}</span><button class="quiet" title="検索対象から削除" ?disabled=${s?.running} @click=${()=>this.folderAction(f,'remove')}>×</button></div><div class="folder-path">${f.path}</div><div class="folder-state">${audio?f.audio_analyzed||0:f.analyzed||0}/${audio?f.audio_count||0:f.count}${audio?'件':'枚'} 解析済み${f.status==='offline'?' · 未接続':f.status==='partial'?' · 一部アクセス不可':''}${f.last_scan?` · 最終確認 ${unixDate(f.last_scan)}`:''}</div>${f.error?html`<div class="folder-state">${f.error}</div>`:nothing}</div>`)}${!this.folders.length?html`<p class="note">写真・音声のあるフォルダーを登録すると、ここに表示されます。</p>`:nothing}</div>
<div class="side-bottom"><button @click=${this.addFolder} ?disabled=${s?.running}>フォルダーを追加</button><button @click=${this.settings}>モデルと設定</button><button @click=${this.help}>ヘルプ / アプリ情報</button><div class="note"><span class="status-dot"></span>完全ローカル処理<br>検索データ ${bytes((s?.database_bytes||0)+(s?.cache_bytes||0)+(s?.runtime_cache_bytes||0))}</div></div></aside>
<main class=${audio?'audio-mode':''}><header class="top"><h1>思い出を、言葉で見つける</h1><div class="media-switch" role="group" aria-label="検索対象"><button class=${!audio?'active':''} aria-pressed=${!audio} @click=${()=>this.changeKind('image')}>写真</button><button class=${audio?'active':''} aria-pressed=${audio} @click=${()=>this.changeKind('audio')}>音声</button></div><p class="subtitle">${audio?'音声の内容を日本語で検索。WAV・MP3・FLACに対応しています。':'「海辺の風景」「赤いりんご」など、探したい写真を日本語で入力。'}</p>
${this.notice?html`<div class="notice" role="status"><div><strong>${this.notice.version} に更新できます。</strong><span>設定から変更内容を確認して更新できます。</span></div><button @click=${()=>{this.notice=undefined;void this.settings()}}>更新を確認</button><button class="quiet" @click=${()=>this.notice=undefined}>後で</button></div>`:nothing}
<form class="search" @submit=${(e:Event)=>{e.preventDefault();this.imageQuery='';this.referenceUrl='';this.rememberSearch(this.query);void this.search()}}><input id="query" class="search-input" aria-label=${audio?'音声を自然言語で検索':'写真を自然言語で検索'} placeholder=${audio?'どんな音を探しますか？':'どんな写真を探しますか？'} .value=${this.query} @input=${(e:Event)=>this.query=(e.target as HTMLInputElement).value}>${!audio?html`<button type="button" @click=${this.imageSearch} ?disabled=${this.busy}>画像で探す</button>`:nothing}<button class="primary" ?disabled=${this.busy}>${this.busy?'検索中…':'検索'}</button></form>
${this.imageQuery?html`<div class="image-query">${this.referenceUrl?html`<img class="reference" src=${this.referenceUrl} alt="参照画像のプレビュー">`:nothing}<span>参照画像：${basename(this.imageQuery)}</span><button class="quiet" @click=${this.clearImageQuery}>解除 ×</button></div>`:nothing}
${this.recent.length?html`<div class="recent"><span>最近の検索：</span>${this.recent.map(value=>html`<button class="chip" @click=${()=>this.useRecent(value)}>${value}</button>`)}<button class="quiet" @click=${this.clearRecent}>履歴を消去</button></div>`:nothing}
<div class="filters"><label>ファイル名 <input class="filename" placeholder="名前の一部" .value=${this.filename} @change=${(e:Event)=>{this.filename=(e.target as HTMLInputElement).value;void this.search()}}></label><label>形式 <select .value=${this.format} @change=${(e:Event)=>{this.format=(e.target as HTMLSelectElement).value;void this.search()}}><option value="">すべて</option>${audio?html`<option value="wav">WAV</option><option value="mp3">MP3</option><option value="flac">FLAC</option>`:html`<option value="jpg">JPEG</option><option value="png">PNG</option><option value="webp">WebP</option>`}</select></label><label>フォルダー <select class="folder-filter" .value=${this.folderFilter} @change=${(e:Event)=>{this.folderFilter=(e.target as HTMLSelectElement).value;void this.search()}}><option value="">すべて</option>${enabledFolders.map(folder=>html`<option value=${folder.id}>${basename(folder.path)}</option>`)}</select></label><label>並び順 <select .value=${this.sort} @change=${(e:Event)=>{this.sort=(e.target as HTMLSelectElement).value as SearchSort;void this.search()}}><option value="date_desc">更新日（新しい順）</option><option value="date_asc">更新日（古い順）</option><option value="name_asc">名前（A→Z）</option><option value="name_desc">名前（Z→A）</option><option value="size_desc">サイズ（大きい順）</option><option value="size_asc">サイズ（小さい順）</option></select></label><label>更新日 <input aria-label="更新日の開始" type="date" .value=${this.dateAfter} @change=${(e:Event)=>{this.dateAfter=(e.target as HTMLInputElement).value;void this.search()}}> 〜 <input aria-label="更新日の終了" type="date" .value=${this.dateBefore} @change=${(e:Event)=>{this.dateBefore=(e.target as HTMLInputElement).value;void this.search()}}></label><button ?disabled=${!filtersActive||this.busy} @click=${this.clearFilters}>条件をクリア</button><span class="kbd">Ctrl + K で検索</span></div>
${chips.length?html`<div class="chips">${chips.map(chip=>html`<button class="chip" @click=${chip.clear}>${chip.label} ×</button>`)}</div>`:nothing}
</header>
${bannerError&&this.dismissedError!==bannerError?html`<div class="error" role="alert"><span>${bannerError}</span><button class="quiet" @click=${()=>{this.dismissedError=bannerError;this.error=''}} aria-label="エラーを閉じる">×</button></div>`:nothing}
<div class="toolbar"><div><strong>${total.toLocaleString()}${itemUnit}</strong> <span>${this.results.semantic?'意味の近い順 · 類似度は正解率ではありません':`${this.sortLabel()} · フィルター検索`}</span></div><div class="view-buttons"><button class=${this.view==='grid'?'active':''} aria-label="グリッド表示" @click=${()=>this.view='grid'}>▦</button><button class=${this.view==='list'?'active':''} aria-label="リスト表示" @click=${()=>this.view='list'}>☰</button></div></div>
${this.results.interpreted_query?html`<p class="note" role="status">音の種類「${this.results.interpreted_query}」として検索しました。</p>`:nothing}<section class=${`results ${this.busy?'loading':''}`} aria-label="検索結果" aria-busy=${this.busy}>${audio&&!total?html`<div class="empty"><div class="empty-icon" aria-hidden="true">♫</div><h2>${s?.audio?.analyzed?'一致する音声区間がありません':'音声を検索する準備をしましょう'}</h2><p>音声フォルダーを登録し、この画面で「差分更新 / 解析」を押してください。30秒ごと・5秒重複で解析します。完全な無音は言葉の検索から除外します。</p><p>対応：WAV（非圧縮PCM 16bit）・MP3・FLAC、mono/stereo、8〜96kHz、1ファイル24時間以内。検索精度は内容によって異なります。</p><button class="primary" @click=${this.index} ?disabled=${s?.running||!s?.model_bytes||!enabledFolders.length}>差分更新 / 解析</button> <button @click=${this.addFolder} ?disabled=${s?.running}>フォルダーを追加</button>${filtersActive?html` <button @click=${this.clearFilters}>条件をクリア</button>`:nothing}</div>`:total?html`<div class=${this.view}>${this.results.items.map(file=>html`<button class="card" @click=${()=>this.showPreview(file)} aria-label=${audio?`${file.name} ${timecode(file.start_ms||0)}から${timecode(file.end_ms||0)}を再生`:`${file.name} をプレビュー`}>${audio?html`<div class="audio-mark"><span aria-hidden="true">♫</span><span>${timecode(file.start_ms||0)} – ${timecode(file.end_ms||0)}</span></div>`:html`<media-thumbnail .fileId=${file.id}></media-thumbnail>`}<div class="card-info"><div class="card-name" title=${file.name}>${file.name}</div><div class="card-meta">${file.format.toUpperCase()} · ${bytes(file.size)}${file.silent?' · 無音（検索対象外）':''}${file.score!==undefined?` · 類似度 ${file.score.toFixed(3)}`:''}</div>${this.view==='list'?html`<div class="card-meta">${date(file.mtime_ns)} · ${file.path}</div>`:nothing}</div></button>`)}</div>`:html`<div class="empty"><div class="empty-icon">▧</div><h2>${!s?.model_bytes?'AIモデルを準備しましょう':done&&pending?'未解析の写真があります':done?'検索条件に一致する写真がありません':this.folders.length?'写真を検索する準備をしましょう':'最初のフォルダーを登録'}</h2><p>${!s?.model_bytes?'「モデルと設定」で既存モデルを選択するか、公式配布から取得してください。':done&&pending?`登録した写真のうち ${pending} 枚がまだ解析されていません。解析を再開すると検索対象になります。`:done?'検索語やフィルターを変えてお試しください。':this.folders.length?'「差分更新 / 解析」を押すと、登録したフォルダーだけを解析します。解析後、言葉や画像から写真を探せます。':'写真のあるフォルダーを選んでください。サブフォルダーも対象になります。'}</p>${!s?.model_bytes?html`<button class="primary" @click=${this.settings}>モデルと設定</button>`:done&&pending?html`<button class="primary" @click=${this.index} ?disabled=${s?.running}>解析を再開</button>${filtersActive?html`<button @click=${this.clearFilters}>条件をクリア</button>`:nothing}`:done?nothing:this.folders.length?html`<button class="primary" @click=${this.index} ?disabled=${s?.running}>差分更新 / 解析</button>`:html`<button class="primary" @click=${this.addFolder}>フォルダーを追加</button>`}${this.searched&&this.query&&done&&!pending?html`<p>検索条件に一致する解析済みの写真はありません。</p>`:nothing}</div>`}</section>
<footer><div><div class=${s?.running?'busy':''}>${this.phase()} ${s?.running?html`<progress max=${Math.max(progressDone+progressPending,1)} value=${progressDone}></progress>`:nothing}</div><div class="progress-text">解析済み ${progressDone}${progressUnit} · 待ち ${progressPending}${progressUnit} · ${(jobs.error||0)>0?html`<button class="linkish" @click=${()=>{const kind=s?.running?s.analysis_kind||'image':this.mediaKind;void this.renderRoot.querySelector<ReleaseControls>('release-controls[mode=assistant]')?.showErrors(0,kind)}}>エラー ${jobs.error}件</button>`:html`<span>エラー 0件</span>`}${s?.running?` · ${s.speed.toFixed(2)}${progressUnit}/秒`:''}${audio?` · 音声 ${s?.audio?.analyzed||0}/${s?.audio?.total||0}件`:''}</div></div><div class="pager">${total?html`<button ?disabled=${start===0||this.busy} @click=${()=>this.goPage(1)} aria-label="最初のページ">«</button><button ?disabled=${start===0||this.busy} @click=${()=>this.search(Math.max(0,start-this.pageSize))}>前へ</button><span>${start+1}–${Math.min(start+this.pageSize,total)} / ${total}</span><input class="page-input" type="number" min="1" max=${pages} aria-label="ページ番号" .value=${String(page)} @change=${(e:Event)=>this.goPage(Number((e.target as HTMLInputElement).value)||1)}><span>/ ${pages}</span><button ?disabled=${start+this.pageSize>=total||this.busy} @click=${()=>this.search(start+this.pageSize)}>次へ</button><button ?disabled=${start+this.pageSize>=total||this.busy} @click=${()=>this.goPage(pages)} aria-label="最後のページ">»</button>`:nothing}<label class="page-size">表示 <select .value=${String(this.pageSize)} @change=${(e:Event)=>this.changePageSize(Number((e.target as HTMLSelectElement).value))}><option value="30">30</option><option value="60">60</option><option value="120">120</option><option value="200">200</option></select></label>${s?.running?html`<button @click=${this.pause}>一時停止</button>`:html`<button @click=${this.index} ?disabled=${!s?.model_bytes||!this.folders.some(f=>f.enabled)}>差分更新 / 解析</button>`}</div></footer></main></div>
<dialog id="settings"><div class="dialog-header"><h2>モデルと設定</h2><button class="quiet" @click=${()=>this.closeDialog('#settings')} aria-label="設定を閉じる">×</button></div><release-controls .status=${s} .mediaKind=${this.mediaKind} @refresh-needed=${()=>this.refresh()}></release-controls><update-controls @update-found=${(e:Event)=>{this.notice=(e as CustomEvent<{version:string;notes:string}>).detail}} @notify=${(e:Event)=>this.notify((e as CustomEvent<{message:string}>).detail.message,'error')}></update-controls><div class="setting"><label>表示テーマ</label><select aria-label="表示テーマ" .value=${this.theme} @change=${(e:Event)=>{this.theme=(e.target as HTMLSelectElement).value;this.applyTheme()}}><option value="system">Windowsの設定に合わせる</option><option value="light">ライト</option><option value="dark">ダーク</option></select></div><div class="dialog-actions"><button @click=${()=>this.closeDialog('#settings')}>閉じる</button></div></dialog>
<release-controls mode="assistant" .status=${s} .mediaKind=${this.mediaKind} @refresh-needed=${()=>this.refresh()}></release-controls>
<dialog id="audio-preview" class="audio-dialog" @close=${this.stopAudio}><div class="dialog-header"><h2>${this.audioPreview?.file.name||'音声の再生'}</h2><button class="quiet" @click=${()=>{this.stopAudio();this.closeDialog('#audio-preview')}} aria-label="音声プレビューを閉じる">×</button></div>${this.audioPreview?html`<p>検索で見つかった区間：${timecode(this.audioPreview.file.match_start_ms??this.audioPreview.file.start_ms??0)} – ${timecode(this.audioPreview.file.match_end_ms??this.audioPreview.file.end_ms??0)}</p><p>再生範囲（前後5秒を含む）：${timecode(this.audioPreview.file.start_ms||0)} – ${timecode(this.audioPreview.file.end_ms||0)}（全体 ${timecode(this.audioPreview.file.duration_ms||0)}）</p><audio controls preload="metadata" src=${this.audioPreview.url} @timeupdate=${(event:Event)=>{this.audioPosition=(this.audioPreview?.file.start_ms||0)+(event.target as HTMLAudioElement).currentTime*1000}} @error=${()=>this.notify('この区間を再生できません。元のファイルを開いて確認してください。','error')}></audio><p class="note">元ファイルの位置：${timecode(this.audioPosition)} · 境目の音を確認できるよう、前後も含めて再生します。${this.audioPreview.file.silent?'この区間は完全な無音です。':''}</p><div class="dialog-actions"><button ?disabled=${!this.audioPreview.file.previous_segment_id} @click=${()=>this.moveAudio('previous')}>前の区間</button><button ?disabled=${!this.audioPreview.file.next_segment_id} @click=${()=>this.moveAudio('next')}>次の区間</button><button @click=${()=>this.run(()=>openFile(this.audioPreview!.file.id))}>元のファイル全体を開く</button><button @click=${()=>this.run(()=>openFile(this.audioPreview!.file.id,true))}>保存場所を開く</button><button @click=${()=>this.run(()=>copyText(this.audioPreview!.file.path).then(()=>this.notify('パスをコピーしました')))}>パスをコピー</button></div><p class="path">${this.audioPreview.file.path}</p>`:nothing}</dialog>
<dialog id="preview" class="preview-dialog"><div class="dialog-header"><strong>${this.preview?.file.name}</strong><button class="quiet" @click=${()=>this.closeDialog('#preview')} aria-label="プレビューを閉じる">×</button></div><div class="preview-tools"><button @click=${()=>this.movePreview(-1)} ?disabled=${this.results.items.length<2} aria-label="前の写真">←</button><span>${this.previewIndex+1} / ${this.results.items.length}</span><button @click=${()=>this.movePreview(1)} ?disabled=${this.results.items.length<2} aria-label="次の写真">→</button><button @click=${()=>this.zoom=Math.max(.25,this.zoom-.25)} aria-label="縮小">−</button><span>${Math.round(this.zoom*100)}%</span><button @click=${()=>this.zoom=Math.min(4,this.zoom+.25)} aria-label="拡大">＋</button><button @click=${()=>this.zoom=1}>画面に合わせる</button><span class="space"></span><button @click=${()=>this.run(()=>copyText(this.preview!.file.path).then(()=>this.notify('パスをコピーしました')))}>パスをコピー</button><button @click=${()=>this.run(async()=>{const path=this.preview!.file.path;this.closeDialog('#preview');await this.setImageQuery(path)})}>この写真に似た画像</button><button @click=${()=>this.run(()=>openFile(this.preview!.file.id))}>元のファイルを開く</button><button @click=${()=>this.run(()=>openFile(this.preview!.file.id,true))}>保存場所を開く</button></div><div class="preview-image"><div class="preview-canvas">${this.preview?html`<img src=${this.preview.url} alt=${this.preview.file.name} @load=${(event:Event)=>this.fitPreview(event.target as HTMLImageElement)} style=${styleMap({width:this.previewBaseWidth?`${this.previewBaseWidth*this.zoom}px`:'auto',maxWidth:this.previewBaseWidth?'none':'100%',maxHeight:this.previewBaseWidth?'none':'100%'})}>`:nothing}</div></div>${this.preview?html`<div class="preview-info"><span>${date(this.preview.file.mtime_ns)}</span><span>${bytes(this.preview.file.size)}</span><span>${this.preview.file.format.toUpperCase()}</span><span class="kbd">←/→ で移動 · +/- で拡大縮小 · 0 で戻す</span></div><div class="path">${this.preview.file.path}</div>`:nothing}</dialog>
${this.toast?html`<div class=${`toast ${this.toastKind}`} role="status" @click=${()=>this.toast=''}><span>${this.toast}</span><button class="quiet" @click=${(e:Event)=>{e.stopPropagation();this.toast=''}} aria-label="通知を閉じる">×</button></div>`:nothing}
${this.dragging?html`<div class="drop-overlay"><div>画像をドロップで類似検索<br><span>フォルダーをドロップで登録</span></div></div>`:nothing}`
  }
}
