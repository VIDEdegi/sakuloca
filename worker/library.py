from pathlib import Path
from array import array
from datetime import datetime
import base64, json, os, sqlite3, threading, time, uuid, shutil, sys, traceback
from ai import LiteRtBackend, PREPROCESS, QUERY_PREFIX, digest, prepare_image, prepare_index_image
from database import connect
from runtime_cache import RuntimeCache, LIMIT
from prefetch import Prefetch
from model_download import ModelDownload
from diagnostics import environment, probe, idle_seconds
from storage import migrate
from user_errors import friendly_error
from exact_search import ExactDot
from audio_library import AudioLibrary
from audio import EXTENSIONS as AUDIO_EXTENSIONS

EXTENSIONS={'.jpg','.jpeg','.png','.webp'}
CACHE_LIMIT=512*1024*1024

class Library:
    def __init__(self,data:Path):
        self.data=data.resolve(); self.data.mkdir(parents=True,exist_ok=True)
        self.cache=self.data/'thumbnails'; self.cache.mkdir(exist_ok=True)
        self.cache_size=sum(p.stat().st_size for p in self.cache.glob('*.jpg'))
        self.cache_since_prune=0
        self.runtime_cache=RuntimeCache(self.data); self.runtime_cache.prune()
        self.db=connect(self.data/'index.sqlite3')
        self.db_lock=threading.RLock(); self.ai_lock=threading.Lock()
        self.engine=None; self.model_id=None; self.model_meta=None
        self.search_cache=None
        self.runtime_path=None; self.cache_disabled=False; self.cache_warning=None
        self.stop=threading.Event(); self.thread=None
        self.progress={'phase':'idle','processed':0,'discovered':0,'errors':0,'started':0,'last_error':None}
        self.download=ModelDownload()
        self.preferences={'background':False,'resume_on_launch':False,'low_load':False,'idle_only':False,'auto_update':False,'update_minutes':30,'setup_step':0,'setup_complete':False,'autostart':False,'start_minimized':False,'manual_pause':False}
        pref=self.db.execute("SELECT value FROM settings WHERE key='preferences'").fetchone()
        if pref:self.preferences.update(json.loads(pref[0]))
        elif self.db.execute('SELECT 1 FROM folders LIMIT 1').fetchone():self.preferences['setup_complete']=True
        self.config={'model_path':str(self.data/'models'/'embeddinggemma-2-740m.litertlm'),'backend':'gpu'}
        row=self.db.execute("SELECT value FROM settings WHERE key='config'").fetchone()
        if row: self.config.update(json.loads(row[0]))
        active=self.db.execute("SELECT value FROM settings WHERE key='active_model'").fetchone()
        self.status_model_id=active[0] if active else None
        self.audio=AudioLibrary(self)
        selected=self.db.execute("SELECT value FROM settings WHERE key='analysis_kind'").fetchone()
        self.analysis_kind=selected[0] if selected and selected[0] in ('image','audio') else 'image'
    def close(self):
        self.download.cancel()
        self.stop.set()
        if self.thread: self.thread.join(timeout=120)
        if self.thread and self.thread.is_alive(): return
        with self.ai_lock:
            if self.engine: self.engine.close(); self.engine=None
            self.audio.close_engine()
            self.runtime_cache.prune()
        with self.db_lock: self.db.close()
    def invalidate_search(self):
        with self.db_lock:
            self.search_cache=None
            if hasattr(self,'audio'):self.audio.search_cache=None
    def configure(self,model_path,backend):
        self.invalidate_search()
        if self.thread and self.thread.is_alive(): raise ValueError('解析を一時停止してから設定を変更してください')
        if backend not in ('gpu','cpu'): raise ValueError('不正なバックエンドです')
        path=Path(model_path).resolve(strict=True)
        with path.open('rb') as f:
            if f.read(8)!=b'LITERTLM': raise ValueError('LiteRT-LM形式ではありません')
        with self.ai_lock:
            if self.engine: self.engine.close()
            self.audio.reset()
            self.engine=None; self.model_id=None; self.model_meta=None
            self.status_model_id=None
            self.runtime_path=None; self.cache_disabled=False; self.cache_warning=None
            self.runtime_cache.prune()
        self.config.update(model_path=str(path),backend=backend)
        with self.db_lock, self.db:
            self.invalidate_search()
            self.db.execute("INSERT OR REPLACE INTO settings VALUES('config',?)",(json.dumps(self.config),))
            self.db.execute("DELETE FROM settings WHERE key='active_audio_model'")
        return self.config
    def save_preferences(self,**changes):
        if set(changes)-set(self.preferences):raise ValueError('不正な設定項目です')
        for key,value in changes.items():
            if key in ('setup_step','update_minutes'):
                if type(value) is not int or not (0<=value<=7 if key=='setup_step' else 5<=value<=1440):raise ValueError('設定値が範囲外です')
            elif type(value) is not bool:raise ValueError('設定値が不正です')
        self.preferences.update(changes)
        with self.db_lock,self.db:self.db.execute("INSERT OR REPLACE INTO settings VALUES('preferences',?)",(json.dumps(self.preferences),))
        return dict(self.preferences)
    def environment(self):return environment(self.data,self.config['model_path'])
    def diagnose(self):
        if self.status()['running']:raise ValueError('解析を一時停止してから診断してください')
        return probe(self.config['model_path'],self.config['backend'],self.data)
    def migrate(self,destination):return migrate(self,destination)
    def _error_scope(self):
        return ("j.state='error' AND j.model_id=? AND f.available=1 AND EXISTS("
                "SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id "
                "WHERE ff.file_id=f.id AND d.enabled=1)")
    def errors(self,offset=0,kind='image'):
        if kind=='audio':return self.audio.errors(offset)
        if kind!='image':raise ValueError('検索対象は写真または音声を選択してください')
        offset=max(0,int(offset));model_id=self.model_id or self.status_model_id
        with self.db_lock:
            scope=self._error_scope()
            return {'total':self.db.execute(f'SELECT COUNT(*) FROM jobs j JOIN files f ON f.id=j.file_id WHERE {scope}',(model_id,)).fetchone()[0],'items':[dict(r) for r in self.db.execute(f'SELECT f.id,f.path,j.error FROM jobs j JOIN files f ON f.id=j.file_id WHERE {scope} ORDER BY f.id LIMIT 50 OFFSET ?',(model_id,offset))]}
    def retry_errors(self,kind='image'):
        if self.status()['running']:raise ValueError('解析を一時停止してから再試行してください')
        if kind=='audio':return self.start(kind='audio')
        if kind!='image':raise ValueError('検索対象は写真または音声を選択してください')
        model_id=self.model_id or self.status_model_id
        with self.db_lock,self.db:
            self.db.execute("UPDATE jobs SET state='pending',error=NULL WHERE state='error' AND model_id=? AND file_id IN (SELECT f.id FROM files f WHERE f.available=1 AND EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1))",(model_id,))
        return self.start(kind='image')
    def _resume_requested(self,value):
        with self.db_lock,self.db:self.db.execute("INSERT OR REPLACE INTO settings VALUES('resume_requested',?)",(json.dumps(value),))
    def resume_requested(self):
        with self.db_lock:row=self.db.execute("SELECT value FROM settings WHERE key='resume_requested'").fetchone()
        return bool(row and json.loads(row[0]))
    def ensure_engine(self):
        # Caller holds ai_lock. No db lock while loading or running inference.
        if self.engine: return
        self.audio.close_engine()
        model=Path(self.config['model_path'])
        sha=digest(model)
        self.runtime_path=None if self.cache_disabled else self.runtime_cache.prepare(sha)
        engine=LiteRtBackend(model,self.config['backend'],self.runtime_path or ':nocache')
        try:
            engine.text('写真') # Discover actual dimensions rather than assume them.
            meta={'sha256':sha,'dimensions':engine.dimensions,'preprocess':PREPROCESS,'normalization':'l2','query_prefix':QUERY_PREFIX,'runtime':'litert-lm-api/0.18.0','backend':self.config['backend']}
            model_id=hashlib_id(meta)
            with self.db_lock, self.db:
                self.db.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,?,?,?,?,?)',(model_id,*meta.values()))
                self.db.execute('UPDATE models SET backend=? WHERE id=?',(meta['backend'],model_id))
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('active_model',?)",(model_id,))
            self.status_model_id=model_id
            self.engine=engine; self.model_id=model_id; self.model_meta={'id':model_id,**meta}
            self.maintain_runtime_cache()
        except BaseException:
            if self.engine is engine:self.engine=None
            engine.close(); self.runtime_cache.prune(); raise
    def maintain_runtime_cache(self):
        # Caller holds ai_lock; never evict active mapped files.
        if self.audio.engine:
            self.audio.maintain_cache();return
        total=self.runtime_cache.prune(keep=self.runtime_path if self.engine else None)
        if total>LIMIT and self.engine and self.runtime_path:
            path=self.runtime_path
            self.engine.close();self.engine=None
            self.runtime_cache.clear_generation(path)
            self.runtime_cache.prune()
            self.runtime_path=None;self.cache_disabled=True
            self.cache_warning='AIキャッシュが1 GiBを超えたため、このセッションでは保存せずに処理します。検索データは保持されます。'
            self.ensure_engine()
    def _pending_jobs(self):
        last=0
        while not self.stop.is_set():
            with self.db_lock:
                job=self.db.execute("SELECT f.*,j.model_id FROM jobs j JOIN files f ON f.id=j.file_id WHERE j.model_id=? AND j.state='pending' AND f.id>? AND f.available=1 AND EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1 AND d.status='ready') ORDER BY f.id LIMIT 1",(self.model_id,last)).fetchone()
            if job is None:return
            last=job['id'];yield job
    def _prepare_job(self,job):
        try:
            path=Path(job['path']);before=path.stat()
            if before.st_size!=job['size'] or before.st_mtime_ns!=job['mtime_ns']:raise ValueError('ファイルが変更されました。差分更新してください')
            image,thumbnail=prepare_index_image(path)
            after=path.stat()
            if after.st_size!=before.st_size or after.st_mtime_ns!=before.st_mtime_ns:raise ValueError('前処理中にファイルが変更されました')
            return job,image,thumbnail,None
        except Exception as exc:return job,None,None,exc
    def clear_runtime_cache(self):
        if self.thread and self.thread.is_alive(): raise ValueError('解析を一時停止してから削除してください')
        with self.ai_lock:
            if self.engine: self.engine.close()
            self.audio.close_engine();self.audio.cache_disabled=False
            self.engine=None; self.model_id=None; self.model_meta=None
            self.runtime_path=None;self.cache_disabled=False;self.cache_warning=None
            return {'bytes':self.runtime_cache.clear()}
    def folders(self):
        model_id=self.model_id or self.status_model_id or ''
        with self.db_lock:
            return [dict(r) for r in self.db.execute('''SELECT f.*,
                (SELECT COUNT(*) FROM folder_files ff JOIN files m ON m.id=ff.file_id WHERE ff.folder_id=f.id AND m.available=1 AND m.kind='image') AS count,
                (SELECT COUNT(*) FROM folder_files ff JOIN files m ON m.id=ff.file_id JOIN jobs j ON j.file_id=m.id AND j.model_id=? WHERE ff.folder_id=f.id AND m.available=1 AND j.state='done') AS analyzed,
                (SELECT COUNT(*) FROM folder_files ff JOIN files m ON m.id=ff.file_id WHERE ff.folder_id=f.id AND m.available=1 AND m.kind='audio') AS audio_count,
                (SELECT COUNT(*) FROM folder_files ff JOIN files m ON m.id=ff.file_id JOIN audio_files a ON a.file_id=m.id WHERE ff.folder_id=f.id AND m.available=1 AND a.model_id=? AND a.state='done') AS audio_analyzed
                FROM folders f ORDER BY f.id''',(model_id,self.audio.model_id))]
    def add_folder(self,path):
        self.invalidate_search()
        folder=Path(path).resolve(strict=True)
        if not folder.is_dir(): raise ValueError('フォルダーを選択してください')
        if folder==Path(folder.anchor): raise ValueError('ドライブ全体は登録できません。写真・音声のフォルダーを指定してください')
        if folder==self.data or folder.is_relative_to(self.data) or self.data.is_relative_to(folder):
            raise ValueError('モデル・インデックスの保存領域と重なるフォルダーは登録できません')
        with self.db_lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO folders(path) VALUES(?)',(str(folder),))
            self.invalidate_search()
        return self.folders()
    def folder_action(self,id,action,enabled=True):
        self.invalidate_search()
        if self.thread and self.thread.is_alive(): raise ValueError('解析を一時停止してからフォルダーを変更してください')
        with self.db_lock, self.db:
            self.invalidate_search()
            if action=='remove':
                self.db.execute('DELETE FROM folders WHERE id=?',(id,))
                self.db.execute('DELETE FROM files WHERE NOT EXISTS(SELECT 1 FROM folder_files WHERE file_id=files.id)')
            elif action=='enable': self.db.execute('UPDATE folders SET enabled=? WHERE id=?',(int(enabled),id))
            else: raise ValueError('不正な操作です')
        self.prune_cache()
        return self.folders()
    def start(self,kind=None):
        kind=self.analysis_kind if kind is None else kind
        if kind not in ('image','audio'):raise ValueError('解析対象は写真または音声を選択してください')
        if self.thread and self.thread.is_alive(): return self.status()
        if not Path(self.config['model_path']).is_file():raise ValueError('AIモデルがありません。モデルと設定から既存ファイルを選択するか、初回セットアップで取得してください')
        if not any(f['enabled'] for f in self.folders()):raise ValueError('写真・音声のフォルダーを登録してください')
        self.analysis_kind=kind
        with self.db_lock,self.db:self.db.execute("INSERT OR REPLACE INTO settings VALUES('analysis_kind',?)",(kind,))
        self.save_preferences(manual_pause=False)
        self._resume_requested(True)
        self.stop.clear(); self.progress={'phase':'loading','processed':0,'discovered':0,'errors':0,'started':time.time(),'last_error':None}
        self.thread=threading.Thread(target=self.audio.index if kind=='audio' else self._index,name='indexer',daemon=True); self.thread.start()
        return self.status()
    def pause(self):
        self.save_preferences(manual_pause=True)
        self._resume_requested(False)
        self.stop.set()
        if self.thread and self.thread.is_alive(): self.progress['phase']='pausing'
        return {'ok':True}
    def _index(self):
        try:
            with self.ai_lock: self.ensure_engine()
            if self.stop.is_set(): return
            self.progress['phase']='scanning'
            for folder in self.folders():
                if self.stop.is_set(): break
                if not folder['enabled']: continue
                self._scan(folder)
            if self.stop.is_set(): return
            self.progress['phase']='indexing'
            with Prefetch(self._pending_jobs(),self._prepare_job,self.stop) as prepared:
                for job,image,thumb_data,prepare_error in prepared:
                    if self.stop.is_set():break
                    while self.preferences['idle_only'] and idle_seconds()<120:
                        self.progress['phase']='waiting_idle'
                        if self.stop.wait(1):break
                    if self.stop.is_set():break
                    self.progress['phase']='indexing'
                    if self.preferences['low_load'] and self.stop.wait(.35):break
                    if shutil.disk_usage(self.data).free<64*1024**2:
                        raise ValueError('保存先の空き容量が64 MiB未満です。容量を確保して再開してください')
                    with self.db_lock,self.db:
                        self.db.execute("UPDATE jobs SET state='processing',attempts=attempts+1 WHERE file_id=? AND model_id=?",(job['id'],self.model_id))
                    try:
                        if prepare_error:raise prepare_error
                        path=Path(job['path']);before=path.stat()
                        if before.st_size!=job['size'] or before.st_mtime_ns!=job['mtime_ns']:raise ValueError('ファイルが変更されました。差分更新してください')
                        with self.ai_lock:
                            if self.stop.is_set():
                                with self.db_lock,self.db:self.db.execute("UPDATE jobs SET state='pending' WHERE file_id=? AND model_id=?",(job['id'],self.model_id))
                                break
                            self.ensure_engine()
                            vector=self.engine.image(image)
                        after=path.stat()
                        if after.st_size!=before.st_size or after.st_mtime_ns!=before.st_mtime_ns:raise ValueError('解析中にファイルが変更されました')
                        thumb=f"{job['id']}-{job['mtime_ns']}.jpg"
                        thumb_path=self.cache/thumb
                        old_size=thumb_path.stat().st_size if thumb_path.exists() else 0
                        thumb_path.write_bytes(thumb_data)
                        self.cache_size+=len(thumb_data)-old_size
                        self.cache_since_prune+=1
                        with self.db_lock,self.db:
                            self.invalidate_search()
                            self.db.execute('INSERT OR REPLACE INTO embeddings VALUES(?,?,?)',(job['id'],self.model_id,array('f',vector).tobytes()))
                            self.db.execute("UPDATE jobs SET state='done',error=NULL WHERE file_id=? AND model_id=?",(job['id'],self.model_id))
                            self.db.execute('UPDATE files SET thumbnail=?,last_analyzed=?,error=NULL WHERE id=?',(thumb,time.time(),job['id']))
                        self.progress['processed']+=1
                        if self.cache_size>CACHE_LIMIT or self.cache_since_prune>=128:self.prune_cache()
                    except Exception as exc:
                        traceback.print_exc(file=sys.stderr)
                        with self.db_lock,self.db:
                            self.db.execute("UPDATE jobs SET state='error',error=? WHERE file_id=? AND model_id=?",(friendly_error(exc),job['id'],self.model_id))
                            self.db.execute('UPDATE files SET error=? WHERE id=?',(friendly_error(exc),job['id']))
                        self.progress['errors']+=1;self.progress['last_error']=friendly_error(exc)
                    with self.ai_lock:self.maintain_runtime_cache()
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            self.progress['last_error']=friendly_error(exc); self.progress['phase']='error'; return
        finally:
            if not self.stop.is_set():self._resume_requested(False)
            self.runtime_cache.size=self.runtime_cache.usage()
            if self.progress['phase']!='error': self.progress['phase']='paused' if self.stop.is_set() else 'idle'
    def _scan(self,folder,kind='image'):
        self.invalidate_search()
        path=Path(folder['path']); token=uuid.uuid4().hex; errors=[]
        if not path.is_dir():
            with self.db_lock,self.db:
                self.invalidate_search()
                self.db.execute("UPDATE folders SET status='offline',error=?,last_scan=? WHERE id=?",('接続またはアクセスを確認してください',time.time(),folder['id']))
            return
        def onerror(exc): errors.append(str(exc))
        for directory,dirs,names in os.walk(path,followlinks=False,onerror=onerror):
            dirs[:]=[n for n in dirs if not (Path(directory)/n).is_symlink() and not (Path(directory)/n).is_junction() and (Path(directory)/n).resolve()!=self.data]
            for name in names:
                if self.stop.is_set(): return
                file=Path(directory)/name
                if file.suffix.lower() not in (AUDIO_EXTENSIONS if kind=='audio' else EXTENSIONS) or file.is_symlink(): continue
                try:
                    if self.progress['discovered']%256==0 and shutil.disk_usage(self.data).free<64*1024**2:raise ValueError('保存先の空き容量が64 MiB未満です。容量を確保して再開してください')
                    stat=file.stat(); identity=f'{stat.st_dev}:{stat.st_ino}'
                    with self.db_lock,self.db:
                        old=self.db.execute('SELECT * FROM files WHERE path=?',(str(file),)).fetchone()
                        changed=old is None or old['size']!=stat.st_size or old['mtime_ns']!=stat.st_mtime_ns or old['identity']!=identity
                        self.invalidate_search()
                        if old is None:
                            id=self.db.execute('INSERT INTO files(path,name,size,mtime_ns,identity,format,kind) VALUES(?,?,?,?,?,?,?)',(str(file),name,stat.st_size,stat.st_mtime_ns,identity,file.suffix.lower().lstrip('.'),kind)).lastrowid
                        else:
                            id=old['id']
                            self.db.execute('UPDATE files SET available=1 WHERE id=?',(id,))
                            if changed:
                                self.db.execute('DELETE FROM embeddings WHERE file_id=?',(id,));self.db.execute('DELETE FROM jobs WHERE file_id=?',(id,))
                                self.db.execute('DELETE FROM audio_files WHERE file_id=?',(id,))
                                self.db.execute('UPDATE files SET size=?,mtime_ns=?,identity=?,thumbnail=NULL,error=NULL WHERE id=?',(stat.st_size,stat.st_mtime_ns,identity,id))
                        self.db.execute('INSERT OR REPLACE INTO folder_files VALUES(?,?,?)',(folder['id'],id,token))
                        if kind=='audio':self.audio.register(id)
                        else:
                            self.db.execute("INSERT INTO jobs(file_id,model_id,state) VALUES(?,?,'pending') ON CONFLICT(file_id,model_id) DO UPDATE SET state=CASE WHEN jobs.state='error' THEN 'pending' ELSE jobs.state END",(id,self.model_id))
                            if not changed and self.db.execute('SELECT 1 FROM embeddings WHERE file_id=? AND model_id=?',(id,self.model_id)).fetchone():
                                self.db.execute("UPDATE jobs SET state='done' WHERE file_id=? AND model_id=?",(id,self.model_id))
                    self.progress['discovered']+=1
                except OSError as exc: errors.append(str(exc))
        with self.db_lock,self.db:
            # Mark deletion only after a fully successful scan. A disconnected drive
            # cannot erase valid vectors, even when it disappears halfway through.
            self.invalidate_search()
            if not errors:
                self.db.execute('DELETE FROM folder_files WHERE folder_id=? AND seen!=? AND file_id IN (SELECT id FROM files WHERE kind=?)',(folder['id'],token,kind))
                self.db.execute('UPDATE files SET available=0 WHERE NOT EXISTS(SELECT 1 FROM folder_files WHERE file_id=files.id)')
            self.db.execute('UPDATE folders SET status=?,error=?,last_scan=? WHERE id=?',('partial' if errors else 'ready','; '.join(errors[:3]) if errors else None,time.time(),folder['id']))
    def status(self):
        with self.db_lock:
            counts={r['state']:r['n'] for r in self.db.execute('SELECT j.state,COUNT(*) n FROM jobs j JOIN files f ON f.id=j.file_id WHERE j.model_id=? AND f.available=1 AND EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1) GROUP BY j.state',(self.model_id or self.status_model_id,))}
            total=self.db.execute("SELECT COUNT(*) FROM files f WHERE f.kind='image' AND f.available=1 AND EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1)").fetchone()[0]
            scope=self._error_scope()
            errors=[dict(r) for r in self.db.execute(f"SELECT f.path,j.error FROM jobs j JOIN files f ON f.id=j.file_id WHERE {scope} LIMIT 10",(self.model_id or self.status_model_id,))]
        usage=sum(p.stat().st_size for p in self.data.glob('index.sqlite3*') if p.is_file())
        cache_bytes=self.cache_size
        elapsed=time.time()-self.progress['started'] if self.progress['started'] else 0
        return {'audio':self.audio.status(),'analysis_kind':self.analysis_kind,'progress':dict(self.progress),'jobs':counts,'total':total,'database_bytes':usage,'cache_bytes':cache_bytes,'runtime_cache_bytes':self.runtime_cache.size,'runtime_cache_warning':self.cache_warning,'data_path':str(self.data),'preferences':dict(self.preferences),'resume_requested':self.resume_requested(),'download':self.download.info()['download'],'free_bytes':shutil.disk_usage(self.data).free,'model_bytes':Path(self.config['model_path']).stat().st_size if Path(self.config['model_path']).is_file() else 0,'config':dict(self.config),'model':self.model_meta,'errors':errors,'speed':self.progress['processed']/elapsed if elapsed>0 else 0,'running':bool(self.thread and self.thread.is_alive())}
    def _where(self,query):
        kind=query.get('kind','image')
        if kind not in ('image','audio'):raise ValueError('検索対象は写真または音声を選択してください')
        conditions=['f.available=1','f.kind=?',"EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1 AND d.status IN ('ready','new','partial'))"];params=[kind]
        name=query.get('filename','').strip()
        if name: conditions.append('instr(lower(f.name),lower(?))>0');params.append(name)
        folder=query.get('folder','')
        if folder not in ('',None):
            try: folder_id=int(folder)
            except (TypeError,ValueError): raise ValueError('検索フォルダーの指定が不正です')
            conditions.append('EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND ff.folder_id=? AND d.enabled=1)');params.append(folder_id)
        format=query.get('format','')
        if format in ('jpg','jpeg'): conditions.append("f.format IN ('jpg','jpeg')")
        elif format: conditions.append('f.format=?');params.append(format)
        for key,operator in [('after','>='),('before','<=')]:
            if query.get(key):
                stamp=datetime.fromisoformat(query[key]).timestamp()
                if key=='before': stamp+=86399.999
                conditions.append(f'f.mtime_ns{operator}?');params.append(int(stamp*1e9))
        return ' AND '.join(conditions),params
    def search(self,**query):
        if query.get('kind')=='audio':return self.audio.search(**query)
        if query.get('kind','image')!='image':raise ValueError('検索対象は写真または音声を選択してください')
        text=query.get('text','').strip(); image=query.get('image','')
        vector=None; model_id=None
        limit=min(max(int(query.get('limit',60)),1),200);offset=max(int(query.get('offset',0)),0)
        key=json.dumps({k:v for k,v in query.items() if k not in ('limit','offset','sort')},sort_keys=True)
        if image:
            stat=Path(image).stat();key+=str((stat.st_size,stat.st_mtime_ns,stat.st_ino))
        if text or image:
            with self.db_lock:
                saved=self.search_cache
                if saved and saved['key']==key:
                    offset=self._clamp_offset(offset,limit,saved['total'])
                    return {'items':self._search_page(saved['ranked'],offset,limit),'total':saved['total'],'offset':offset,'semantic':True}
        if text or image:
            with self.ai_lock:
                self.ensure_engine();vector=self.engine.image(prepare_image(Path(image))) if image else self.engine.text(text);model_id=self.model_id
                self.maintain_runtime_cache()
        where,params=self._where(query);limit=min(max(int(query.get('limit',60)),1),200);offset=max(int(query.get('offset',0)),0)
        orders={'date_desc':'f.mtime_ns DESC,f.id','date_asc':'f.mtime_ns ASC,f.id','name_asc':'f.name COLLATE NOCASE ASC,f.id','name_desc':'f.name COLLATE NOCASE DESC,f.id','size_desc':'f.size DESC,f.id','size_asc':'f.size ASC,f.id'}
        order=orders.get(query.get('sort') or 'date_desc',orders['date_desc'])
        with self.db_lock:
            if vector:
                dot=ExactDot(vector)
                # Read only IDs/blobs while ranking. Fetch metadata for the winners.
                candidates=self.db.execute(f'SELECT f.id,e.vector FROM files f JOIN embeddings e ON e.file_id=f.id WHERE {where} AND e.model_id=?',(*params,model_id))
                ranked=[]
                for row in candidates:
                    score=dot(row['vector'])
                    if score is None: continue
                    ranked.append((score,-row['id']))
                # Cache compact scores/IDs for all candidates, no vectors or
                # metadata. Later pages must not silently lose ranks >1000.
                ranked.sort(reverse=True);count=len(ranked)
                offset=self._clamp_offset(offset,limit,count)
                self.search_cache={'key':key,'ranked':ranked,'total':count}
                results=self._search_page(ranked,offset,limit)
            else:
                count=self.db.execute(f'SELECT COUNT(*) FROM files f WHERE {where}',params).fetchone()[0]
                offset=self._clamp_offset(offset,limit,count)
                results=[dict(r) for r in self.db.execute(f'SELECT f.* FROM files f WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?',(*params,limit,offset))]
        return {'items':results,'total':count,'offset':offset,'semantic':vector is not None}
    def _clamp_offset(self,offset,limit,count):
        if count<=0:return 0
        return min(offset,((count-1)//limit)*limit)
    def _search_page(self,ranked,offset,limit):
        page=ranked[offset:offset+limit]
        if not page:return []
        ids=[-negative_id for _,negative_id in page]
        metadata={r['id']:dict(r) for r in self.db.execute('SELECT * FROM files WHERE id IN ('+','.join('?' for _ in ids)+')',ids)}
        return [{**metadata[-negative_id],'score':score} for score,negative_id in page]
    def file(self,id):
        with self.db_lock: row=self.db.execute('SELECT * FROM files WHERE id=? AND available=1 AND EXISTS(SELECT 1 FROM folder_files WHERE file_id=files.id)',(id,)).fetchone()
        if row is None: raise ValueError('このファイルは登録されていません')
        path=Path(row['path']).resolve(strict=True)
        allowed=any(path.is_relative_to(Path(f['path']).resolve()) for f in self.folders())
        if not allowed: raise ValueError('登録フォルダーの外にあるファイルは開けません')
        stat=path.stat()
        if stat.st_size!=row['size'] or stat.st_mtime_ns!=row['mtime_ns'] or f'{stat.st_dev}:{stat.st_ino}'!=row['identity']: raise ValueError('ファイルが変更されています。差分更新してください')
        return dict(row)
    def preview(self,id,thumbnail=False,segment_id=None):
        row=self.file(id)
        if row['kind']=='audio':
            if segment_id is None:raise ValueError('再生する音声区間を選択してください')
            return self.audio.preview(id,segment_id)
        cached=self.cache/row['thumbnail'] if row['thumbnail'] else None
        if thumbnail and cached and cached.is_file():
            try:
                image=cached.read_bytes();os.utime(cached,None)
                return {'url':'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii'),'file':row}
            except OSError: pass
        image=prepare_image(Path(row['path']),320 if thumbnail else 4096)
        if thumbnail:
            target=cached or self.cache/f"{row['id']}-{row['mtime_ns']}.jpg"
            try:
                old_size=target.stat().st_size if target.exists() else 0
                target.write_bytes(image)
                self.cache_size+=len(image)-old_size
                self.cache_since_prune+=1
                if target.name!=row['thumbnail']:
                    with self.db_lock,self.db:
                        self.invalidate_search()
                        self.db.execute('UPDATE files SET thumbnail=? WHERE id=?',(target.name,row['id']))
            except OSError: pass
            else:
                if self.cache_size>CACHE_LIMIT or self.cache_since_prune>=128:self.prune_cache()
        return {'url':'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii'),'file':row}
    def reference_preview(self,path):
        source=Path(path)
        if not source.is_file(): raise ValueError('参照画像が見つかりません')
        image=prepare_image(source,320)
        return {'url':'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')}
    def clear_thumbnails(self):
        if self.thread and self.thread.is_alive(): raise ValueError('解析を一時停止してから削除してください')
        freed=0
        for p in self.cache.glob('*.jpg'):
            try: freed+=p.stat().st_size;p.unlink(missing_ok=True)
            except FileNotFoundError: pass
        with self.db_lock,self.db:
            self.invalidate_search()
            self.db.execute('UPDATE files SET thumbnail=NULL')
        self.cache_size=0;self.cache_since_prune=0
        return {'bytes':freed}
    def prune_cache(self):
        # Metadata stays in SQLite; an evicted thumbnail is regenerated on demand.
        entries=[];total=0
        with self.db_lock: referenced={r[0] for r in self.db.execute('SELECT thumbnail FROM files WHERE thumbnail IS NOT NULL')}
        for p in self.cache.glob('*.jpg'):
            try:
                if p.name not in referenced: p.unlink();continue
                stat=p.stat();total+=stat.st_size;entries.append((stat.st_mtime,p,stat.st_size))
            except FileNotFoundError: pass
        for _,p,size in sorted(entries):
            if total<=CACHE_LIMIT: break
            try:p.unlink();total-=size
            except FileNotFoundError:pass
        self.cache_size=total;self.cache_since_prune=0

def hashlib_id(meta):
    import hashlib
    return hashlib.sha256(json.dumps({k:v for k,v in meta.items() if k!='backend'},sort_keys=True).encode()).hexdigest()
