"""Copy migration: leave source intact, verify before pointer activation."""
from contextlib import closing
import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from model_download import file_hash


def migrate(library, destination):
    if library.status()['running'] or (library.download.thread and library.download.thread.is_alive()):
        raise ValueError('解析とモデル取得を停止してから保存先を変更してください')
    source = library.data
    destination = Path(destination).resolve(strict=True) / 'LocalPhotoSearchData'
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError('元の保存領域と重ならないフォルダーを選択してください')
    if destination.exists():
        raise ValueError('移行先にLocalPhotoSearchDataが存在します。空の別フォルダーを選択してください')
    files = []
    # AI runtime cache is disposable and regenerated on first use; copying it
    # makes migration slow without preserving search data.
    for directory in ('models', 'thumbnails'):
        base = source / directory
        if base.is_symlink() or base.is_junction():raise ValueError('保存領域にリンクがあります。移行を中止しました')
        if base.exists():
            for entry in base.rglob('*'):
                if entry.is_symlink() or entry.is_junction():
                    raise ValueError('保存領域にリンクがあります。移行を中止しました')
                if entry.is_file():
                    files.append(entry)
    # A user may select a model anywhere inside the data area, not only models/.
    model = Path(library.config['model_path'])
    if source in model.parents and model.is_file() and model not in files:
        for entry in (model, *model.parents):
            if entry == source:
                break
            if entry.is_symlink() or entry.is_junction():
                raise ValueError('モデル保存領域にリンクがあります。移行を中止しました')
        files.append(model)
    required = sum(p.stat().st_size for p in files) + library.status()['database_bytes'] + 256 * 1024**2
    if shutil.disk_usage(destination.parent).free < required:
        raise ValueError('移行先の空き容量が不足しています。元データは保持されています')
    staging = destination.with_name('LocalPhotoSearchData.copy-' + uuid.uuid4().hex)
    staging.mkdir()
    try:
        with library.ai_lock:
            if library.engine:
                library.engine.close()
                library.engine = None
            with library.db_lock:
                with closing(sqlite3.connect(staging / 'index.sqlite3')) as copied:
                    library.db.backup(copied)
                    if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('コピーしたSQLiteの整合性を確認できませんでした')
                    if copied.execute('PRAGMA foreign_key_check').fetchone():
                        raise ValueError('コピーしたSQLiteの参照整合性に問題があります')
                    config = dict(library.config)
                    model = Path(config['model_path'])
                    if source in model.parents:
                        config['model_path'] = str(destination / model.relative_to(source))
                    copied.execute("INSERT OR REPLACE INTO settings VALUES('config',?)", (json.dumps(config),))
                    copied.commit()
            manifest = {}
            for entry in files:
                relative = entry.relative_to(source)
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, target)
                before = file_hash(entry)
                if file_hash(target) != before:
                    raise ValueError('コピーしたファイルのSHA-256が一致しませんでした')
                manifest[str(relative)] = before
            (staging / 'migration.json').write_text(json.dumps({'verified': True, 'files': manifest}), encoding='utf-8')
            staging.rename(destination)
        return {'data_path': str(destination), 'files_verified': len(files), 'source_preserved': True}
    except Exception:
        # Keep failed staging for diagnosis/recovery; never touch source or a preexisting destination.
        raise ValueError('保存先のコピーに失敗しました。元データは保持されています。空き容量・権限を確認し、別の移行先で再試行してください')
