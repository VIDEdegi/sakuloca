import {invoke} from '@tauri-apps/api/core';
export type MediaKind='image'|'audio';
export interface Folder {id:number;path:string;enabled:number;status:string;error:string|null;count:number;analyzed:number;audio_count?:number;audio_analyzed?:number;last_scan:number|null}
export interface MediaFile {id:number;path:string;name:string;size:number;mtime_ns:number;format:string;kind?:MediaKind;segment_id?:number;start_ms?:number;end_ms?:number;duration_ms?:number;silent?:boolean;score?:number;error:string|null;last_analyzed:number|null}
export interface SearchResponse {items:MediaFile[];total:number;offset:number;semantic:boolean;interpreted_query?:string}
export interface MediaFile {previous_segment_id?:number|null;next_segment_id?:number|null;match_start_ms?:number;match_end_ms?:number}
export interface Preferences {background:boolean;resume_on_launch:boolean;low_load:boolean;idle_only:boolean;auto_update:boolean;update_minutes:number;setup_step:number;setup_complete:boolean;autostart:boolean;start_minimized:boolean}
export interface Environment {os:string;architecture:string;os_supported:boolean;free_bytes:number;recommended_free:number;runtime:Record<string,string|null>;python_bundled:boolean;model_exists:boolean;model_bytes:number;gpu_note:string;cpu_note:string}
export interface Download {phase:string;received:number;total:number;path:string;error:string|null}
export interface Status {preferences:Preferences;download:Download;free_bytes:number;model_bytes:number;progress:{phase:string;processed:number;discovered:number;errors:number;last_error:string|null};jobs:Record<string,number>;total:number;database_bytes:number;cache_bytes:number;runtime_cache_bytes:number;runtime_cache_warning:string|null;data_path:string;config:{model_path:string;backend:'gpu'|'cpu'};model:{dimensions:number;id:string}|null;speed:number;running:boolean;errors:{path:string;error:string}[]}
export type SearchSort='date_desc'|'date_asc'|'name_asc'|'name_desc'|'size_desc'|'size_asc';
export interface SearchQuery {text?:string;image?:string;filename?:string;format?:string;after?:string;before?:string;folder?:string;sort?:SearchSort;offset?:number;limit?:number}
export interface Status {analysis_kind?:MediaKind;audio?:{total:number;analyzed:number;jobs:Record<string,number>;error_files:number;errors:{path:string;error:string}[]}}
export interface SearchQuery {kind?:MediaKind}
export const rpc=<T>(method:string,params:Record<string,unknown>={}):Promise<T>=>invoke('worker_request',{method,params});
export const choose=(kind:'folder'|'image'|'model'|'storage'|'model_dir'):Promise<string|null>=>invoke('choose_path',{kind});
export const openFile=(id:number,reveal=false):Promise<void>=>invoke('open_media',{id,reveal});
export const bytes=(n:number)=>n>=1024**3?`${(n/1024**3).toFixed(2)} GB`:n>=1024**2?`${(n/1024**2).toFixed(1)} MB`:n>=1024?`${(n/1024).toFixed(0)} KB`:`${n} B`;
export const date=(n:number)=>new Date(n/1e6).toLocaleString('ja-JP');
export const unixDate=(n:number)=>new Date(n*1000).toLocaleString('ja-JP');
export const timecode=(ms:number)=>{const n=Math.max(0,Math.floor(ms)),s=Math.floor(n/1000);return `${s>=3600?`${Math.floor(s/3600)}:`:''}${String(Math.floor(s/60)%60).padStart(2,'0')}:${String(s%60).padStart(2,'0')}${n%1000?`.${String(n%1000).padStart(3,'0')}`:''}`};

export const basename=(path:string)=>path.replaceAll(String.fromCharCode(92),'/').split('/').filter(Boolean).pop()||path;

export const desktop=(action:string):Promise<unknown>=>invoke('desktop_action',{action});
export const reference=(kind:string):Promise<void>=>invoke('open_reference',{kind});
export const saveDesktop=(preferences:Partial<Preferences>,autostart?:boolean,minimized?:boolean):Promise<Preferences>=>invoke('desktop_preferences',{preferences,autostart,minimized});
export const migrateStorage=(destination:string):Promise<unknown>=>invoke('migrate_storage',{destination});
export async function copyText(text:string){
  try{await navigator.clipboard.writeText(text);return true}
  catch{
    const area=document.createElement('textarea');area.value=text;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();
    try{return document.execCommand('copy')}finally{area.remove()}
  }
}
