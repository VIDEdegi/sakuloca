"""Real JSON-lines Worker + native embeddings, using generated media only."""
import argparse
from array import array
import base64
from contextlib import closing
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import math
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import wave
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def wav(path, seconds=1, silent=False, rate=16000, channels=1):
    pcm = b''.join(struct.pack('<h', 0 if silent else round(5000 * math.sin(2 * math.pi * 440 * n / rate))) * channels for n in range(rate))
    with wave.open(str(path), 'wb') as target:
        target.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        for _ in range(seconds): target.writeframesraw(pcm)


def photo_hash(data):
    with closing(sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute('SELECT file_id,model_id,vector FROM embeddings ORDER BY file_id,model_id').fetchall()
        return hashlib.sha256(b''.join(str(i).encode() + model.encode() + vector for i, model, vector in rows)).hexdigest()


def peak_rss(process):
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [(key, ctypes.c_size_t) for key in ['peak_rss', 'rss', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private']]
    memory = Counters(); memory.cb = ctypes.sizeof(memory)
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
    if ctypes.windll.psapi.GetProcessMemoryInfo(wintypes.HANDLE(int(process._handle)), ctypes.byref(memory), memory.cb):
        return memory.peak_rss


class Worker:
    def __init__(self, command, data, log):
        self.log = log.open('w', encoding='utf-8')
        self.process = subprocess.Popen([*command, '--data', str(data)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.log, text=True, encoding='utf-8',
                                        env=dict(os.environ, MEDIA_SEARCH_DATA=str(data), PYTHONIOENCODING='utf-8'),
                                        creationflags=subprocess.CREATE_NO_WINDOW)
        self.replies = queue.Queue(); self.counter = 0
        def reader():
            for line in self.process.stdout: self.replies.put(line)
            self.replies.put(None)
        threading.Thread(target=reader, daemon=True).start()

    def rpc(self, method, **params):
        self.counter += 1
        self.process.stdin.write(json.dumps(dict(version=1, id=self.counter, method=method, params=params), ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        line = self.replies.get(timeout=180)
        assert line is not None, f'Worker exited: {self.process.poll()}'
        reply = json.loads(line)
        assert reply['id'] == self.counter, reply
        assert 'error' not in reply, reply
        return reply['result']

    def wait(self):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = self.rpc('status')
            if not status['running']:
                assert status['progress']['phase'] != 'error', status
                return status
            time.sleep(.1)
        raise TimeoutError('Indexing did not complete')

    def close(self):
        if self.process.poll() is None:
            try:
                self.rpc('shutdown'); self.process.wait(timeout=30)
            finally:
                if self.process.poll() is None: self.process.kill(); self.process.wait()
        self.process.stdin.close(); self.process.stdout.close(); self.log.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=['cpu', 'gpu'], default='gpu')
    parser.add_argument('--packed', action='store_true')
    parser.add_argument('--formats', action='store_true', help='Also exercise generated MP3/FLAC (developer ffmpeg required)')
    args = parser.parse_args()
    output = args.output.resolve()
    assert (ROOT / 'data/release-tests').resolve() in output.parents
    output.mkdir(parents=True, exist_ok=False)
    media = output / 'artificial'; media.mkdir()
    wav(media / 'tone.wav', 60)
    wav(media / 'silent.wav', silent=True)
    wav(media / 'stereo.wav', rate=48000, channels=2)
    if args.formats:
        for extension, codec in [('mp3', 'libmp3lame'), ('flac', 'flac')]:
            subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(media / 'tone.wav'),
                            '-c:a', codec, str(media / ('tone.' + extension))], check=True, timeout=60)
    (media / 'broken.wav').write_bytes(b'artificial-invalid-wav')
    Image.new('RGB', (128, 96), 'red').save(media / 'red-square.jpg')
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in media.iterdir()}
    data = output / 'index'
    command = [str(ROOT / 'dist-worker/media-search-worker/media-search-worker.exe')] if args.packed else [sys.executable, str(ROOT / 'worker/main.py')]
    report = {'backend': args.backend, 'packed': args.packed, 'artificial_media_only': True}
    model = ROOT / 'data/models/embeddinggemma-2-740m.litertlm'
    with model.open('rb') as source: model_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    worker = Worker(command, data, output / 'worker.log')
    started = time.monotonic()
    try:
        worker.rpc('configure', model_path=str(model), backend=args.backend)
        worker.rpc('preferences', setup_complete=True)
        worker.rpc('add_folder', path=str(media))
        worker.rpc('start', kind='image'); before = worker.wait()
        assert before['jobs']['done'] == 1, before
        saved_photo = photo_hash(data)
        worker.rpc('start', kind='audio')
        # Request a real pause while a window is being processed.
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            status = worker.rpc('status')
            if status['audio']['jobs'].get('processing') or not status['running']: break
            time.sleep(.03)
        worker.rpc('pause'); paused = worker.wait()
        report['pause_phase'] = paused['progress']['phase']
        worker.rpc('start', kind='audio'); indexed = worker.wait()
        expected_done = 10 if args.formats else 4
        assert indexed['audio']['jobs']['done'] == expected_done, indexed
        assert indexed['audio']['jobs']['skipped'] == 1, indexed
        assert indexed['audio']['error_files'] == 1, indexed
        assert photo_hash(data) == saved_photo
        result = worker.rpc('search', kind='audio', text='電子音', limit=2)
        assert result['total'] == expected_done and len(result['items']) == 2, result
        assert all(item['kind'] == 'audio' and item['segment_id'] for item in result['items'])
        assert all(math.isfinite(item['score']) for item in result['items'])
        last = worker.rpc('search', kind='audio', text='電子音', limit=2, offset=999)
        assert last['offset'] == expected_done - 2 and len(last['items']) == 2
        rows = worker.rpc('search', kind='audio', filename='tone', format='wav', sort='name_asc')['items']
        assert [item['start_ms'] for item in rows] == [0, 25000, 50000], rows
        preview = worker.rpc('preview', id=rows[1]['id'], segment_id=rows[1]['segment_id'])
        with wave.open(io.BytesIO(base64.b64decode(preview['url'].split(',')[1])), 'rb') as audio:
            assert (audio.getnframes(), audio.getframerate()) == (40 * 16000, 16000)
        assert len(worker.rpc('search', text='赤い四角')['items']) == 1
        assert worker.rpc('errors', kind='audio')['total'] == 1
        assert worker.rpc('errors')['total'] == 0
        worker.rpc('start', kind='image'); worker.wait()
        assert photo_hash(data) == saved_photo
        assert worker.rpc('search', kind='audio', filename='tone', format='wav')['total'] == 3
        if args.formats:
            for extension in ['mp3', 'flac']:
                compressed = worker.rpc('search', kind='audio', format=extension, sort='name_asc')['items']
                assert len(compressed) == 3 and compressed[1]['start_ms'] == 25000
                decoded = worker.rpc('preview', id=compressed[1]['id'], segment_id=compressed[1]['segment_id'])
                with wave.open(io.BytesIO(base64.b64decode(decoded['url'].split(',')[1])), 'rb') as audio:
                    assert audio.getnframes() == 40 * audio.getframerate()
        with closing(sqlite3.connect((data / 'index.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not db.execute('PRAGMA foreign_key_check').fetchall()
            vectors = [array('f', row[0]) for row in db.execute('SELECT vector FROM audio_embeddings')]
            assert len(vectors) == expected_done and all(len(v) == 768 and abs(sum(x*x for x in v) - 1) < 1e-5 for v in vectors)
        report.update(audio_jobs=indexed['audio']['jobs'], photo_vector_hash=saved_photo,
                      audio_dimensions=768, query_scores=[dict(name=item['name'], start_ms=item['start_ms'], score=item['score']) for item in result['items']],
                      peak_worker_working_set_bytes=peak_rss(worker.process) if args.packed else None, interval_preview=True,
                      photo_vectors_unchanged=True, integrity='ok', semantic_quality_evaluated=False)
    finally:
        worker.close()
    # Reopen the saved library without reanalysis or an eager model load.
    worker = Worker(command, data, output / 'reopened-worker.log')
    try:
        assert worker.rpc('search', kind='audio', filename='tone', format='wav')['total'] == 3
        assert worker.rpc('status')['model'] is None
        assert worker.rpc('status')['audio']['model'] is None
        report['saved_index_reopened'] = True
    finally:
        worker.close()
    assert source_hashes == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in media.iterdir()}
    with model.open('rb') as source: assert hashlib.file_digest(source, 'sha256').hexdigest() == model_hash
    report.update(source_files_unchanged=True, model_unchanged=True, seconds=time.monotonic() - started, verified=True)
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
