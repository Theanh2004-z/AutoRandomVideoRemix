import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.storage_cleanup import Workspace, clean_orphans, MARKER, SpaceGuard
from core.render_cache import RenderCache


class StorageCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scratch = self.root/'scratch'
        self.scratch.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def age(self, workspace):
        workspace.info['created'] = time.time() - 48*3600
        workspace._write_marker()

    def test_live_workspace_keeps_lease_even_when_old(self):
        workspace = Workspace(self.scratch, 'remix')
        try:
            self.age(workspace)
            (workspace.path/'seg.mp4').write_bytes(b'active')
            self.assertEqual(clean_orphans(self.scratch), 0)
            self.assertTrue(workspace.path.exists())
        finally:
            workspace.cleanup()

    def test_orphan_removes_only_owned_workspace_and_tracked_partial(self):
        workspace = Workspace(self.scratch, 'remix')
        final = self.root/'finished.mp4'; final.write_bytes(b'keep result')
        source = self.scratch/'source.mp4'; source.write_bytes(b'keep source')
        staging = self.root/'_staging_12345678.mp4'
        workspace.track_staging(staging); staging.write_bytes(b'incomplete')
        self.age(workspace); workspace.close()
        legacy = self.scratch/'_temp_remix_12345678'; legacy.mkdir()
        unknown = self.scratch/('_temp_remix_'+'a'*32); unknown.mkdir()
        self.assertEqual(clean_orphans(self.scratch), 1)
        self.assertFalse(workspace.path.exists())
        self.assertFalse(staging.exists())
        self.assertEqual(final.read_bytes(), b'keep result')
        self.assertEqual(source.read_bytes(), b'keep source')
        self.assertTrue(legacy.exists()); self.assertTrue(unknown.exists())

    def test_recent_crash_workspace_is_preserved(self):
        workspace = Workspace(self.scratch, 'audio'); workspace.close()
        self.assertEqual(clean_orphans(self.scratch), 0)
        workspace.cleanup()

    def test_cleanup_rejects_linked_tree(self):
        workspace = Workspace(self.scratch, 'remix')
        target = self.root/'outside'; target.mkdir(); (target/'source.mp4').write_bytes(b'keep')
        link = workspace.path/'outside'
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError:
            workspace.cleanup()
            self.skipTest('Symbolic links unavailable')
        self.age(workspace); workspace.close()
        try:
            self.assertEqual(clean_orphans(self.scratch), 0)
            self.assertTrue((target/'source.mp4').exists())
        finally:
            link.unlink(); workspace.cleanup()

    def test_track_staging_rejects_existing_user_file(self):
        workspace = Workspace(self.scratch, 'remix')
        file = self.root/'_staging_abcdef12.mp4'; file.write_bytes(b'keep')
        try:
            with self.assertRaises(ValueError):
                workspace.track_staging(file)
            workspace.cleanup()
            self.assertEqual(file.read_bytes(), b'keep')
        finally:
            workspace.close()

    def cache(self, quota=500, minimum=0):
        return RenderCache(str(self.root/'cache'), quota, min_free_bytes=minimum)

    def clip(self, name, size):
        file=self.root/name; file.write_bytes(b'0000ftyp'+b'x'*(size-8)); return str(file)

    def test_cache_accounting_does_not_double_first_entry(self):
        cache = self.cache()
        self.assertIsNotNone(cache.put('a'*24, self.clip('a.mp4', 300)))
        self.assertEqual(cache._approx_size_bytes, 300)
        cache.put('b'*24, self.clip('b.mp4', 300))
        self.assertIsNone(cache.get('a'*24))
        self.assertIsNotNone(cache.get('b'*24))

    def test_low_space_skips_cache_before_copying(self):
        cache = self.cache(minimum=1000)
        source = self.clip('clip.mp4', 300)
        with patch('core.render_cache.shutil.disk_usage', return_value=SimpleNamespace(free=1100)), patch('core.render_cache.shutil.copy2') as copy:
            self.assertIsNone(cache.put('a'*24, source))
            copy.assert_not_called()

    def test_pressure_evicts_cache_but_preserves_unrelated_media(self):
        cache = self.cache(minimum=1000)
        entry = Path(cache.cache_dir)/('a'*24+'.mp4'); entry.write_bytes(b'0000ftyp'+b'x'*100)
        user_file=Path(cache.cache_dir)/'holiday.mp4'; user_file.write_bytes(b'user media')
        with patch('core.render_cache.shutil.disk_usage', return_value=SimpleNamespace(free=100)):
            cache.flush()
        self.assertFalse(entry.exists()); self.assertTrue(user_file.exists())

    def test_active_hardlink_cache_entry_is_not_evicted(self):
        cache = self.cache()
        entry = cache.put('a'*24, self.clip('a.mp4', 300))
        cache.max_size_bytes = 1
        # The sandbox forbids creating NTFS hard links. Model the link count
        # returned by the filesystem while exercising real eviction/deletion.
        with patch('core.render_cache._link_count', return_value=2):
            cache.flush()
        self.assertTrue(Path(entry).exists())
        cache.flush()
        self.assertFalse(Path(entry).exists())

    def test_abandoned_cache_copy_requires_dead_process_and_old_age(self):
        cache = self.cache()
        stale=Path(cache.cache_dir)/('a'*24+'.mp4.tmp_999999_abcdef')
        stale.write_bytes(b'partial'); old=time.time()-90000; os.utime(stale, (old, old))
        with patch('core.render_cache.process_alive', return_value=True):
            cache.flush(); self.assertTrue(stale.exists())
        with patch('core.render_cache.process_alive', return_value=False):
            cache.flush(); self.assertFalse(stale.exists())

    def test_low_space_stops_engine(self):
        from unittest.mock import Mock
        engine=Mock(render_cache=None)
        guard=SpaceGuard([self.scratch], engine)
        with patch('core.storage_cleanup.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            self.assertFalse(guard.start())
        engine.cancel.assert_called_once()

    def test_low_space_can_recover_by_pruning_cache(self):
        from unittest.mock import Mock
        engine=Mock()
        guard=SpaceGuard([self.scratch], engine)
        with patch('core.storage_cleanup.shutil.disk_usage', side_effect=[SimpleNamespace(free=0), SimpleNamespace(free=200*1024**3)]):
            self.assertTrue(guard.check())
        engine.render_cache.flush.assert_called_once()
        engine.cancel.assert_not_called()

    def test_missing_drive_stops_engine_instead_of_losing_finished_signal(self):
        from unittest.mock import Mock
        engine=Mock(render_cache=None)
        guard=SpaceGuard([self.scratch], engine)
        with patch('core.storage_cleanup.shutil.disk_usage', side_effect=OSError('drive removed')):
            self.assertFalse(guard.start())
        engine.cancel.assert_called_once()

    def test_cleanup_waits_for_owned_process_to_stop(self):
        from core.storage_cleanup import stop_owned_process
        from unittest.mock import Mock
        process=Mock()
        process.poll.side_effect=[None, -9]
        engine=Mock(_current_subprocess=process)
        self.assertTrue(stop_owned_process(engine))
        engine.cancel.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)

    def test_video_exception_cleans_workspace_and_partial(self):
        from core.video_processor import VideoProcessorEngine
        from core.config import OutputConfig
        engine=VideoProcessorEngine(OutputConfig(), scratch_dir=str(self.scratch), render_cache=self.cache())
        staging=self.root/'_staging_abcdef12.mp4'
        def fail(*args):
            engine._workspace=Workspace(self.scratch, 'remix')
            engine._temp_dir=str(engine._workspace.path)
            engine._workspace.track_staging(staging); staging.write_bytes(b'partial')
            raise RuntimeError('unexpected failure')
        with patch.object(engine, '_process_video_plan', side_effect=fail):
            with self.assertRaises(RuntimeError):
                engine.process_video_plan([], str(self.root/'result.mp4'), 1, 1)
        self.assertFalse(staging.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])

    def test_audio_exception_cleans_workspace_and_new_partial(self):
        from core.audio_processor import AudioProcessorEngine
        engine=AudioProcessorEngine(scratch_dir=str(self.scratch))
        result=self.root/'result.mp3'
        def fail(*args):
            engine._workspace=Workspace(self.scratch, 'audio')
            engine._temp_dir=str(engine._workspace.path)
            result.write_bytes(b'partial')
            raise RuntimeError('unexpected failure')
        with patch.object(engine, '_process_audio_playlist', side_effect=fail):
            with self.assertRaises(RuntimeError):
                engine.process_audio_playlist([], str(result))
        self.assertFalse(result.exists())
        self.assertEqual(list(self.scratch.iterdir()), [])
