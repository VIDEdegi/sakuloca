"""Versioned JSON-lines IPC over stdin/stdout. No listening socket or telemetry."""
import argparse,json,sys,traceback
from pathlib import Path
from library import Library
from user_errors import friendly_error

def main():
    # Native Rust IPC is UTF-8 even on a Japanese Windows console.
    for stream in (sys.stdin,sys.stdout,sys.stderr):
        if stream and hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser();parser.add_argument('--data',required=True)
    parser.add_argument('--probe',action='store_true');parser.add_argument('--model');parser.add_argument('--backend',choices=['cpu','gpu'],default='gpu')
    args=parser.parse_args()
    if args.probe:
        from ai import LiteRtBackend
        from PIL import Image
        import io,time
        start=__import__('time').monotonic()
        engine=LiteRtBackend(Path(args.model),args.backend,':nocache')
        try:
            engine.text('写真');image=io.BytesIO();Image.new('RGB',(64,64),'red').save(image,format='JPEG');engine.image(image.getvalue())
            print(json.dumps({'dimensions':engine.dimensions,'seconds':round(time.monotonic()-start,2)}),flush=True)
        finally:engine.close()
        return
    library=Library(Path(args.data))
    handlers={'status':lambda _:library.status(),'clear_runtime_cache':lambda _:library.clear_runtime_cache(),'folders':lambda _:library.folders(),'add_folder':lambda p:library.add_folder(p['path']),'folder_action':lambda p:library.folder_action(**p),'configure':lambda p:library.configure(**p),'start':lambda p:library.start(**p),'pause':lambda _:library.pause(),'search':lambda p:library.search(**p),'preview':lambda p:library.preview(**p),'reference_preview':lambda p:library.reference_preview(p['path']),'clear_thumbnails':lambda _:library.clear_thumbnails(),'file':lambda p:library.file(p['id']),'preferences':lambda p:library.save_preferences(**p),'environment':lambda _:library.environment(),'diagnose':lambda _:library.diagnose(),'model_info':lambda _:library.download.info(),'download_model':lambda p:library.download.start(**p),'cancel_download':lambda _:library.download.cancel(),'migrate':lambda p:library.migrate(**p),'errors':lambda p:library.errors(**p),'retry_errors':lambda p:library.retry_errors(**p)}
    try:
        for line in sys.stdin:
            request={}
            try:
                if len(line)>1024*1024:raise ValueError('リクエストが大きすぎます')
                request=json.loads(line)
                if request.get('version')!=1:raise ValueError('IPCバージョンが不一致です')
                if request['method']=='shutdown':
                    library.stop.set();library.download.cancel();result={'ok':True}
                else:result=handlers[request['method']](request.get('params',{}))
                reply={'id':request.get('id'),'result':result}
            except Exception as exc:
                traceback.print_exc(file=sys.stderr);reply={'id':request.get('id'),'error':friendly_error(exc)}
            print(json.dumps(reply,ensure_ascii=False,allow_nan=False),flush=True)
            if request.get('method')=='shutdown':break
    finally:library.close()

if __name__=='__main__':main()
