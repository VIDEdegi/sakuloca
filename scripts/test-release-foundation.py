"""Isolated safety checks. Fake network/AI only where explicitly labelled."""
from contextlib import closing
import hashlib
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'worker'))
from library import Library
import model_download
from storage import migrate


class Response(io.BytesIO):
    def __init__(self,data,offset=0,status=200):
        super().__init__(data[offset:]);self.status=status;self.url='https://huggingface.co/test'
        self.headers={'Content-Range':f'bytes {offset}-{len(data)-1}/{len(data)}'}


class ReleaseSafety(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=root/'tmp');self.path=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def model_metadata(self):
        data=b'LITERTLM'+b'isolated test model'*32
        return data,patch.dict(model_download.MODEL,bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
    def test_download_requires_consent_and_never_overwrites(self):
        manager=model_download.ModelDownload()
        with self.assertRaises(ValueError):manager.start(str(self.path))
        target=self.path/model_download.MODEL['filename'];target.write_bytes(b'keep')
        with self.assertRaises(ValueError):manager.start(str(self.path),True)
        self.assertEqual(target.read_bytes(),b'keep')
    def test_resume_range_sha_and_existing_reuse(self):
        data,metadata=self.model_metadata()
        partial=self.path/(model_download.MODEL['filename']+'.part');partial.write_bytes(data[:17])
        with metadata,patch('model_download.urllib.request.urlopen',return_value=Response(data,17,206)) as network:
            manager=model_download.ModelDownload();manager.start(str(self.path),True);manager.thread.join(5)
            self.assertEqual(manager.state['phase'],'complete');self.assertEqual(network.call_args[0][0].get_header('Range'),'bytes=17-')
            self.assertEqual((self.path/model_download.MODEL['filename']).read_bytes(),data)
            manager.start(str(self.path),True);self.assertEqual(network.call_count,1)
    def test_corrupt_response_never_promoted(self):
        data,metadata=self.model_metadata()
        with metadata,patch('model_download.urllib.request.urlopen',return_value=Response(b'X'*len(data))):
            manager=model_download.ModelDownload();manager.start(str(self.path),True);manager.thread.join(5)
            self.assertEqual(manager.state['phase'],'invalid');self.assertFalse((self.path/model_download.MODEL['filename']).exists())
    def test_offline_preserves_partial_for_retry(self):
        data,metadata=self.model_metadata()
        partial=self.path/(model_download.MODEL['filename']+'.part');partial.write_bytes(data[:17])
        with metadata,patch('model_download.urllib.request.urlopen',side_effect=OSError('offline')):
            manager=model_download.ModelDownload();manager.start(str(self.path),True);manager.thread.join(5)
            self.assertEqual(manager.state['phase'],'error');self.assertEqual(partial.read_bytes(),data[:17])
    def test_server_ignoring_range_preserves_partial(self):
        data,metadata=self.model_metadata()
        partial=self.path/(model_download.MODEL['filename']+'.part');partial.write_bytes(data[:17])
        with metadata,patch('model_download.urllib.request.urlopen',return_value=Response(data)):
            manager=model_download.ModelDownload();manager.start(str(self.path),True);manager.thread.join(5)
            self.assertEqual(manager.state['phase'],'error');self.assertEqual(partial.read_bytes(),data[:17])
    def test_migration_preserves_source_and_verified_database(self):
        source=self.path/'source';lib=Library(source)
        try:
            (source/'models').mkdir();model=source/'models/model.litertlm';model.write_bytes(b'LITERTLMtest')
            (source/'runtime-cache').mkdir(exist_ok=True);(source/'runtime-cache/keep-source.txt').write_text('source only',encoding='utf-8')
            lib.configure(str(model),'cpu');lib.save_preferences(setup_step=3,background=True)
            target=self.path/'target';target.mkdir();result=migrate(lib,str(target));destination=Path(result['data_path'])
            self.assertTrue(source.is_dir());self.assertEqual(model.read_bytes(),b'LITERTLMtest')
            with closing(sqlite3.connect(destination/'index.sqlite3')) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
                config=json.loads(db.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0])
                self.assertEqual(config['model_path'],str(destination/'models/model.litertlm'))
            self.assertEqual((destination/'models/model.litertlm').read_bytes(),model.read_bytes())
            self.assertTrue((source/'runtime-cache/keep-source.txt').is_file())
            self.assertFalse((destination/'runtime-cache/keep-source.txt').exists())
            with self.assertRaises(ValueError):migrate(lib,str(target))
        finally:lib.close()
    def test_migration_copy_failure_keeps_source_and_no_final_destination(self):
        source=self.path/'source';lib=Library(source)
        try:
            model=source/'models';model.mkdir();(model/'a.litertlm').write_bytes(b'LITERTLMkeep')
            target=self.path/'target';target.mkdir()
            with patch('storage.shutil.copy2',side_effect=OSError('disk failure')):
                with self.assertRaises(ValueError):migrate(lib,str(target))
            self.assertEqual((model/'a.litertlm').read_bytes(),b'LITERTLMkeep')
            self.assertFalse((target/'LocalPhotoSearchData').exists());self.assertEqual(lib.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:lib.close()
    def test_migration_copies_active_model_outside_models_subdirectory(self):
        source=self.path/'source';lib=Library(source)
        try:
            model=source/'custom-model.litertlm';model.write_bytes(b'LITERTLMcustom')
            lib.configure(str(model),'cpu')
            target=self.path/'target';target.mkdir();result=migrate(lib,str(target))
            copied=Path(result['data_path'])
            with closing(sqlite3.connect(copied/'index.sqlite3')) as db:
                config=json.loads(db.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0])
            self.assertEqual(Path(config['model_path']).read_bytes(),model.read_bytes())
            self.assertEqual(Path(config['model_path']).parent,copied)
            self.assertTrue(model.is_file())
        finally:lib.close()
    def test_legacy_preferences_and_resume_intent(self):
        lib=Library(self.path/'source')
        lib.save_preferences(background=True,setup_step=4);lib._resume_requested(True);lib.close()
        lib=Library(self.path/'source')
        try:
            self.assertTrue(lib.preferences['background']);self.assertEqual(lib.preferences['setup_step'],4);self.assertTrue(lib.resume_requested())
            lib.pause();self.assertFalse(lib.resume_requested())
            with self.assertRaises(ValueError):lib.save_preferences(update_minutes=0)
        finally:lib.close()


if __name__=='__main__':unittest.main(verbosity=2)
