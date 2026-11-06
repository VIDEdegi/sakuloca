import {LitElement,html,css} from 'lit';
import {customElement,state} from 'lit/decorators.js';
import {invoke} from '@tauri-apps/api/core';
import {listen, type UnlistenFn} from '@tauri-apps/api/event';

@customElement('update-controls')
export class UpdateControls extends LitElement {
  static styles=css`:host{display:block;margin:16px 0}button{font:inherit;padding:6px 12px;margin:8px 8px 0 0}pre{white-space:pre-wrap;font:inherit}progress{width:100%}`;
  @state() private automatic=true;
  @state() private busy=false;
  @state() private message='';
  @state() private version='';
  @state() private notes='';
  @state() private received=0;
  @state() private total=0;
  private unlisten?:UnlistenFn;
  async connectedCallback(){
    super.connectedCallback();
    try {
      this.unlisten=await listen<{received?:number;total?:number;installing?:boolean}>('update-progress',e=>{
        this.received=e.payload.received||0;this.total=e.payload.total||0;
        if(e.payload.installing)this.message='解析を安全に停止しました。更新セットアップを起動します…';
      });
      if(!this.isConnected){this.unlisten();return}
      this.automatic=(await invoke<{automatic:boolean}>('update_preferences')).automatic;
      if(this.automatic)await this.check(true);
    }catch{this.message='更新設定を読み込めません。手動セットアップを利用できます。'}
  }
  disconnectedCallback(){this.unlisten?.();super.disconnectedCallback()}
  private async check(startup=false){
    this.busy=true;this.message='更新を確認中…';
    try{const u=await invoke<{available:boolean;version?:string;notes?:string}>('check_update');this.version=u.version||'';this.notes=u.notes||'';this.message=u.available?`${u.version} に更新できます。`:startup?'':'最新バージョンです。';if(startup&&u.available&&this.isConnected)this.dispatchEvent(new CustomEvent('update-found',{detail:{version:u.version||'',notes:u.notes||''},bubbles:true,composed:true}))}
    catch(e){this.message=startup?'':String(e)}finally{this.busy=false}
  }
  private async install(){
    if(!confirm(`${this.version} を取得・署名確認して更新します。解析を保存して停止し、アプリを終了します。続行しますか？`))return;
    this.busy=true;this.message='更新を取得・署名確認しています…';
    try{await invoke('install_update')}catch(e){this.message=String(e);this.busy=false;this.dispatchEvent(new CustomEvent('notify',{detail:{message:String(e)},bubbles:true,composed:true}))}
  }
  private async toggle(value:boolean){
    try{this.automatic=(await invoke<{automatic:boolean}>('update_preferences',{automatic:value})).automatic}catch(e){this.message=String(e)}
  }
  render(){return html`<strong>アプリの更新</strong><p><label><input type="checkbox" .checked=${this.automatic} ?disabled=${this.busy} @change=${(e:Event)=>this.toggle((e.target as HTMLInputElement).checked)}>起動時に更新を確認（ダウンロード・導入は確認後）</label></p><button ?disabled=${this.busy} @click=${()=>this.check()}>更新を確認</button>${this.version?html`<button ?disabled=${this.busy} @click=${this.install}>${this.version} に更新</button><pre>${this.notes}</pre>`:''}<p role="status">${this.message}</p>${this.total?html`<progress max=${this.total} value=${this.received}></progress>`:''}<p>更新確認ではGitHubへ接続します。写真・検索語は送信しません。失敗時はGitHub ReleasesのWindowsセットアップを取得し、トレイから完全終了して手動更新してください。</p><a href="https://github.com/VIDEdegi/sakuloca/releases" @click=${(e:Event)=>{e.preventDefault();void invoke('open_reference',{kind:'releases'})}}>GitHub Releases</a>`}
}
