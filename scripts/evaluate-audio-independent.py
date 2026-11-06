"""Frozen, independent environment/music/Japanese speech retrieval comparison."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'worker'))
from ai import LiteRtAudioBackend, AUDIO_QUERY_PREFIX, QUERY_PREFIX
from audio import inspect_wave, read_window
from audio_queries import resolve_audio_query

LABELS={
 'dog':('犬の鳴き声','A dog barking'), 'cat':('猫の鳴き声','A cat meowing'),
 'chirping_birds':('鳥のさえずり','Birds chirping'), 'rain':('雨が降る音','Rain falling'),
 'sea_waves':('海の波の音','Ocean waves'), 'wind':('風が吹く音','Wind blowing'),
 'thunderstorm':('雷の音','Thunderstorm'), 'crackling_fire':('火がぱちぱち燃える音','Fire crackling'),
 'crickets':('コオロギの鳴き声','Crickets chirping'), 'clock_alarm':('目覚まし時計の音','An alarm clock ringing'),
 'keyboard_typing':('キーボードを打つ音','Typing on a keyboard'), 'siren':('サイレンの音','An emergency siren'),
 'car_horn':('車のクラクション','A car horn honking'), 'engine':('エンジンの音','An engine running'),
 'helicopter':('ヘリコプターの音','A helicopter flying'), 'train':('列車が走る音','A train moving'),
 'vacuum_cleaner':('掃除機の音','A vacuum cleaner running'), 'church_bells':('教会の鐘の音','Church bells ringing'),
 'clapping':('拍手の音','People clapping'), 'laughing':('人の笑い声','People laughing'),
 'jazz':('ジャズの音楽','Jazz music'), 'drums':('ドラムとベースの音楽','Drum and bass music'),
 'orchestra':('オーケストラの音楽','Orchestral music'), 'piano':('ピアノの演奏','Piano music'),
}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--backend',choices=['cpu','gpu'],default='gpu');p.add_argument('--vectors',type=Path);args=p.parse_args()
 sources,output=args.sources.resolve(strict=True),args.output.resolve();allowed=(ROOT/'data/release-tests').resolve()
 assert allowed in sources.parents and allowed in output.parents
 output.mkdir(parents=True,exist_ok=False);(output/'cache').mkdir()
 selected=json.loads((sources/'selected.json').read_text(encoding='utf-8-sig'));speech=json.loads((sources/'speech.json').read_text(encoding='utf-8'))
 files=[(r['filename'],r['category'],'environment') for r in selected]
 files += [(f'music-{n}.wav',n,'music') for n in ['jazz','drums','orchestra','piano']]
 files += [(r['filename'],r['filename'],'synthetic_speech') for r in speech]
 queries=[dict(text=text,expected=category,group='environment' if category in [r['category'] for r in selected] else 'music',language=language) for category in LABELS for language,text in zip(['ja','en'],LABELS[category])]
 queries += [dict(text=r[language],expected=r['filename'],group='synthetic_speech',language=language) for r in speech for language in ['ja','en']]
 engine=LiteRtAudioBackend(ROOT/'data/models/embeddinggemma-2-740m.litertlm',args.backend,output/'cache');report=dict(backend=args.backend,files=len(files),queries=len(queries),source_hashes={},results={});audios=[];started=time.monotonic()
 try:
  saved = json.loads(args.vectors.read_text(encoding='utf-8')) if args.vectors else None
  if saved: assert saved['backend']==args.backend and len(saved['audio_vectors'])==len(files)
  for name,category,group in files:
   path=sources/name
   if saved:
    assert hashlib.sha256(path.read_bytes()).hexdigest()==saved['source_hashes'][name]
    vector=next(v for n,c,g,v in saved['audio_vectors'] if n==name and c==category)
   else:
    info=inspect_wave(path);data,_=read_window(path,0,min(info.frames,info.rate*30));vector=engine.audio(data)
   audios.append((name,category,group,vector));report['source_hashes'][name]=hashlib.sha256(path.read_bytes()).hexdigest()
  for mode,prefix in [('legacy',QUERY_PREFIX),('corrected',AUDIO_QUERY_PREFIX)]:
   cases=[]
   for query in queries:
    actual=resolve_audio_query(query['text']) if mode=='corrected' else query['text']
    vector=engine._vector(prefix+actual);ranked=sorted([(sum(x*y for x,y in zip(vector,audio)),name,category) for name,category,_,audio in audios],reverse=True)
    rank=next(i+1 for i,(_,_,category) in enumerate(ranked) if category==query['expected'])
    cases.append(dict(**query,interpreted_query=actual,rank=rank,top=[dict(name=name,category=category,score=score) for score,name,category in ranked[:5]]))
   report['results'][mode]=dict(top1=sum(c['rank']==1 for c in cases),top3=sum(c['rank']<=3 for c in cases),cases=cases)
   print(json.dumps(dict(mode=mode,top1=report['results'][mode]['top1'],top3=report['results'][mode]['top3'],queries=len(cases)),ensure_ascii=False),flush=True)
 finally:engine.close()
 report['seconds']=time.monotonic()-started;report['audio_vectors']=audios
 (output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
