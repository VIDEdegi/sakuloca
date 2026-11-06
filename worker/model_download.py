"""Opt-in, pinned official model acquisition. Never sends photos or queries."""
import hashlib
import os
import re
import shutil
import threading
import urllib.request
from pathlib import Path

MODEL = {
    'name': 'EmbeddingGemma 2 (740M)',
    'filename': 'embeddinggemma-2-740m.litertlm',
    'bytes': 484622336,
    'sha256': 'e7a8a2204b91e0f96e92960e84a09a89212e1633dcb7575a9bf3378b4df77f4c',
    'revision': '24d962e906c7d332c6428e71c9676855024569e2',
    'source': 'https://huggingface.co/litert-community/embeddinggemma-2-740m-litert-lm',
    'license': 'Apache-2.0',
    'license_url': 'https://huggingface.co/google/embeddinggemma-2',
}
MODEL['url'] = f"{MODEL['source']}/resolve/{MODEL['revision']}/{MODEL['filename']}"


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class ModelDownload:
    def __init__(self):
        self.thread = None
        self.stop = threading.Event()
        self.state = {'phase': 'idle', 'received': 0, 'total': MODEL['bytes'], 'path': '', 'error': None}

    def info(self):
        return {**MODEL, 'required_free': MODEL['bytes'] + 1024**3, 'download': dict(self.state)}

    def start(self, directory, consent=False):
        if consent is not True:
            raise ValueError('配布元・ライセンス・保存先・必要容量を確認して、ダウンロードに同意してください')
        if self.thread and self.thread.is_alive():
            raise ValueError('モデルの取得はすでに実行中です')
        folder = Path(directory).resolve(strict=True)
        if not folder.is_dir():
            raise ValueError('モデルの保存先フォルダーを選択してください')
        destination = folder / MODEL['filename']
        partial = destination.with_suffix('.litertlm.part')
        if destination.is_symlink() or partial.is_symlink():
            raise ValueError('リンク先にはモデルを保存できません')
        if destination.exists():
            if destination.stat().st_size == MODEL['bytes'] and file_hash(destination) == MODEL['sha256']:
                self.state.update(phase='complete', received=MODEL['bytes'], path=str(destination), error=None)
                return self.info()
            raise ValueError('同名の異なるファイルが存在します。別の保存先を選択してください。上書きはしません')
        received = partial.stat().st_size if partial.exists() else 0
        if received > MODEL['bytes']:
            raise ValueError('途中ファイルのサイズが不正です。別の保存先を選択してください')
        if shutil.disk_usage(folder).free < MODEL['bytes'] - received + 1024**3:
            raise ValueError('モデル保存先の空き容量が不足しています。モデル残量と1 GiBの余裕が必要です')
        self.stop.clear()
        self.state.update(phase='downloading', received=received, path=str(destination), error=None)
        self.thread = threading.Thread(target=self._run, args=(destination, partial), daemon=True, name='model-download')
        self.thread.start()
        return self.info()

    def cancel(self):
        self.stop.set()
        return self.info()

    def _run(self, destination, partial):
        try:
            offset = self.state['received']
            if offset < MODEL['bytes']:
                headers = {'User-Agent': 'SakuLoca/1.0', 'Accept-Encoding': 'identity'}
                if offset:
                    headers['Range'] = f'bytes={offset}-'
                request = urllib.request.Request(MODEL['url'], headers=headers)
                with urllib.request.urlopen(request, timeout=20) as response:
                    if not response.url.startswith('https://'):
                        raise ValueError('安全なHTTPS配布元から取得できません')
                    if offset:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if response.status != 206 or not match or int(match[1]) != offset or int(match[3]) != MODEL['bytes']:
                            raise ValueError('配布元が途中再開を受け付けません。別の保存先で再試行してください')
                    elif response.status != 200:
                        raise ValueError('配布元の応答が不正です')
                    with partial.open('ab' if partial.exists() else 'xb') as stream:
                        while not self.stop.is_set():
                            block = response.read(min(1024 * 1024, MODEL['bytes'] - self.state['received'] + 1))
                            if not block:
                                break
                            if self.state['received'] + len(block) > MODEL['bytes']:
                                raise ValueError('配布ファイルのサイズが予定と異なります')
                            stream.write(block)
                            self.state['received'] += len(block)
                        stream.flush()
                        os.fsync(stream.fileno())
            if self.stop.is_set():
                self.state['phase'] = 'paused'
                return
            self.state['phase'] = 'verifying'
            if partial.stat().st_size != MODEL['bytes']:
                raise ValueError('取得が途中で終了しました。再試行すると途中から取得します')
            if file_hash(partial) != MODEL['sha256']:
                self.state['phase'] = 'invalid'
                raise ValueError('SHA-256が公式情報と一致しません。別の保存先で再取得してください。途中ファイルは使用しません')
            # Windows rename fails when a destination already exists; never replace.
            partial.rename(destination)
            self.state['phase'] = 'complete'
        except Exception as exc:
            if self.state['phase'] != 'invalid':
                self.state['phase'] = 'error'
            self.state['error'] = str(exc) if isinstance(exc, ValueError) else 'モデルを取得できません。インターネット接続・保存先の権限・空き容量を確認し、再試行してください。オフラインでは既存モデルを選択できます'
