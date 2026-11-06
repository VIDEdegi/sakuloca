"""Compare official query templates against an existing isolated audio index."""
import argparse
from array import array
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from ai import LiteRtAudioBackend

PREFIXES = {
    'legacy_guide': 'task: search query | text: ',
    'model_card': 'task: search result | query: ',
    'classification': 'task: classification | query: ',
    'raw': '',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=['cpu', 'gpu'], default='gpu')
    args = parser.parse_args()
    data, output = args.index.resolve(strict=True), args.output.resolve()
    allowed = (ROOT / 'data/release-tests').resolve()
    assert allowed in data.parents and allowed in output.parents
    output.mkdir(parents=True, exist_ok=False)
    (output / 'cache').mkdir()
    spec = importlib.util.spec_from_file_location('evaluation', ROOT / 'scripts/evaluate-audio-quality.py')
    evaluation = importlib.util.module_from_spec(spec); spec.loader.exec_module(evaluation)
    with sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        candidates = [(name, array('f', blob)) for name, blob in db.execute('SELECT f.name,e.vector FROM audio_embeddings e JOIN audio_segments s ON s.id=e.segment_id JOIN files f ON f.id=s.file_id WHERE f.name NOT LIKE ? ORDER BY s.id', ('boundary-%',))]
    engine = LiteRtAudioBackend(ROOT / 'data/models/embeddinggemma-2-740m.litertlm', args.backend, output / 'cache')
    results = {}; started = time.monotonic()
    try:
        for name, prefix in PREFIXES.items():
            cases = []
            for expected, queries in evaluation.CASES:
                for query in queries:
                    vector = engine._vector(prefix + query)
                    ranked = {}
                    for candidate, audio in candidates:
                        score = sum(x*y for x, y in zip(audio, vector))
                        ranked[candidate] = max(score, ranked.get(candidate, -1))
                    ranking = sorted(ranked, key=ranked.get, reverse=True)
                    cases.append(dict(query=query, expected=expected, rank=ranking.index(expected)+1,
                                      ranking=[dict(name=n,score=ranked[n]) for n in ranking]))
            results[name] = dict(prefix=prefix, top1=sum(c['rank']==1 for c in cases),
                                 top3=sum(c['rank']<=3 for c in cases), queries=len(cases), cases=cases)
            print(json.dumps({k:v for k,v in results[name].items() if k!='cases'}, ensure_ascii=False), flush=True)
    finally: engine.close()
    (output / 'results.json').write_text(json.dumps(dict(backend=args.backend, seconds=time.monotonic()-started, results=results),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__': main()
