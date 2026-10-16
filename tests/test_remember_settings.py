"""Preferences survive process restarts, relocation and interrupted writes."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from core.settings_manager import SettingsManager,DEFAULT_SETTINGS
from core import app_paths


class RememberSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.path=self.root/'shared'/'user_settings.json'

    def tearDown(self):
        self.temp.cleanup()

    def manager(self,legacy=()):
        class IsolatedManager(SettingsManager):
            _instance=None
        with patch('core.settings_manager.get_settings_json_path',return_value=str(self.path)), \
             patch('core.settings_manager.get_legacy_settings_paths',return_value=[str(p) for p in legacy]):
            manager=IsolatedManager()
            manager._file_path=str(self.path)
            return manager

    def write(self,path,data):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')

    def test_legacy_preferences_are_imported_once_without_changing_original(self):
        legacy=self.root/'old-exe'/'_internal'/'user_settings.json'
        self.write(legacy,{'video_folder':'D:/Kho cảnh','render_speed':'fast','target_mins':8,'theme':'dark'})
        before=legacy.read_bytes()
        first=self.manager([legacy])
        self.assertEqual(first.get('video_folder'),'D:/Kho cảnh')
        self.assertTrue(self.path.is_file())
        first.set('target_mins',12)
        second=self.manager([legacy])
        self.assertEqual(second.get('target_mins'),12)
        self.assertEqual(legacy.read_bytes(),before)

    def test_existing_shared_preferences_win_over_local_installation(self):
        legacy=self.root/'old.json'
        self.write(legacy,{'render_speed':'quality'})
        self.write(self.path,{'render_speed':'fast','parallel_segments':2})
        self.assertEqual(self.manager([legacy]).get('render_speed'),'fast')

    def test_backup_recovers_damaged_main_file(self):
        manager=self.manager()
        manager.update({'target_mins':8,'decoder_mode':'cpu','theme':'dark'})
        manager.set('target_mins',9)
        self.path.write_text('{truncated',encoding='utf-8')
        recovered=self.manager()
        self.assertEqual(recovered.get('target_mins'),8)
        self.assertEqual(recovered.get('decoder_mode'),'cpu')
        self.assertEqual(json.loads(self.path.read_text())['theme'],'dark')

    def test_failed_atomic_replace_keeps_previous_valid_file_and_cleans_temp(self):
        manager=self.manager();manager.set('target_mins',8)
        original=self.path.read_bytes()
        with patch('core.settings_manager.os.replace',side_effect=OSError('File busy')):
            manager.set('target_mins',9)
        self.assertTrue(manager.last_error)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(list(self.path.parent.glob('.remix-settings-*.tmp')),[])
        self.assertTrue(manager.save())
        self.assertEqual(self.manager().get('target_mins'),9)

    def test_invalid_saved_values_do_not_break_application_controls(self):
        self.write(self.path,{'target_mins':{},'batch_count':-5,'segment_duration':'bad',
            'cache_max_gb':9999,'fps':False,'parallel_segments':[],'theme':'unknown',
            'gpu_processing':'false','quality':'not-a-profile','video_folder':123,
            'export_target':'audio_only'})
        manager=self.manager()
        for key in ('target_mins','batch_count','segment_duration','cache_max_gb','fps',
                    'parallel_segments','theme','gpu_processing','quality','video_folder'):
            self.assertEqual(manager.get(key),DEFAULT_SETTINGS[key],key)
        self.assertEqual(manager.get('export_target'),'audio_only')

    def test_custom_test_path_never_imports_real_user_settings(self):
        manager=self.manager();isolated=self.root/'test'/'settings.json'
        with patch('core.settings_manager.get_legacy_settings_paths',side_effect=AssertionError('must not migrate')):
            manager.set_file_path(str(isolated))
        self.assertEqual(manager.get('video_folder'),'')

    def test_frozen_build_migrates_internal_location_and_uses_stable_store(self):
        with patch.object(app_paths,'_TEST_DATA_DIR',None), \
             patch.dict(os.environ,{'APPDATA':str(self.root/'local-data')}), \
             patch.object(sys,'frozen',True,create=True), \
             patch.object(sys,'executable',str(self.root/'release-1'/'tool.exe')), \
             patch.object(sys,'_MEIPASS',str(self.root/'release-1'/'_internal'),create=True):
            first=app_paths.get_settings_json_path()
            self.assertIn(str(self.root/'release-1'/'_internal'/'user_settings.json'),app_paths.get_legacy_settings_paths())
            with patch.object(sys,'executable',str(self.root/'release-2'/'tool.exe')):
                second=app_paths.get_settings_json_path()
        self.assertEqual(first,second)
        self.assertEqual(first,str(self.root/'local-data'/'AutoRandomVideoRemix'/'user_settings.json'))

    def test_real_process_restart_keeps_all_preferences_after_relocation(self):
        source=Path(__file__).resolve().parents[1]
        env=dict(os.environ,APPDATA=str(self.root/'local-data'),PYTHONPATH=str(source),PYTHONDONTWRITEBYTECODE='1')
        values={
            'video_folder':'D:/Kho cảnh','audio_folder':'D:/Nhạc','output_folder':'D:/Kết quả',
            'recursive_video':True,'recursive_audio':True,'segment_duration':7.5,
            'target_hours':1,'target_mins':8,'target_secs':12,'batch_count':3,
            'merge_remainder':False,'trim_last_segment':False,'unique_policy':'unique_output',
            'anti_consecutive_policy':'strict','use_history':True,'no_consecutive_audio':False,
            'auto_reshuffle_audio':False,'audio_mode':'keep_all','export_target':'video_only',
            'audio_output_format':'m4a','resolution':'1080x1920','fps':60,'quality':'very_high',
            'aspect_ratio_mode':'crop','video_audio_mode':'bgm','bgm_path':'D:/Nhạc/a.wav',
            'output_prefix':'Series A','hw_accel':'nvenc','render_speed':'fast',
            'parallel_segments':2,'gpu_processing':False,'decoder_mode':'cpu',
            'cache_enabled':False,'cache_max_gb':7,'cache_folder':'D:/Cache','theme':'dark'}
        for phase in ('writer','reader'):
            folder=self.root/phase;folder.mkdir()
            code="import sys,json;sys.frozen=True;sys.executable="+repr(str(folder/'tool.exe'))+";from core.settings_manager import settings;"
            if phase=='writer':code+='settings.update('+repr(values)+');assert not settings.last_error'
            else:code+='assert all(settings.get(k)==v for k,v in '+repr(values)+'.items()), settings._data'
            process=subprocess.run([sys.executable,'-c',code],cwd=folder,env=env,capture_output=True,text=True,timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.assertEqual(process.returncode,0,process.stderr)

    def test_history_migration_includes_committed_wal_without_overwriting_existing_db(self):
        import sqlite3
        legacy=self.root/'old-app'/'history'/'export_history.db'
        legacy.parent.mkdir(parents=True)
        connection=sqlite3.connect(legacy)
        try:
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('CREATE TABLE exports (name TEXT)')
            connection.execute("INSERT INTO exports VALUES ('committed in WAL')")
            connection.commit()
            with patch.object(app_paths,'_TEST_DATA_DIR',None), \
                 patch.dict(os.environ,{'APPDATA':str(self.root/'user-data')}), \
                 patch.object(app_paths,'get_app_dir',return_value=str(legacy.parent.parent)):
                target=Path(app_paths.get_history_db_path())
                with closing(sqlite3.connect(target)) as shared:
                    self.assertEqual(shared.execute('SELECT name FROM exports').fetchone()[0],'committed in WAL')
                    shared.execute("INSERT INTO exports VALUES ('new shared entry')")
                    shared.commit()
                self.assertEqual(Path(app_paths.get_history_db_path()),target)
                with closing(sqlite3.connect(target)) as shared:
                    self.assertEqual(shared.execute('SELECT COUNT(*) FROM exports').fetchone()[0],2)
        finally:
            connection.close()

    def test_old_policy_flags_are_preserved_when_named_policies_are_absent(self):
        self.write(self.path,{'allow_reuse':True,'no_consecutive_video':False})
        manager=self.manager()
        self.assertEqual(manager.get('unique_policy'),'cycle_output')
        self.assertEqual(manager.get('anti_consecutive_policy'),'off')

    def test_failed_history_migration_keeps_using_existing_history(self):
        import sqlite3
        legacy=self.root/'old-app'/'history'/'export_history.db'
        legacy.parent.mkdir(parents=True)
        with closing(sqlite3.connect(legacy)) as connection:
            connection.execute('CREATE TABLE exports (name TEXT)')
            connection.commit()
        with patch.object(app_paths,'_TEST_DATA_DIR',None), \
             patch.dict(os.environ,{'APPDATA':str(self.root/'user-data')}), \
             patch.object(app_paths,'get_app_dir',return_value=str(legacy.parent.parent)), \
             patch.object(app_paths.os,'rename',side_effect=OSError('File unavailable')):
            self.assertEqual(Path(app_paths.get_history_db_path()),legacy)
