"""Small, declared-query retrieval evaluation on public samples, never user media."""
import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audio_runtime', ROOT / 'scripts/test-audio-runtime.py')
runtime = importlib.util.module_from_spec(spec); spec.loader.exec_module(runtime)

# Declared before inference; this is a small diagnostic set, not an accuracy benchmark.
CASES = [
    ('cat.wav', ['猫が鳴いている', 'A cat meowing']),
    ('456440__inspectorj__bird-whistling-robin-single-13.wav', ['鳥のさえずり', 'A bird chirping']),
    ('train.wav', ['汽笛を鳴らす蒸気機関車', 'A steam train whistle']),
    ('google-wonderful.wav', ['よい一日をと挨拶している声', 'Have a wonderful day']),
    ('sorohanro_-_solo-trumpet-06.wav', ['トランペットの演奏', 'A trumpet playing music']),
    ('147793__setuniman__sweet-waltz-0i-22mi.wav', ['ピアノの音楽', 'Piano music']),
    ('speech-whistle.wav', ['人が口笛を吹く', 'A person whistling']),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=['cpu', 'gpu'], default='gpu')
    parser.add_argument('--packed', action='store_true')
    parser.add_argument('--reuse', action='store_true', help='Reopen this script\'s existing isolated evaluation index')
    args = parser.parse_args()
    output, sources = args.output.resolve(), args.sources.resolve(strict=True)
    allowed = (ROOT / 'data/release-tests').resolve()
    assert allowed in output.parents and allowed in sources.parents
    output.mkdir(parents=True, exist_ok=args.reuse)
    media = output / 'evaluation'; media.mkdir(exist_ok=args.reuse)
    if not args.reuse:
        for path in sources.glob('*.wav'): shutil.copy2(path, media / path.name)
    # Place the complete cat call across each adjacent window edge. The 5s
    # overlap protects short events. A longer event can straddle both windows;
    # measure retrieval of partial events rather than asserting full coverage.
    with wave.open(str(media / 'cat.wav'), 'rb') as source:
        rate, pcm = source.getframerate(), source.readframes(source.getnframes())
    boundaries = []
    for offset in [24, 28, 49, 53]:
        name = f'boundary-{offset}.wav'
        if not args.reuse:
            with wave.open(str(media / name), 'wb') as target:
                target.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                target.writeframes(b'\0' * (offset * rate * 2) + pcm + b'\0' * ((65 - offset) * rate * 2 - len(pcm)))
        boundaries.append((name, offset * 1000, offset * 1000 + len(pcm) * 1000 // (2 * rate)))
    hashes = {p.name: runtime.hashlib.sha256(p.read_bytes()).hexdigest() for p in media.iterdir()}
    data = output / 'index'
    command = [str(ROOT / 'dist-worker/media-search-worker/media-search-worker.exe')] if args.packed else [runtime.sys.executable, str(ROOT / 'worker/main.py')]
    worker = runtime.Worker(command, data, output / 'worker.log')
    report = dict(backend=args.backend, packed=args.packed, public_samples_only=True, cases=[], boundaries=[])
    started = time.monotonic()
    try:
        worker.rpc('configure', model_path=str(ROOT / 'data/models/embeddinggemma-2-740m.litertlm'), backend=args.backend)
        worker.rpc('preferences', setup_complete=True)
        worker.rpc('add_folder', path=str(media))
        if not args.reuse: worker.rpc('start', kind='audio'); indexed = worker.wait()
        else: indexed = worker.rpc('status')
        assert indexed['audio']['error_files'] == 0, indexed
        for expected, queries in CASES:
            for query in queries:
                result = worker.rpc('search', kind='audio', text=query, limit=200)
                # Exclude the constructed duplicates, rank distinct files.
                unique = {}
                for item in result['items']:
                    if not item['name'].startswith('boundary-'): unique.setdefault(item['name'], item['score'])
                names = list(unique)
                row = dict(query=query, expected=expected, rank=names.index(expected) + 1,
                           ranking=[dict(name=name, score=score) for name, score in unique.items()])
                report['cases'].append(row)
                print(json.dumps({k: row[k] for k in ['query', 'expected', 'rank']}, ensure_ascii=False), flush=True)
        for name, event_start, event_end in boundaries:
            result = worker.rpc('search', kind='audio', filename=name, text='猫が鳴いている', limit=200)
            complete = [i for i in result['items'] if i['start_ms'] <= event_start and i['end_ms'] >= event_end]
            best = result['items'][0]
            preview = worker.rpc('preview', id=best['id'], segment_id=best['segment_id'])
            assert preview['file']['start_ms'] <= event_start and preview['file']['end_ms'] >= event_end, preview['file']
            overlap = max(0, min(event_end, best['end_ms']) - max(event_start, best['start_ms']))
            report['boundaries'].append(dict(name=name, event=[event_start, event_end],
                top_window=[best['start_ms'], best['end_ms']],
                top_contains_complete_event=best in complete,
                top_event_coverage=overlap / (event_end - event_start),
                preview_contains_complete_event=True,
                preview_window=[preview['file']['start_ms'], preview['file']['end_ms']],
                complete_event_rank=min((result['items'].index(i) + 1 for i in complete), default=None),
                ranking=[{k: i[k] for k in ['start_ms', 'end_ms', 'score']} for i in result['items']]))
        report.update(jobs=indexed['audio']['jobs'],
            top1=sum(c['rank'] == 1 for c in report['cases']),
            top3=sum(c['rank'] <= 3 for c in report['cases']), queries=len(report['cases']),
            boundary_top1=sum(b['top_contains_complete_event'] for b in report['boundaries']),
            peak_worker_working_set_bytes=runtime.peak_rss(worker.process) if args.packed else None,
            seconds=time.monotonic() - started, completed=True,
            limitation='Small public sample set; speech topic/words, Japanese speech and noisy mixtures are not established.')
    finally:
        worker.close()
    assert hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in media.iterdir()}
    report['source_hashes'] = hashes
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ['cases', 'source_hashes']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
