"""Isolated audio migration, cancellation and mixed-library regressions."""
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
import audio_library
import database
import library
from audio import WaveInfo, WindowReader, inspect_wave, read_window
from audio_queries import resolve_audio_query
from PIL import Image

TEST_ROOT = ROOT / 'data/release-tests/audio-unit'
TEST_ROOT.mkdir(parents=True, exist_ok=True)


def wav(path, seconds=1, silent=False, rate=8000, channels=1):
    # Repeated one-second PCM pattern avoids large intermediate Python lists.
    pcm = b''.join(struct.pack('<h', 0 if silent else round(3000 * math.sin(2 * math.pi * 440 * n / rate))) * channels for n in range(rate))
    with wave.open(str(path), 'wb') as output:
        output.setparams((channels, 2, rate, 0, 'NONE', 'not compressed'))
        for _ in range(seconds):
            output.writeframesraw(pcm)


class FakePhoto:
    def __init__(self, model, backend, cache):
        self.dimensions = 4
        self.closed = False
        self.cache = cache
    def text(self, text): return [1., 0., 0., 0.]
    def image(self, data): return self.text('')
    def close(self): self.closed = True


class FakeAudio(FakePhoto):
    def text(self, text): return [0., 1., 0., 0.]
    def audio(self, data): return self.text('')


class WaveWindows(unittest.TestCase):
    def test_sound_synonyms_preserve_free_speech_and_negation(self):
        self.assertEqual(resolve_audio_query('猫の鳴き声'), 'A cat meowing')
        self.assertEqual(resolve_audio_query('キーボードを打つ音'), 'Typing on a keyboard')
        self.assertEqual(resolve_audio_query('  ピアノの演奏。  '), 'Piano music')
        for query in ['猫について話している声', '猫の声ではない', '猫の音楽', '雨の演奏', '雨の日に傘を持つように言う', '猫と犬', 'A person talking about a cat']:
            self.assertEqual(resolve_audio_query(query), query)
    def test_boundaries_overlap_and_no_redundant_tail(self):
        for frames, expected in [(1, [(0, 1)]), (30000, [(0, 30000)]),
                                 (30001, [(0, 30000), (25000, 30001)]),
                                 (55000, [(0, 30000), (25000, 55000)]),
                                 (60000, [(0, 30000), (25000, 55000), (50000, 60000)])]:
            info = WaveInfo(frames, 1000, 1, 2)
            self.assertEqual([(a, b) for a, b, _, _ in info.windows()], expected)

    def test_window_bytes_and_truncation(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            path = Path(directory) / 'stereo.wav'
            wav(path, 2, rate=48000, channels=2)
            info = inspect_wave(path)
            data, silent = read_window(path, 48000, 96000)
            self.assertFalse(silent)
            with wave.open(io.BytesIO(data), 'rb') as decoded:
                self.assertEqual((decoded.getnframes(), decoded.getframerate(), decoded.getnchannels()), (48000, 48000, 2))
            self.assertEqual(info.duration_ms, 2000)
            with self.assertRaises(ValueError): read_window(path, -1, 100)
            path.write_bytes(path.read_bytes()[:-200])
            with self.assertRaisesRegex(ValueError, '途中'): read_window(path, 48000, 96000)


class CompressedWindows(unittest.TestCase):
    def test_formats_unicode_overlap_seek_tail_and_close(self):
        import miniaudio
        fixtures = json.loads((ROOT / 'scripts/fixtures/artificial-audio.json').read_text(encoding='utf-8'))['files']
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            for name, fixture in fixtures.items():
                path = Path(directory) / ('日本語の音声-' + name.upper())
                encoded = base64.b64decode(fixture['base64'])
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), fixture['sha256'])
                path.write_bytes(encoded)
                info = inspect_wave(path)
                self.assertEqual((info.rate, info.channels), (8000, 1))
                with WindowReader(path, info) as reader:
                    full, silent = reader.read(0, info.frames)
                    with wave.open(io.BytesIO(full), 'rb') as decoded:
                        reference = decoded.readframes(info.frames)
                    self.assertEqual(silent, name.startswith('silent'))
                    for start, end in [(0, 8000), (4000, 12000), (10000, info.frames), (info.frames - 1, info.frames)]:
                        data, _ = reader.read(start, end)
                        with wave.open(io.BytesIO(data), 'rb') as decoded:
                            self.assertEqual(decoded.readframes(end - start), reference[start * 2:end * 2])
                        self.assertLessEqual(len(reader.buffer), (info.rate * 30 + 4096) * 2)
                self.assertIsNone(reader.stream)
                self.assertEqual(reader.buffer, b'')
                self.assertEqual(path.read_bytes(), encoded)
                # No leaked Windows handle after early generator closure.
                path.rename(path.with_name(path.name + '.finished'))

    def test_invalid_compressed_files_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            for extension in ['mp3', 'flac']:
                path = Path(directory) / ('broken.' + extension)
                path.write_bytes(b'not audio')
                with self.assertRaises(ValueError): inspect_wave(path)


class Migration(unittest.TestCase):
    def test_v1_photo_bytes_backup_and_recovery(self):
        with tempfile.TemporaryDirectory(dir=TEST_ROOT) as directory:
            path = Path(directory) / 'index.sqlite3'
            db = sqlite3.connect(path)
            db.executescript(database.SCHEMA + 'PRAGMA user_version=1;')
            vector = struct.pack('<4f', 1., 0., 0., 0.)
            db.execute("INSERT INTO models VALUES('photo','sha',4,'photo','l2','query','runtime','gpu')")
            db.execute("INSERT INTO files VALUES(1,'fake-photo','photo',10,1,'identity','image','jpg',1,NULL,NULL,NULL)")
            db.execute("INSERT INTO embeddings VALUES(1,'photo',?)", (vector,))
            db.execute("INSERT INTO jobs VALUES(1,'photo','done',NULL,1)")
            db.commit(); db.close()
            migrated = database.connect(path)
            self.assertEqual(migrated.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(migrated.execute('SELECT vector FROM embeddings').fetchone()[0], vector)
            self.assertEqual(migrated.execute('SELECT state FROM jobs').fetchone()[0], 'done')
            self.assertEqual(migrated.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            migrated.close()
            backups = list(path.parent.glob('*.bak'))
            self.assertEqual(len(backups), 1)
            old = sqlite3.connect(backups[0])
            self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(old.execute('SELECT vector FROM embeddings').fetchone()[0], vector)
            old.close()
            # A failed migration rolls back; its backup remains readable.
            failed = Path(directory) / 'failed.sqlite3'
            source = sqlite3.connect(backups[0]); target = sqlite3.connect(failed)
            source.backup(target); source.close(); target.close()
            with patch.object(database, 'AUDIO_SCHEMA', 'CREATE TABLE transient_test(id); INVALID SQL;'):
                with self.assertRaises(sqlite3.Error): database.connect(failed)
            reopened = sqlite3.connect(failed)
            self.assertEqual(reopened.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertIsNone(reopened.execute("SELECT name FROM sqlite_master WHERE name='transient_test'").fetchone())
            reopened.close()


class AudioLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_ROOT)
        self.path = Path(self.temp.name)
        self.photo_patch = patch.object(library, 'LiteRtBackend', FakePhoto); self.photo_patch.start()
        self.audio_patch = patch.object(audio_library, 'LiteRtAudioBackend', FakeAudio); self.audio_patch.start()
        self.trace_patch = patch.object(audio_library.traceback, 'print_exc'); self.trace_patch.start()
        self.model = self.path / 'model.litertlm'; self.model.write_bytes(b'LITERTLM-test')
        self.lib = library.Library(self.path / 'index'); self.lib.configure(str(self.model), 'gpu')
        self.media = self.path / 'media'; self.media.mkdir()
        Image.new('RGB', (16, 16), 'red').save(self.media / 'photo.jpg')
        wav(self.media / 'tone.wav', 60)
        wav(self.media / 'silence.wav', 1, silent=True)
        (self.media / 'broken.wav').write_bytes(b'artificial-broken-wav')
        self.lib.add_folder(str(self.media))

    def tearDown(self):
        self.lib.close(); self.trace_patch.stop(); self.audio_patch.stop(); self.photo_patch.stop(); self.temp.cleanup()

    def finish(self, kind):
        self.lib.start(kind=kind); self.lib.thread.join(timeout=10)
        self.assertFalse(self.lib.thread.is_alive())
        self.assertNotEqual(self.lib.progress['phase'], 'error', self.lib.progress)

    def test_mixed_scan_separation_silence_preview_and_idempotency(self):
        self.finish('image')
        original = self.lib.db.execute('SELECT vector FROM embeddings').fetchone()[0]
        self.finish('audio')
        self.assertEqual(self.lib.status()['total'], 1)
        self.assertEqual(self.lib.status()['audio']['jobs']['done'], 3)
        self.assertEqual(self.lib.status()['audio']['jobs']['skipped'], 1)
        self.assertEqual(self.lib.errors(kind='audio')['total'], 1)
        self.assertEqual(self.lib.errors()['total'], 0)
        self.assertEqual(len(self.lib.search(text='写真')['items']), 1)
        result = self.lib.search(kind='audio', text='電子音', limit=2)
        self.assertEqual(result['total'], 3)
        self.assertTrue(all(item['kind'] == 'audio' for item in result['items']))
        self.assertEqual([item['start_ms'] for item in result['items']], [0, 25000])
        last = self.lib.search(kind='audio', text='電子音', limit=2, offset=999)
        self.assertEqual((last['offset'], len(last['items'])), (2, 1))
        self.assertEqual(self.lib.search(kind='audio', filename='tone')['total'], 3)
        self.assertEqual(self.lib.search(kind='audio', filename='silence')['total'], 1)
        item = result['items'][1]
        preview = self.lib.preview(item['id'], segment_id=item['segment_id'])
        previous = self.lib.preview(item['id'], segment_id=preview['file']['previous_segment_id'])
        following = self.lib.preview(item['id'], segment_id=preview['file']['next_segment_id'])
        self.assertEqual(previous['file']['match_start_ms'], 0)
        self.assertIsNone(previous['file']['previous_segment_id'])
        self.assertEqual(following['file']['match_start_ms'], 50000)
        self.assertIsNone(following['file']['next_segment_id'])
        with wave.open(io.BytesIO(base64.b64decode(preview['url'].split(',')[1])), 'rb') as decoded:
            self.assertEqual(decoded.getnframes(), 40 * 8000)
        self.assertEqual((preview['file']['start_ms'],preview['file']['end_ms']), (20000,60000))
        self.assertEqual((preview['file']['match_start_ms'],preview['file']['match_end_ms']), (25000,55000))
        with self.assertRaises(ValueError): self.lib.preview(item['id'], segment_id=999999)
        saved = list(self.lib.db.execute('SELECT * FROM audio_embeddings ORDER BY segment_id'))
        self.finish('image'); self.finish('audio')
        self.assertEqual(self.lib.db.execute('SELECT vector FROM embeddings').fetchone()[0], original)
        self.assertEqual([tuple(r) for r in self.lib.db.execute('SELECT * FROM audio_embeddings ORDER BY segment_id')], [tuple(r) for r in saved])
        self.assertEqual(self.lib.folders()[0]['count'], 1)
        self.assertEqual(self.lib.folders()[0]['audio_count'], 3)
        self.assertEqual(self.lib.folders()[0]['audio_analyzed'], 2)
        self.assertEqual(self.lib.db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_pause_restart_and_processing_recovery(self):
        entered = threading.Event(); release = threading.Event()
        original = FakeAudio.audio
        def blocked(engine, data):
            entered.set(); release.wait(5); return original(engine, data)
        with patch.object(FakeAudio, 'audio', blocked):
            self.lib.start(kind='audio'); self.assertTrue(entered.wait(5))
            self.lib.pause(); release.set(); self.lib.thread.join(timeout=10)
        saved = self.lib.db.execute('SELECT vector FROM audio_embeddings').fetchone()[0]
        self.assertTrue(self.lib.resume_requested() is False)
        self.assertEqual(self.lib.progress['phase'], 'paused')
        with self.lib.db:
            self.lib.db.execute("UPDATE audio_jobs SET state='processing' WHERE state='pending'")
        self.lib.close(); self.lib = library.Library(self.path / 'index')
        self.assertEqual(self.lib.analysis_kind, 'audio')
        self.assertEqual(self.lib.db.execute("SELECT COUNT(*) FROM audio_jobs WHERE state='processing'").fetchone()[0], 0)
        self.finish('audio')
        self.assertEqual(self.lib.db.execute('SELECT vector FROM audio_embeddings ORDER BY segment_id LIMIT 1').fetchone()[0], saved)
        self.assertEqual(self.lib.status()['audio']['jobs']['done'], 3)

    def test_changes_retry_delete_and_disabled_folder(self):
        self.finish('audio')
        first = self.lib.search(kind='audio', text='電子音')['items'][0]
        wav(self.media / 'tone.wav', 1)
        with self.assertRaisesRegex(ValueError, '変更'): self.lib.preview(first['id'], segment_id=first['segment_id'])
        wav(self.media / 'broken.wav', 1)
        self.lib.retry_errors(kind='audio'); self.lib.thread.join(timeout=10)
        self.assertEqual(self.lib.errors(kind='audio')['total'], 0)
        self.assertEqual(self.lib.search(kind='audio', text='電子音')['total'], 2)
        folder = self.lib.folders()[0]
        self.lib.folder_action(folder['id'], 'enable', False)
        self.assertEqual(self.lib.search(kind='audio', text='電子音')['total'], 0)
        self.lib.folder_action(folder['id'], 'enable', True)
        self.assertEqual(self.lib.search(kind='audio', text='電子音')['total'], 2)
        (self.media / 'tone.wav').unlink(); self.finish('audio')
        self.assertEqual(self.lib.search(kind='audio', text='電子音')['total'], 1)
        self.lib.folder_action(folder['id'], 'remove')
        self.assertEqual(self.lib.db.execute('SELECT COUNT(*) FROM audio_segments').fetchone()[0], 0)

    def test_changed_during_inference_never_commits(self):
        def changed(engine, data):
            (self.media / 'tone.wav').write_bytes(b'changed-source-fixture')
            return [0., 1., 0., 0.]
        with patch.object(FakeAudio, 'audio', changed): self.finish('audio')
        self.assertEqual(self.lib.db.execute('SELECT COUNT(*) FROM audio_embeddings').fetchone()[0], 0)
        self.assertEqual(self.lib.search(kind='audio', text='電子音')['total'], 0)

    def test_audio_profile_backend_is_separate_from_photo(self):
        self.finish('image'); photo = self.lib.model_id
        self.finish('audio'); audio_gpu = self.lib.audio.model_id
        self.lib.configure(str(self.model), 'cpu'); self.finish('audio')
        self.assertNotEqual(audio_gpu, self.lib.audio.model_id)
        self.finish('image'); self.assertEqual(self.lib.model_id, photo)
        self.assertNotEqual(self.lib.model_id, self.lib.audio.model_id)

    def test_audio_cache_closes_before_eviction_and_keeps_profile(self):
        with self.lib.ai_lock: self.lib.audio.ensure_engine()
        audio = self.lib.audio; original = audio.engine; profile = audio.model_id
        path = audio.runtime_path
        mapped = path / 'fake_mldrift_weight_cache.bin'; mapped.write_bytes(b'x' * 2048)
        unknown = path / 'keep-user-note.txt'; unknown.write_text('preserve')
        clear = self.lib.runtime_cache.clear_generation
        def checked_clear(generation):
            self.assertTrue(original.closed)
            return clear(generation)
        with patch.object(audio_library, 'LIMIT', 1024), patch.object(self.lib.runtime_cache, 'clear_generation', checked_clear):
            with self.lib.ai_lock: audio.maintain_cache()
        self.assertFalse(mapped.exists()); self.assertTrue(unknown.exists())
        with self.lib.ai_lock: audio.ensure_engine()
        self.assertEqual(audio.model_id, profile)
        self.assertEqual(audio.engine.cache, ':nocache')

    def test_replaced_source_identity_is_rejected_even_with_same_size_time(self):
        self.finish('audio')
        item = self.lib.search(kind='audio', filename='tone')['items'][0]
        path = self.media / 'tone.wav'; before = path.stat()
        replacement = self.media / 'replacement.tmp'; replacement.write_bytes(path.read_bytes())
        os.replace(replacement, path); os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, '変更'):
            self.lib.preview(item['id'], segment_id=item['segment_id'])


if __name__ == '__main__': unittest.main()
