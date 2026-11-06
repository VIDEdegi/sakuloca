"""Audio indexing shares the owner's locks, lifecycle and SQLite connection."""
from array import array
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

from ai import LiteRtAudioBackend, AUDIO_QUERY_PREFIX, digest
from audio import PREPROCESS, MAX_PREVIEW_MS, PREVIEW_CONTEXT_MS, WindowReader, inspect_wave
from audio_queries import resolve_audio_query
from diagnostics import idle_seconds
from exact_search import ExactDot
from runtime_cache import LIMIT
from user_errors import friendly_error


class AudioLibrary:
    def __init__(self, owner):
        self.owner = owner
        self.engine = None
        self.runtime_path = None
        self.cache_disabled = False
        self.model_meta = None
        self.search_cache = None
        row = owner.db.execute("SELECT value FROM settings WHERE key='active_audio_model'").fetchone()
        self.model_id = row[0] if row else None

    def close_engine(self):
        if self.engine:
            self.engine.close()
            self.engine = None
        self.runtime_path = None

    def reset(self):
        self.close_engine()
        self.model_id = None
        self.model_meta = None
        self.cache_disabled = False
        self.search_cache = None

    def ensure_engine(self):
        # Caller holds the owner's AI lock. Only one modality is resident.
        if self.engine:
            return
        lib = self.owner
        if lib.engine:
            lib.engine.close(); lib.engine = None; lib.runtime_path = None
        model = Path(lib.config['model_path'])
        sha = digest(model)
        generation = hashlib.sha256((sha + PREPROCESS + lib.config['backend']).encode()).hexdigest()
        self.runtime_path = None if self.cache_disabled else lib.runtime_cache.prepare(generation)
        engine = LiteRtAudioBackend(model, lib.config['backend'], self.runtime_path or ':nocache')
        try:
            engine.text('音声')
            meta = dict(sha256=sha, dimensions=engine.dimensions, preprocess=PREPROCESS,
                        normalization='l2', query_prefix=AUDIO_QUERY_PREFIX,
                        runtime='litert-lm-api/0.18.0', backend=lib.config['backend'])
            # Audio CPU/GPU equivalence is not assumed from photo verification.
            model_id = hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()
            with lib.db_lock, lib.db:
                lib.db.execute('INSERT OR IGNORE INTO models VALUES(?,?,?,?,?,?,?,?)', (model_id, *meta.values()))
                lib.db.execute("INSERT OR REPLACE INTO settings VALUES('active_audio_model',?)", (model_id,))
            self.model_id = model_id
            self.model_meta = {'id': model_id, **meta}
            self.engine = engine
        except BaseException:
            engine.close(); self.runtime_path = None
            raise

    def maintain_cache(self):
        lib = self.owner
        total = lib.runtime_cache.prune(keep=self.runtime_path if self.engine else lib.runtime_path)
        if total > LIMIT and self.engine and self.runtime_path:
            path = self.runtime_path
            self.close_engine()
            lib.runtime_cache.clear_generation(path)
            lib.runtime_cache.prune()
            self.cache_disabled = True
            lib.cache_warning = 'AIキャッシュが1 GiBを超えたため、このセッションでは保存せずに処理します。検索データは保持されます。'

    def register(self, file_id):
        # Called from the common scanner under the DB transaction.
        db = self.owner.db
        db.execute("INSERT INTO audio_files(file_id,model_id) VALUES(?,?) ON CONFLICT(file_id,model_id) DO UPDATE SET state=CASE WHEN audio_files.state='error' THEN 'pending' ELSE audio_files.state END,error=NULL", (file_id, self.model_id))
        db.execute("UPDATE audio_jobs SET state='pending',error=NULL WHERE state='error' AND segment_id IN (SELECT id FROM audio_segments WHERE file_id=? AND model_id=?)", (file_id, self.model_id))

    def _prepare(self, file):
        lib = self.owner
        checked = lib.file(file['id'])
        info = inspect_wave(Path(checked['path']))
        lib.file(file['id'])  # Detect source changes during header decoding.
        with lib.db_lock, lib.db:
            lib.db.execute('UPDATE audio_files SET duration_ms=?,sample_rate=?,channels=? WHERE file_id=? AND model_id=?',
                           (info.duration_ms, info.rate, info.channels, file['id'], self.model_id))
            for start, end, start_ms, end_ms in info.windows():
                lib.db.execute('INSERT OR IGNORE INTO audio_segments(file_id,model_id,start_frame,end_frame,start_ms,end_ms) VALUES(?,?,?,?,?,?)',
                               (file['id'], self.model_id, start, end, start_ms, end_ms))
            lib.db.execute("INSERT OR IGNORE INTO audio_jobs(segment_id) SELECT id FROM audio_segments WHERE file_id=? AND model_id=?", (file['id'], self.model_id))
        return info

    def _wait(self):
        lib = self.owner
        while lib.preferences['idle_only'] and idle_seconds() < 120:
            lib.progress['phase'] = 'waiting_idle'
            if lib.stop.wait(1):
                return False
        if lib.stop.is_set() or (lib.preferences['low_load'] and lib.stop.wait(.35)):
            return False
        if shutil.disk_usage(lib.data).free < 64 * 1024**2:
            raise ValueError('保存先の空き容量が64 MiB未満です。容量を確保して再開してください')
        lib.progress['phase'] = 'indexing'
        return True

    def index(self):
        lib = self.owner
        try:
            with lib.ai_lock:
                self.ensure_engine(); self.maintain_cache()
            if lib.stop.is_set():
                return
            lib.progress['phase'] = 'scanning'
            for folder in lib.folders():
                if lib.stop.is_set():
                    return
                if folder['enabled']:
                    lib._scan(folder, kind='audio')
            lib.progress['phase'] = 'indexing'
            last_file = 0
            while not lib.stop.is_set():
                with lib.db_lock:
                    file = lib.db.execute("SELECT f.* FROM files f JOIN audio_files a ON a.file_id=f.id WHERE a.model_id=? AND a.state='pending' AND f.id>? AND f.available=1 AND EXISTS(SELECT 1 FROM folder_files ff JOIN folders d ON d.id=ff.folder_id WHERE ff.file_id=f.id AND d.enabled=1 AND d.status='ready') ORDER BY f.id LIMIT 1", (self.model_id, last_file)).fetchone()
                if file is None:
                    break
                last_file = file['id']
                if not self._wait():
                    break
                try:
                    info = self._prepare(file)
                except Exception as exc:
                    self._file_error(file['id'], exc)
                    continue
                with WindowReader(Path(file['path']), info) as reader:
                    last_segment = 0
                    while not lib.stop.is_set():
                        with lib.db_lock:
                            segment = lib.db.execute("SELECT s.* FROM audio_segments s JOIN audio_jobs j ON j.segment_id=s.id WHERE s.file_id=? AND s.model_id=? AND j.state='pending' AND s.id>? ORDER BY s.id LIMIT 1", (file['id'], self.model_id, last_segment)).fetchone()
                        if segment is None or not self._wait():
                            break
                        last_segment = segment['id']
                        with lib.db_lock, lib.db:
                            lib.db.execute("UPDATE audio_jobs SET state='processing',attempts=attempts+1 WHERE segment_id=?", (segment['id'],))
                        try:
                            lib.file(file['id'])
                            data, silent = reader.read(segment['start_frame'], segment['end_frame'])
                            vector = None
                            if not silent:
                                with lib.ai_lock:
                                    if lib.stop.is_set():
                                        with lib.db_lock, lib.db:
                                            lib.db.execute("UPDATE audio_jobs SET state='pending' WHERE segment_id=?", (segment['id'],))
                                        break
                                    self.ensure_engine()
                                    vector = self.engine.audio(data)
                            lib.file(file['id'])
                            with lib.db_lock, lib.db:
                                lib.invalidate_search()
                                if vector is not None:
                                    lib.db.execute('INSERT OR REPLACE INTO audio_embeddings VALUES(?,?)', (segment['id'], array('f', vector).tobytes()))
                                lib.db.execute("UPDATE audio_jobs SET state=?,error=NULL WHERE segment_id=?", ('skipped' if silent else 'done', segment['id']))
                            lib.progress['processed'] += 1
                        except Exception as exc:
                            traceback.print_exc(file=sys.stderr)
                            reader.close()
                            with lib.db_lock, lib.db:
                                lib.db.execute("UPDATE audio_jobs SET state='error',error=? WHERE segment_id=?", (friendly_error(exc), segment['id']))
                            lib.progress['errors'] += 1
                            lib.progress['last_error'] = friendly_error(exc)
                        with lib.ai_lock:
                            if self.engine:
                                self.maintain_cache()
                with lib.db_lock, lib.db:
                    error = lib.db.execute("SELECT j.error FROM audio_jobs j JOIN audio_segments s ON s.id=j.segment_id WHERE s.file_id=? AND s.model_id=? AND j.state='error' LIMIT 1", (file['id'], self.model_id)).fetchone()
                    pending = lib.db.execute("SELECT 1 FROM audio_jobs j JOIN audio_segments s ON s.id=j.segment_id WHERE s.file_id=? AND s.model_id=? AND j.state IN ('pending','processing') LIMIT 1", (file['id'], self.model_id)).fetchone()
                    state = 'error' if error else 'pending' if pending else 'done'
                    lib.db.execute('UPDATE audio_files SET state=?,error=? WHERE file_id=? AND model_id=?', (state, error[0] if error else None, file['id'], self.model_id))
                    if state == 'done':
                        lib.db.execute('UPDATE files SET last_analyzed=?,error=NULL WHERE id=?', (time.time(), file['id']))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            lib.progress['last_error'] = friendly_error(exc)
            lib.progress['phase'] = 'error'
        finally:
            if not lib.stop.is_set():
                lib._resume_requested(False)
            lib.runtime_cache.size = lib.runtime_cache.usage()
            if lib.progress['phase'] != 'error':
                lib.progress['phase'] = 'paused' if lib.stop.is_set() else 'idle'

    def _file_error(self, file_id, exc):
        lib = self.owner
        traceback.print_exc(file=sys.stderr)
        with lib.db_lock, lib.db:
            lib.db.execute("UPDATE audio_files SET state='error',error=? WHERE file_id=? AND model_id=?", (friendly_error(exc), file_id, self.model_id))
        lib.progress['errors'] += 1
        lib.progress['last_error'] = friendly_error(exc)

    def status(self):
        lib = self.owner
        where, params = lib._where({'kind': 'audio'})
        with lib.db_lock:
            counts = dict(lib.db.execute(f'SELECT j.state,COUNT(*) FROM audio_jobs j JOIN audio_segments s ON s.id=j.segment_id JOIN files f ON f.id=s.file_id WHERE {where} AND s.model_id=? GROUP BY j.state', (*params, self.model_id)).fetchall())
            files = lib.db.execute(f'SELECT COUNT(*) FROM files f WHERE {where}', params).fetchone()[0]
            done = lib.db.execute(f"SELECT COUNT(*) FROM files f JOIN audio_files a ON a.file_id=f.id WHERE {where} AND a.model_id=? AND a.state='done'", (*params, self.model_id)).fetchone()[0]
            errors = self.errors()
            without_segments = lib.db.execute(f"SELECT COUNT(*) FROM files f JOIN audio_files a ON a.file_id=f.id WHERE {where} AND a.model_id=? AND a.state='error' AND NOT EXISTS(SELECT 1 FROM audio_segments s WHERE s.file_id=f.id AND s.model_id=a.model_id)", (*params, self.model_id)).fetchone()[0]
            counts['error'] = counts.get('error', 0) + without_segments
        return {'jobs': counts, 'total': files, 'analyzed': done, 'model': self.model_meta,
                'errors': errors['items'][:10], 'error_files': errors['total']}

    def errors(self, offset=0):
        lib = self.owner
        where, params = lib._where({'kind': 'audio'})
        clause = f"{where} AND a.model_id=? AND a.state='error'"
        with lib.db_lock:
            total = lib.db.execute(f'SELECT COUNT(*) FROM files f JOIN audio_files a ON a.file_id=f.id WHERE {clause}', (*params, self.model_id)).fetchone()[0]
            items = [dict(r) for r in lib.db.execute(f'SELECT f.id,f.path,a.error FROM files f JOIN audio_files a ON a.file_id=f.id WHERE {clause} ORDER BY f.id LIMIT 50 OFFSET ?', (*params, self.model_id, max(0, int(offset))))]
        return {'total': total, 'items': items}

    def search(self, **query):
        lib = self.owner
        if query.get('image'):
            raise ValueError('音声は言葉またはファイル名で検索してください')
        text = query.get('text', '').strip()
        interpreted = resolve_audio_query(text)
        interpretation = {'interpreted_query': interpreted} if interpreted != text else {}
        limit = min(max(int(query.get('limit', 60)), 1), 200)
        offset = max(int(query.get('offset', 0)), 0)
        key = json.dumps({k: v for k, v in query.items() if k not in ('limit', 'offset', 'sort')}, sort_keys=True)
        if text:
            with lib.db_lock:
                saved = self.search_cache
                if saved and saved['key'] == key:
                    offset = lib._clamp_offset(offset, limit, len(saved['ranked']))
                    return {'items': self._page(saved['ranked'], offset, limit), 'total': len(saved['ranked']), 'offset': offset, 'semantic': True, **interpretation}
            with lib.ai_lock:
                self.ensure_engine()
                vector = self.engine.text(interpreted)
                self.maintain_cache()
        where, params = lib._where({**query, 'kind': 'audio'})
        clause = f"{where} AND s.model_id=? AND j.state IN ('done','skipped')"
        joins = 'audio_segments s JOIN files f ON f.id=s.file_id JOIN audio_jobs j ON j.segment_id=s.id'
        with lib.db_lock:
            if text:
                dot = ExactDot(vector)
                ranked = []
                for row in lib.db.execute(f'SELECT s.id,e.vector FROM {joins} JOIN audio_embeddings e ON e.segment_id=s.id WHERE {clause}', (*params, self.model_id)):
                    score = dot(row['vector'])
                    if score is not None:
                        ranked.append((score, -row['id']))
                ranked.sort(reverse=True)
                self.search_cache = {'key': key, 'ranked': ranked}
                total = len(ranked)
                offset = lib._clamp_offset(offset, limit, total)
                items = self._page(ranked, offset, limit)
            else:
                orders = {'date_desc': 'f.mtime_ns DESC,f.id', 'date_asc': 'f.mtime_ns,f.id',
                          'name_asc': 'f.name COLLATE NOCASE,f.id', 'name_desc': 'f.name COLLATE NOCASE DESC,f.id',
                          'size_desc': 'f.size DESC,f.id', 'size_asc': 'f.size,f.id'}
                order = orders.get(query.get('sort'), orders['date_desc'])
                total = lib.db.execute(f'SELECT COUNT(*) FROM {joins} WHERE {clause}', (*params, self.model_id)).fetchone()[0]
                offset = lib._clamp_offset(offset, limit, total)
                ids = [(None, -r[0]) for r in lib.db.execute(f'SELECT s.id FROM {joins} WHERE {clause} ORDER BY {order},s.start_frame LIMIT ? OFFSET ?', (*params, self.model_id, limit, offset))]
                items = self._page(ids, 0, limit)
        return {'items': items, 'total': total, 'offset': offset, 'semantic': bool(text), **interpretation}

    def _page(self, ranked, offset, limit):
        page = ranked[offset:offset + limit]
        if not page:
            return []
        ids = [-negative for _, negative in page]
        placeholders = ','.join('?' for _ in ids)
        rows = self.owner.db.execute(f"SELECT f.*,s.id AS segment_id,s.start_ms,s.end_ms,a.duration_ms,j.state='skipped' AS silent FROM audio_segments s JOIN files f ON f.id=s.file_id JOIN audio_files a ON a.file_id=s.file_id AND a.model_id=s.model_id JOIN audio_jobs j ON j.segment_id=s.id WHERE s.id IN ({placeholders})", ids)
        metadata = {row['segment_id']: dict(row) for row in rows}
        return [{**metadata[-negative], **({'score': score} if score is not None else {})} for score, negative in page]

    def preview(self, id, segment_id):
        lib = self.owner
        row = lib.file(id)
        if row['kind'] != 'audio':
            raise ValueError('音声ファイルを選択してください')
        with lib.db_lock:
            segment = lib.db.execute('SELECT s.*,a.duration_ms FROM audio_segments s JOIN audio_files a ON a.file_id=s.file_id AND a.model_id=s.model_id WHERE s.id=? AND s.file_id=? AND s.model_id=?', (segment_id, id, self.model_id)).fetchone()
            neighbors = {}
            if segment is not None:
                for direction, operator, order in [('previous', '<', 'DESC'), ('next', '>', 'ASC')]:
                    adjacent = lib.db.execute(f'SELECT id FROM audio_segments WHERE file_id=? AND model_id=? AND start_frame{operator}? ORDER BY start_frame {order} LIMIT 1', (id, self.model_id, segment['start_frame'])).fetchone()
                    neighbors[direction + '_segment_id'] = adjacent[0] if adjacent else None
        if segment is None:
            raise ValueError('音声区間が見つかりません。検索を更新してください')
        with WindowReader(Path(row['path'])) as reader:
            info = reader.info
            padding = info.rate * PREVIEW_CONTEXT_MS // 1000
            start = max(0, segment['start_frame'] - padding)
            end = min(info.frames, segment['end_frame'] + padding)
            data, silent = reader.read(start, end, max_ms=MAX_PREVIEW_MS)
        lib.file(id)
        return {'url': 'data:audio/wav;base64,' + base64.b64encode(data).decode('ascii'),
                'file': {**row, 'segment_id': segment_id, 'start_ms': start * 1000 // info.rate,
                         'end_ms': (end * 1000 + info.rate - 1) // info.rate,
                         'match_start_ms': segment['start_ms'], 'match_end_ms': segment['end_ms'],
                         'duration_ms': segment['duration_ms'], 'silent': silent, **neighbors}}
