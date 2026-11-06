"""Diagnose padding and audio/query embedding shapes on public evaluation clips."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from audio import inspect_wave, read_window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); output = args.output.resolve()
    assert (ROOT / 'data/release-tests').resolve() in output.parents
    output.mkdir(parents=True, exist_ok=False)
    from litert_lm import Backend, Content, EmbeddingEngine, EmbeddingOptions, InputOverflowStrategy
    spec = importlib.util.spec_from_file_location('evaluation', ROOT / 'scripts/evaluate-audio-quality.py')
    evaluation = importlib.util.module_from_spec(spec); spec.loader.exec_module(evaluation)
    results = {}
    for minimum in [1024, 128]:
        cache = output / str(minimum); cache.mkdir()
        selected = Backend.GPU()
        engine = EmbeddingEngine(str(ROOT / 'data/models/embeddinggemma-2-740m.litertlm'), backend=selected,
                                 audio_backend=selected, cache_dir=str(cache), min_input_length=minimum, max_input_length=1024)
        options = EmbeddingOptions(normalize=True, input_overflow_strategy=InputOverflowStrategy.ERROR)
        def embed(content):
            vector = list(engine.compute_embedding(content, options=options).embedding)
            assert len(vector)==768 and all(math.isfinite(x) for x in vector)
            return vector
        started = time.monotonic()
        try:
            audios=[]
            for path in (ROOT / 'data/release-tests/audio-eval-sources-20261030').glob('*.wav'):
                info=inspect_wave(path)
                for start,end,_,_ in info.windows():
                    data,_=read_window(path,start,end)
                    audios.append((path.name,embed(Content.AudioBytes(data))))
            queries=[];cases=[]
            for expected,texts in evaluation.CASES:
                for query in texts:
                    vector=embed('task: search result | query: '+query); queries.append((query,vector))
                    scores={}
                    for name,audio in audios: scores[name]=max(scores.get(name,-1),sum(a*b for a,b in zip(vector,audio)))
                    ranking=sorted(scores,key=scores.get,reverse=True)
                    cases.append(dict(query=query,expected=expected,rank=ranking.index(expected)+1,ranking=ranking))
            results[str(minimum)]=dict(top1=sum(c['rank']==1 for c in cases),top3=sum(c['rank']<=3 for c in cases),cases=cases,
                                       audios=audios,queries=queries,seconds=time.monotonic()-started)
            print(json.dumps(dict(minimum=minimum,top1=results[str(minimum)]['top1'],top3=results[str(minimum)]['top3']),ensure_ascii=False),flush=True)
        finally: engine.close()
    (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
