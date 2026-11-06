"""Probe the installed audio API with generated WAVs in isolated child processes.

Reads the existing model; never opens the photo database or user media. Native
failures and timeouts are recorded by the parent, not mistaken for support.
"""
import argparse
import array
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import wave

ROOT = Path(__file__).resolve().parents[1]
QUERIES = ['電子音が鳴っている', '無音の音声', '人が日本語で話している']
CASES = [
    ('tone-16k-mono', 16000, 1, 1, False),
    ('silence-16k-mono', 16000, 1, 1, True),
    ('tone-48k-stereo', 48000, 2, 1, False),
    ('tone-8k-mono', 8000, 1, 1, False),
    ('tone-96k-stereo', 96000, 2, 1, False),
    ('tone-16k-30s', 16000, 1, 30, False),
]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def fixture(path, rate, channels, seconds, silent):
    samples = array.array('h')
    for n in range(rate * seconds):
        value = 0 if silent else round(6000 * math.sin(2 * math.pi * 440 * n / rate))
        samples.extend([value] * channels)
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(path), 'wb') as output:
        output.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(samples.tobytes())


def normalized(vector):
    vector = list(vector)
    if not vector or not all(math.isfinite(v) for v in vector):
        raise ValueError('Non-finite or empty embedding')
    norm = math.sqrt(sum(v * v for v in vector))
    if norm < 1e-8:
        raise ValueError('Zero embedding')
    return [v / norm for v in vector], norm


def child(args, output):
    report = {'backend': args.backend, 'verified': False, 'cases': [], 'queries': QUERIES}
    started = time.perf_counter()
    report_path = output / f'{args.backend}.json'
    save(report_path, report)
    try:
        from litert_lm import Backend, Content, EmbeddingEngine, EmbeddingOptions, InputOverflowStrategy
        selected = Backend.GPU() if args.backend == 'gpu' else Backend.CPU(thread_count=4)
        cache = output / f'cache-{args.backend}'
        cache.mkdir(exist_ok=True)
        with EmbeddingEngine(str(args.model), backend=selected, audio_backend=selected,
                             cache_dir=str(cache), min_input_length=1024,
                             max_input_length=1024) as engine:
            report['load_seconds'] = time.perf_counter() - started
            options = EmbeddingOptions(normalize=True, input_overflow_strategy=InputOverflowStrategy.ERROR)
            report['overflow_strategy'] = 'ERROR'
            queries = [normalized(engine.compute_embedding(
                'task: search query | text: ' + text, options=options).embedding)[0]
                for text in QUERIES]
            report['text_dimensions'] = [len(v) for v in queries]
            for name, rate, channels, seconds, silent in CASES:
                item = {'name': name, 'sample_rate': rate, 'channels': channels,
                        'duration_seconds': seconds}
                tick = time.perf_counter()
                try:
                    vector, norm = normalized(engine.compute_embedding(
                        Content.AudioFile(str(output / f'{name}.wav')), options=options).embedding)
                    item.update(dimensions=len(vector), norm=norm, similarities=[
                        sum(a * b for a, b in zip(query, vector, strict=True)) for query in queries])
                    item['verified'] = all(len(query) == len(vector) for query in queries)
                except Exception as exc:
                    item.update(verified=False, error=str(exc))
                item['seconds'] = time.perf_counter() - tick
                report['cases'].append(item)
                save(report_path, report)
            # A malformed file must produce an error, not a vector.
            broken = output / 'invalid.wav'
            broken.write_bytes(b'not-a-wav-artificial-fixture')
            try:
                engine.compute_embedding(Content.AudioFile(str(broken)), options=options)
                report['invalid_input_rejected'] = False
            except Exception as exc:
                report['invalid_input_rejected'] = True
                report['invalid_input_error'] = str(exc)
            recovered, _ = normalized(engine.compute_embedding(
                Content.AudioFile(str(output / 'tone-16k-mono.wav')), options=options).embedding)
            report['valid_input_after_error'] = len(recovered) == len(queries[0])
            report['verified'] = (all(item['verified'] for item in report['cases'])
                                  and report['invalid_input_rejected'] and report['valid_input_after_error'])
    except Exception as exc:
        report.update(error=str(exc), traceback=traceback.format_exc())
    report['total_seconds'] = time.perf_counter() - started
    save(report_path, report)
    return 0 if report['verified'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=ROOT / 'data/models/embeddinggemma-2-740m.litertlm')
    parser.add_argument('--output', type=Path, required=True, help='New directory under data/release-tests')
    parser.add_argument('--backend', choices=['cpu', 'gpu', 'both'], default='both')
    parser.add_argument('--timeout', type=int, default=180, help='Seconds per native child process')
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    allowed = (ROOT / 'data/release-tests').resolve()
    if allowed not in output.parents:
        parser.error('--output must be inside data/release-tests')
    args.model = args.model.resolve(strict=True)
    os.environ['MEDIA_SEARCH_DATA'] = str(output)
    os.environ['TEMP'] = os.environ['TMP'] = str(output)
    if args.child:
        if args.backend == 'both':
            parser.error('A child requires one backend')
        return child(args, output)
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    output.mkdir(parents=True, exist_ok=False)
    for name, rate, channels, seconds, silent in CASES:
        fixture(output / f'{name}.wav', rate, channels, seconds, silent)
    before = digest(args.model)
    report = {'model_sha256_before': before, 'artificial_inputs_only': True, 'backends': {}}
    for backend in (['cpu', 'gpu'] if args.backend == 'both' else [args.backend]):
        command = [sys.executable, str(Path(__file__).resolve()), '--child', '--model',
                   str(args.model), '--output', str(output), '--backend', backend]
        with (output / f'{backend}.log').open('wb') as log:
            try:
                result = subprocess.run(command, env=os.environ.copy(), stdout=log,
                                        stderr=subprocess.STDOUT, timeout=args.timeout)
                state = {'exit_code': result.returncode}
            except subprocess.TimeoutExpired:
                state = {'timed_out': True}
        detail = output / f'{backend}.json'
        if detail.exists():
            state['report'] = json.loads(detail.read_text(encoding='utf-8'))
        state['verified'] = state.get('exit_code') == 0 and state.get('report', {}).get('verified', False)
        report['backends'][backend] = state
        save(output / 'results.json', report)
        print(f'{backend}: verified={state["verified"]}', flush=True)
    report['model_sha256_after'] = digest(args.model)
    report['model_unchanged'] = before == report['model_sha256_after']
    report['verified'] = report['model_unchanged'] and all(
        item['verified'] for item in report['backends'].values())
    save(output / 'results.json', report)
    print(f'Results: {output / "results.json"}', flush=True)
    return 0 if report['verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
