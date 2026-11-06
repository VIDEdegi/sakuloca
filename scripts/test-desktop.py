"""Real Windows WebView2 + packaged Python Worker, driven by W3C WebDriver."""
import urllib.request,json,time,base64,sys,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
endpoint=os.environ.get('MEDIA_SEARCH_TEST_ENDPOINT','http://127.0.0.1:4444')

def request(method,path,payload=None):
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(endpoint+path,data=data,method=method,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=150) as response:result=json.load(response)
    value=result.get('value')
    if isinstance(value,dict) and value.get('error'):raise RuntimeError(value)
    return value

session=request('POST','/session',{'capabilities':{'alwaysMatch':{'tauri:options':{'application':os.environ.get('MEDIA_SEARCH_TEST_EXE',str(root/'target/release/embeddinggemma-media-search.exe'))}}}})['sessionId']
(root/'data/webdriver-session.txt').write_text(session)
base='/session/'+session
request('POST',base+'/timeouts',{'script':120000,'pageLoad':60000,'implicit':0})

def js(script,args=[]):return request('POST',base+'/execute/sync',{'script':script,'args':args})
def rpc(method,params={}):
    value=request('POST',base+'/execute/async',{'script':"const done=arguments[arguments.length-1];window.__TAURI_INTERNALS__.invoke('worker_request',{method:arguments[0],params:arguments[1]}).then(v=>done({result:v})).catch(e=>done({error:String(e)}));",'args':[method,params]})
    if 'error' in value:raise RuntimeError(value['error'])
    return value['result']
def wait(predicate,timeout=120):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        result=predicate()
        if result:return result
        time.sleep(.3)
    raise TimeoutError('Desktop condition not satisfied')
def screenshot(name):
    (root/'data'/name).write_bytes(base64.b64decode(request('GET',base+'/screenshot')))

report={'webview2':True}
try:
    wait(lambda:js("return !!document.querySelector('media-search-app')?.shadowRoot?.querySelector('#query')"))
    isolated=Path(rpc('status')['data_path']).resolve()
    assert isolated==root/'data/ui-test' or isolated.is_relative_to(root/'data/release-tests'),'Desktop test requires isolated data'
    js("const a=document.querySelector('media-search-app'),c=a.shadowRoot.querySelector('release-controls[mode=assistant]');c?.shadowRoot?.querySelector('#wizard')?.close();a.theme='light';a.applyTheme();")
    rpc('configure',{'model_path':str(root/'data/models/embeddinggemma-2-740m.litertlm'),'backend':'gpu'})
    rpc('add_folder',{'path':str(root/'data/fixtures/objects')});rpc('add_folder',{'path':str(root/'data/fixtures/landscapes')})
    rpc('start');wait(lambda:not rpc('status')['running'])
    status=rpc('status');assert status['jobs']['done']==4,status
    report['packaged_gpu_indexing']=status
    js("const root=document.querySelector('media-search-app').shadowRoot;const q=root.querySelector('#query');q.value='海辺の風景';q.dispatchEvent(new Event('input',{bubbles:true}));root.querySelector('form').requestSubmit();")
    wait(lambda:js("return document.querySelector('media-search-app').shadowRoot.querySelector('.card-name')?.textContent==='coast.png'"))
    wait(lambda:js("return !!document.querySelector('media-search-app').shadowRoot.querySelector('media-thumbnail')?.shadowRoot.querySelector('img')?.complete"))
    report['japanese_search_ui']=True;screenshot('desktop-light.png')
    js("document.querySelector('media-search-app').shadowRoot.querySelector('.card').click()")
    wait(lambda:js("return document.querySelector('media-search-app').shadowRoot.querySelector('#preview').open"))
    report['preview']=js("const r=document.querySelector('media-search-app').shadowRoot;return {open:r.querySelector('#preview').open,image:r.querySelector('#preview img').complete,name:r.querySelector('#preview strong').textContent}")
    assert report['preview']['name']=='coast.png'
    wait(lambda:js("return document.querySelector('media-search-app').shadowRoot.querySelector('#preview img').complete"))
    screenshot('desktop-preview.png')
    time.sleep(.5)
    base_width=js("return document.querySelector('media-search-app').shadowRoot.querySelector('#preview img').getBoundingClientRect().width")
    js("document.querySelector('media-search-app').shadowRoot.querySelector('#preview [aria-label=拡大]').click()")
    report['zoom']=wait(lambda:js("return Array.from(document.querySelector('media-search-app').shadowRoot.querySelectorAll('#preview .preview-tools span')).find(s=>s.textContent.trim()==='125%')?.textContent"))
    assert report['zoom']=='125%'
    zoom_width=js("return document.querySelector('media-search-app').shadowRoot.querySelector('#preview img').getBoundingClientRect().width")
    time.sleep(.5)
    zoom_width=js("return document.querySelector('media-search-app').shadowRoot.querySelector('#preview img').getBoundingClientRect().width")
    debug=js("const a=document.querySelector('media-search-app'),i=a.shadowRoot.querySelector('#preview img');return {zoom:a.zoom,base:a.previewBaseWidth,style:i.getAttribute('style'),computed:getComputedStyle(i).width,max:getComputedStyle(i).maxWidth}")
    assert abs(zoom_width/base_width-1.25)<.02,(base_width,zoom_width,debug)
    report['zoom_geometry_ratio']=zoom_width/base_width
    js("document.querySelector('media-search-app').shadowRoot.querySelector('#preview').close();document.querySelector('media-search-app').shadowRoot.querySelector('[aria-label=リスト表示]').click()")
    wait(lambda:js("return !!document.querySelector('media-search-app').shadowRoot.querySelector('.list')"));report['list_view']=True
    js("const r=document.querySelector('media-search-app').shadowRoot;Array.from(r.querySelectorAll('button')).find(b=>b.textContent==='モデルと設定').click()")
    wait(lambda:js("return document.querySelector('media-search-app').shadowRoot.querySelector('#settings').open"))
    js("const select=document.querySelector('media-search-app').shadowRoot.querySelector('[aria-label=表示テーマ]');select.value='dark';select.dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('media-search-app').shadowRoot.querySelector('#settings').close()")
    wait(lambda:js("return document.querySelector('media-search-app').getAttribute('theme')==='dark'"));screenshot('desktop-dark.png');report['dark_theme']=True
    for width in [1000,860]:
        request('POST',base+'/window/rect',{'width':width,'height':760});time.sleep(.5)
        overflow=js("const r=document.querySelector('media-search-app').shadowRoot;return r.querySelector('main').scrollWidth>r.querySelector('main').clientWidth")
        assert not overflow,f'Overflow at {width}px'
    report['resize_no_horizontal_overflow']=True
    image_results=rpc('search',{'image':str(root/'data/fixtures/objects/still-life.jpg')})
    assert image_results['items'][0]['name']=='still-life.jpg';report['similar_image_ipc']=True
    report['frontend_title']=js('return document.title')
    assert rpc('status')['runtime_cache_bytes']>0
    rpc('clear_runtime_cache');assert rpc('status')['runtime_cache_bytes']==0
    assert rpc('search',{'text':'海辺'})['items'][0]['name']=='coast.png'
    report['cache_clear_keeps_index']=True
    (root/'data/desktop-test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
finally:
    request('DELETE',base)
