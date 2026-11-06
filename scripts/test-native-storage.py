"""Native pointer migration in a driver with isolated LOCALAPPDATA and no MEDIA_SEARCH_DATA."""
from contextlib import closing
import hashlib,json,os,shutil,sqlite3,subprocess,sys,time,urllib.request
from pathlib import Path

root=Path(__file__).resolve().parents[1];test=root/'data/release-tests/native-storage'
source=test/'source';target=test/'target';pointer=test/'localappdata/LocalPhotoSearch/storage.json'
def vectors(path):
    with closing(sqlite3.connect((path/'index.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
        h=hashlib.sha256()
        for i,m,v in db.execute('SELECT file_id,model_id,vector FROM embeddings ORDER BY file_id,model_id'):h.update(str(i).encode()+m.encode()+v)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        return h.hexdigest()
if '--prepare' in sys.argv:
    test.mkdir(exist_ok=False);source.mkdir();target.mkdir();pointer.parent.mkdir(parents=True)
    previous=root/'data/release-tests/legacy-ui-final'
    with closing(sqlite3.connect((previous/'index.sqlite3').as_uri()+'?mode=ro',uri=True)) as old:
        with closing(sqlite3.connect(source/'index.sqlite3')) as copied:old.backup(copied)
    shutil.copytree(previous/'thumbnails',source/'thumbnails')
    (source/'runtime-cache').mkdir();(source/'runtime-cache/migration-test-note.txt').write_text('Preserve source and copied cache notes',encoding='utf-8')
    pointer.write_text(json.dumps({'data_path':str(source)}),encoding='utf-8')
    print('Prepared isolated native storage pointer');raise SystemExit
assert '--run' in sys.argv
user_pointer=Path(os.environ['LOCALAPPDATA'])/'LocalPhotoSearch/storage.json'
assert user_pointer!=pointer
original_pointer=user_pointer.read_bytes()
protected=vectors(source)
endpoint=os.environ.get('MEDIA_SEARCH_TEST_ENDPOINT','http://127.0.0.1:4448')
exe=root/'target/release/embeddinggemma-media-search.exe'
def request(method,path,payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(endpoint+path,data=data,method=method,headers={'Content-Type':'application/json'}),timeout=150) as response:value=json.load(response)['value']
    if isinstance(value,dict) and value.get('error'):raise RuntimeError(value)
    return value
def open_session():
    session=request('POST','/session',{'capabilities':{'alwaysMatch':{'tauri:options':{'application':str(exe)}}}})['sessionId']
    return '/session/'+session
base=open_session()
def js(script):return request('POST',base+'/execute/sync',{'script':script,'args':[]})
def rpc(method,params={}):
    value=request('POST',base+'/execute/async',{'script':"const done=arguments[arguments.length-1];window.__TAURI_INTERNALS__.invoke('worker_request',{method:arguments[0],params:arguments[1]}).then(result=>done({result})).catch(e=>done({rpc_error:String(e)}));",'args':[method,params]})
    assert 'result' in value,value
    return value['result']
def wait(action):
    end=time.monotonic()+90
    while time.monotonic()<end:
        value=action()
        if value:return value
        time.sleep(.3)
    raise TimeoutError('Native storage condition timed out')
def own_app_running():
    shell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(shell),'-NoProfile','-Command',"Get-CimInstance Win32_Process -Filter \"Name='embeddinggemma-media-search.exe'\" | Select-Object ExecutablePath | ConvertTo-Json -Compress"],capture_output=True,text=True,creationflags=0x08000000,timeout=30,check=True)
    rows=json.loads(result.stdout or '[]');rows=rows if isinstance(rows,list) else [rows]
    return any(row.get('ExecutablePath') and Path(row['ExecutablePath'])==exe for row in rows)
try:
    wait(lambda:js("return !!document.querySelector('media-search-app')?.shadowRoot?.querySelector('#query')"))
    assert Path(rpc('status')['data_path']).resolve()==source
    assert rpc('status')['jobs'].get('done')==4
    # Use the same native command as settings; close/relaunch only after pointer commit.
    request('POST',base+'/execute/sync',{'script':"setTimeout(()=>window.__TAURI_INTERNALS__.invoke('migrate_storage',{destination:arguments[0]}).catch(e=>window.migrationError=String(e)),20);return true",'args':[str(target)]})
    migrated=target/'LocalPhotoSearchData'
    wait(lambda:Path(json.loads(pointer.read_text())['data_path'])==migrated)
    wait(lambda:not own_app_running())
    try:request('DELETE',base)
    except Exception:pass
    assert vectors(source)==protected and vectors(migrated)==protected
    assert (migrated/'runtime-cache/migration-test-note.txt').exists() is False
    assert (source/'runtime-cache/migration-test-note.txt').read_bytes()==b'Preserve source and copied cache notes'
    assert len(list((migrated/'thumbnails').glob('*.jpg')))==len(list((source/'thumbnails').glob('*.jpg')))>0
    base=open_session();request('POST',base+'/timeouts',{'script':120000})
    wait(lambda:js("return !!document.querySelector('media-search-app')?.shadowRoot?.querySelector('#query')"))
    assert Path(rpc('status')['data_path']).resolve()==migrated
    assert rpc('search',{'text':'海辺の風景'})['items'][0]['name']=='coast.png'
    assert user_pointer.read_bytes()==original_pointer
    report={'native_pointer_migration':True,'relaunched_using_migrated_data':True,'japanese_search_after_migration':True,'four_saved_vectors_identical':True,'source_preserved':True,'runtime_cache_not_copied_and_thumbnails_copied':True,'real_user_pointer_unchanged':True,'model_reference_not_copied':not list(migrated.rglob('*.litertlm'))}
    (root/'data/release-tests/native-storage-results.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
finally:
    try:request('DELETE',base)
    except Exception:pass
