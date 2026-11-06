"""Packaged Worker endurance, process recovery and resource measurements.

Only generated tones and an artificial photo are used, under release-tests.
ffmpeg is a fixture creation tool on the developer PC, never an app dependency.
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import importlib.util
import io
import base64
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('runtime', ROOT / 'scripts/test-audio-runtime.py')
rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)


def cpu_seconds(process):
    values = [wintypes.FILETIME() for _ in range(4)]
    function = ctypes.windll.kernel32.GetProcessTimes
    function.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    if function(wintypes.HANDLE(int(process._handle)), *(ctypes.byref(v) for v in values)):
        return sum((v.dwHighDateTime << 32) + v.dwLowDateTime for v in values[2:]) / 1e7


def vectors(data):
    with sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        return {row[0]: hashlib.sha256(row[1]).hexdigest() for row in db.execute('SELECT segment_id,vector FROM audio_embeddings')}


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def sampled_cpu_seconds(samples):
    # The counter restarts with each Worker. Sampling misses the short interval
    # between its final sample and process exit, so report an approximation.
    total = peak = previous = 0
    for sample in samples:
        current = sample['worker_cpu_seconds']
        if current < previous:
            total += peak
            peak = 0
        peak = max(peak, current)
        previous = current
    return total + peak


def verify_existing(output):
    """Repeat only final checks after a harness failure; never starts analysis."""
    report = json.loads((output / 'results.json').read_text(encoding='utf-8'))
    data = output / 'index'
    assert report['synthetic_only'] and len(report['recoveries']) == 2
    with sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        jobs = dict(db.execute('SELECT state,COUNT(*) FROM audio_jobs GROUP BY state'))
        assert jobs == {'done': 864}, jobs
        retried = db.execute('SELECT segment_id,attempts FROM audio_jobs WHERE attempts>1').fetchall()
        # This fixed fixture has consecutive IDs. Pause and crash each may
        # retry the next, unfinished window; completed windows cannot retry.
        expected = {entry['saved_segments'] + 1 for entry in report['recoveries']}
        assert {row[0] for row in retried} <= expected and all(row[1] == 2 for row in retried), retried
        finish = db.execute('SELECT MAX(last_analyzed) FROM files').fetchone()[0]
    for path in (output / 'artificial').iterdir():
        assert digest(path) == report['source_hashes'][path.name]
    saved = vectors(data); photo = rt.photo_hash(data)
    worker = rt.Worker([str(ROOT / 'dist-worker/media-search-worker/media-search-worker.exe')], data, output / 'final-verification-worker.log')
    try:
        status = worker.rpc('status'); assert not status['running'] and status['audio']['analyzed'] == 3
        assert worker.rpc('search', kind='audio')['total'] == len(saved) == 864
        for extension in ['wav', 'mp3', 'flac']:
            result = worker.rpc('search', kind='audio', format=extension, offset=287, limit=1)
            assert result['total'] == 288
            tail = result['items'][0]; assert tail['end_ms'] == 7200000
            preview = worker.rpc('preview', id=tail['id'], segment_id=tail['segment_id'])
            with wave.open(io.BytesIO(base64.b64decode(preview['url'].split(',')[1])), 'rb') as decoded:
                assert decoded.getnframes() == (preview['file']['end_ms'] - preview['file']['start_ms']) * decoded.getframerate() // 1000
        assert vectors(data) == saved and rt.photo_hash(data) == photo
    finally: worker.close()
    samples = report['resource_samples']
    report.update(completed=True, final_verification_only=True, jobs=jobs, saved_index_reopened=True,
                  source_files_unchanged=True, photo_vectors_unchanged=True, integrity='ok',
                  retried_segments=len(retried), retried_segment_ids=[row[0] for row in retried],
                  peak_worker_working_set_bytes=max(s['worker_peak_working_set_bytes'] for s in samples),
                  worker_cpu_seconds=sampled_cpu_seconds(samples),
                  cpu_measurement='Approximation from samples across three Worker lifetimes; final checks excluded.',
                  seconds=finish - (output / 'worker-1.log').stat().st_birthtime,
                  duration_measurement='Worker log creation to last file analysis completion; final verification excluded.',
                  harness_correction='Initial final assertion allowed only one retry, but intentional pause and crash interrupted separate windows. Read-only audit found only IDs 11 and 21 retried, all 864 done, integrity/FK OK. Final checks repeated without analysis.')
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ['resource_samples','source_hashes']}, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=7200)
    parser.add_argument('--verify-existing', action='store_true')
    parser.add_argument('--backend', choices=['cpu','gpu'], default='gpu')
    args = parser.parse_args()
    output = args.output.resolve()
    assert (ROOT / 'data/release-tests').resolve() in output.parents
    if args.verify_existing:
        verify_existing(output)
        return
    assert 120 <= args.seconds <= 7200
    output.mkdir(parents=True, exist_ok=False)
    media = output / 'artificial'; media.mkdir()
    # Stereo 48kHz stresses window memory and decoding, not merely file headers.
    wav = media / '長時間.wav'
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-f', 'lavfi',
               '-i', 'sine=frequency=440:sample_rate=48000', '-t', str(args.seconds), '-ac', '2']
    subprocess.run([*command, '-c:a', 'pcm_s16le', str(wav)], check=True, timeout=300)
    for extension, codec in [('mp3', 'libmp3lame'), ('flac', 'flac')]:
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(wav),
                        '-c:a', codec, str(media / ('長時間.' + extension))], check=True, timeout=300)
    rt.Image.new('RGB', (128, 96), 'red').save(media / 'red-square.jpg')
    source_hashes = {p.name: digest(p) for p in media.iterdir()}
    data = output / 'index'
    worker_command = [str(ROOT / 'dist-worker/media-search-worker/media-search-worker.exe')]
    report = dict(packed=True, backend=args.backend, synthetic_only=True, source_seconds=args.seconds,
                  source_hashes=source_hashes, resource_samples=[], recoveries=[])
    report_path = output / 'results.json'
    def save(): report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    worker = rt.Worker(worker_command, data, output / 'worker-1.log')
    started = time.monotonic(); last_print = started; last_gpu = 0
    peak_memory = 0; cpu_total = 0
    try:
        worker.rpc('configure', model_path=str(ROOT / 'data/models/embeddinggemma-2-740m.litertlm'), backend=args.backend)
        worker.rpc('preferences', setup_complete=True)
        worker.rpc('add_folder', path=str(media))
        worker.rpc('start', kind='image'); worker.wait()
        photo_before = rt.photo_hash(data)
        worker.rpc('start', kind='audio')
        pause_done = crash_done = False
        checkpoint = min(10, max(1, args.seconds // 100))
        deadline = time.monotonic() + 5400
        while time.monotonic() < deadline:
            status = worker.rpc('status'); jobs = status['audio']['jobs']; done = jobs.get('done', 0)
            assert status['progress']['phase'] != 'error', status
            assert not jobs.get('error'), status
            peak_memory = max(peak_memory, rt.peak_rss(worker.process) or 0)
            now = time.monotonic()
            if now - last_gpu >= 5:
                sample = dict(seconds=now - started, worker_peak_working_set_bytes=rt.peak_rss(worker.process),
                              worker_cpu_seconds=cpu_seconds(worker.process), completed_segments=done)
                try:
                    gpu = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,utilization.gpu', '--format=csv,noheader,nounits'],
                                         capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                    memory, utilization = gpu.stdout.strip().splitlines()[0].split(',')
                    sample.update(whole_gpu_memory_mib=int(memory), whole_gpu_utilization_percent=int(utilization))
                except (OSError, ValueError, subprocess.TimeoutExpired): pass
                report['resource_samples'].append(sample); last_gpu = now; save()
            if not pause_done and done >= checkpoint:
                t = time.monotonic(); worker.rpc('pause'); paused = worker.wait()
                saved = vectors(data)
                assert paused['progress']['phase'] == 'paused' and saved
                cpu_total += cpu_seconds(worker.process) or 0
                worker.close(); worker = rt.Worker(worker_command, data, output / 'worker-2.log')
                assert vectors(data) == saved
                assert not worker.rpc('status')['running']
                worker.rpc('start', kind='audio')
                report['recoveries'].append(dict(kind='pause-close-reopen-resume', saved_segments=len(saved), seconds=time.monotonic()-t))
                pause_done = True
            elif pause_done and not crash_done and done >= checkpoint * 2 and jobs.get('processing'):
                saved = vectors(data); cpu_total += cpu_seconds(worker.process) or 0
                worker.process.kill(); worker.process.wait(timeout=30); worker.close()
                worker = rt.Worker(worker_command, data, output / 'worker-3.log')
                recovered = worker.rpc('status')
                assert not recovered['audio']['jobs'].get('processing')
                assert vectors(data) == saved
                worker.rpc('start', kind='audio')
                report['recoveries'].append(dict(kind='isolated-worker-crash-reopen-resume', saved_segments=len(saved)))
                crash_done = True
            if now - last_print >= 30:
                print(json.dumps(dict(elapsed=round(now-started), jobs=jobs, peak_working_set_bytes=peak_memory)), flush=True)
                last_print = now
            if not status['running']:
                assert pause_done and crash_done and status['audio']['analyzed'] == 3, status
                break
            time.sleep(.25)
        else: raise TimeoutError('Endurance deadline exceeded')
        assert rt.photo_hash(data) == photo_before
        for extension in ['wav', 'mp3', 'flac']:
            found = worker.rpc('search', kind='audio', format=extension, text='電子音', limit=200)
            expected = (args.seconds * 1000 - 30000 + 24999) // 25000 + 1
            assert found['total'] == expected, (extension, found['total'], expected)
            tail = worker.rpc('search', kind='audio', format=extension, offset=expected-1, limit=1)['items'][0]
            preview = worker.rpc('preview', id=tail['id'], segment_id=tail['segment_id'])
            with wave.open(io.BytesIO(base64.b64decode(preview['url'].split(',')[1])), 'rb') as decoded:
                assert decoded.getnframes() > 0 and decoded.getnframes() <= 40 * decoded.getframerate()
            assert abs(tail['end_ms'] - args.seconds*1000) < 100, tail
        after = vectors(data)
        with sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
            max_attempts = db.execute('SELECT MAX(attempts) FROM audio_jobs').fetchone()[0]
            # Each of pause and crash may interrupt one unfinished window.
            retried = db.execute('SELECT COUNT(*) FROM audio_jobs WHERE attempts>1').fetchone()[0]
            assert max_attempts <= 2 and retried <= len(report['recoveries']), (max_attempts, retried)
        cpu_total += cpu_seconds(worker.process) or 0
        report.update(analysis_completed=True, seconds=time.monotonic()-started, jobs=status['audio']['jobs'],
                      peak_worker_working_set_bytes=peak_memory, worker_cpu_seconds=cpu_total,
                      logical_processors=os.cpu_count(), photo_vectors_unchanged=True, integrity='ok',
                      retried_segments=retried,
                      vram_note='nvidia-smi memory is whole-device usage including other apps, not worker-only VRAM.')
        save()
    finally:
        worker.close()
    for path in media.iterdir():
        with path.open('rb') as source: assert hashlib.file_digest(source,'sha256').hexdigest() == source_hashes[path.name]
    report['source_files_unchanged'] = True
    worker = rt.Worker(worker_command, data, output / 'worker-reopened.log')
    try:
        assert vectors(data) == after
        assert worker.rpc('search', kind='audio')['total'] == len(after)
        report['saved_index_reopened'] = True; report['completed'] = True; save()
    finally: worker.close()
    print(json.dumps({k:v for k,v in report.items() if k not in ['resource_samples','source_hashes']}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
