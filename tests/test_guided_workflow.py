"""Functional checks for the source/configuration/review/export workflow."""
import os
import time
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFontDatabase
from core.app_paths import set_test_data_dir, reset_data_dir
from core.config import VideoInfo, AudioInfo
from core.hardware_detector import HardwareProfile
from core.settings_manager import settings
from ui.workflow_state import WorkflowInputs, evaluate_readiness


class ReadinessTests(unittest.TestCase):
    def inputs(self, **changes):
        data = dict(target='video_with_random_audio', video_count=2, video_seconds=40,
                    audio_count=2, audio_seconds=30, duration=10, batch_count=2,
                    unique_policy='unique_batch', output_dir='C:/exports')
        data.update(changes)
        return WorkflowInputs(**data)

    def test_audio_only_needs_no_video(self):
        self.assertTrue(evaluate_readiness(self.inputs(target='audio_only', video_count=0)).can_prepare)

    def test_batch_capacity_uses_the_selected_uniqueness_scope(self):
        self.assertFalse(evaluate_readiness(self.inputs(video_seconds=15)).can_prepare)
        self.assertTrue(evaluate_readiness(self.inputs(video_seconds=15, unique_policy='unique_output')).can_prepare)

    def test_reuse_is_explicit_and_reported(self):
        state = evaluate_readiness(self.inputs(video_seconds=5, unique_policy='cycle_output'))
        self.assertTrue(state.can_prepare)
        self.assertTrue(any(c.state == 'warning' for c in state.checks))

    def test_scanning_and_missing_output_block_preparation(self):
        self.assertFalse(evaluate_readiness(self.inputs(scanning_video=True)).can_prepare)
        self.assertFalse(evaluate_readiness(self.inputs(output_dir='')).can_prepare)

    def test_audio_cannot_silently_repeat_if_cycles_disabled(self):
        self.assertFalse(evaluate_readiness(self.inputs(audio_seconds=2, audio_repeats=False)).can_prepare)

    def test_cancelled_hash_does_not_cache_partial_content(self):
        import hashlib
        from core.models import compute_media_content_hash
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'data.bin'
            content=b'x'*(1024*1024)
            path.write_bytes(content)
            with self.assertRaises(InterruptedError):
                compute_media_content_hash(str(path),cancel_check=lambda: True)
            self.assertEqual(compute_media_content_hash(str(path)),hashlib.sha256(content).hexdigest()[:16])


class GuidedWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for font in ['segoeui.ttf', 'segoeuib.ttf']:
            path = 'C:/Windows/Fonts/' + font
            if os.path.isfile(path):
                QFontDatabase.addApplicationFont(path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        set_test_data_dir(str(self.root))
        self.previous_settings_path = settings.file_path
        self.previous_settings = dict(settings._data)
        settings.set_file_path(str(self.root/'settings.json'))
        settings.update({'video_folder':'', 'audio_folder':'', 'output_folder':str(self.root/'exports'),
                         'target_hours':0, 'target_mins':0, 'target_secs':4, 'batch_count':1,
                         'segment_duration':2, 'unique_policy':'unique_batch', 'allow_reuse':False,
                         'no_repeat_batch':True, 'use_history':False, 'export_target':'video_with_random_audio'})
        from ui.main_window import MainWindow
        profile = HardwareProfile('libx264', 'CPU', 'CPU', False, [], [])
        with patch('core.hardware_detector.detect_hardware', return_value=profile):
            self.win = MainWindow()
        self.flow = self.win.workflow

    def tearDown(self):
        self.flow.timer.stop()
        self.flow.stop_background()
        deadline=time.monotonic()+10
        while self.flow.background_running() and time.monotonic()<deadline:
            QTest.qWait(10)
        self.app.processEvents()
        self.win.close()
        self.win.deleteLater()
        self.app.processEvents()
        settings._file_path = self.previous_settings_path
        settings._data = self.previous_settings
        reset_data_dir()
        self.temp.cleanup()

    def load_sources(self):
        path = self.root/'video.mp4'; path.write_bytes(b'video fixture content')
        audio_path = self.root/'music.wav'; audio_path.write_bytes(b'audio fixture content')
        self.win._on_source_scanned([VideoInfo(str(path), path.name, 12, 320, 180, 30, 'h264', False, path.stat().st_size)])
        self.win._on_audios_scanned([AudioInfo(str(audio_path), audio_path.name, 10, 48000, 2, 'pcm_s16le', audio_path.stat().st_size)])
        self.flow.refresh()

    def await_preparation(self):
        deadline=time.monotonic()+10
        while (self.flow.prepare_worker or self.flow.prepare_timer.isActive()) and time.monotonic()<deadline:
            QTest.qWait(10)
        self.assertIsNone(self.flow.prepare_worker)

    def await_condition(self, condition, timeout=8):
        deadline=time.monotonic()+timeout
        while not condition() and time.monotonic()<deadline:
            QTest.qWait(10)
        self.assertTrue(condition())

    def test_folder_paste_scans_and_prepares_automatically_with_ui_heartbeat(self):
        video=self.root/'video.mp4'; video.write_bytes(b'video')
        music=self.root/'music.wav'; music.write_bytes(b'music')
        videos=[VideoInfo(str(video),video.name,12,320,180,30,'h264',False,5)]
        tracks=[AudioInfo(str(music),music.name,10,48000,2,'pcm_s16le',5)]
        from core.models import compute_media_content_hash
        main_thread=threading.get_ident()
        threads=[]
        ticks=[]
        timer=QTimer(self.win); timer.setInterval(20)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        def slow_hash(*args,**kwargs):
            threads.append(threading.get_ident())
            time.sleep(.18)
            return compute_media_content_hash(*args,**kwargs)
        try:
            with patch('core.video_scanner.scan_directory',return_value=(videos,[])) as scan_video, \
                 patch('core.audio_scanner.scan_audio_directory',return_value=(tracks,[])) as scan_audio, \
                 patch('ui.background.compute_media_content_hash',side_effect=slow_hash):
                self.win.source_panel.folder_edit.setText(str(self.root))
                self.win.audio_panel.folder_edit.setText(str(self.root))
                self.await_condition(self.flow.validated)
                self.assertEqual(scan_video.call_count,1)
                self.assertEqual(scan_audio.call_count,1)
            self.assertGreater(len(ticks),12)
            self.assertTrue(all(t!=main_thread for t in threads))
            self.assertLess(max(b-a for a,b in zip(ticks,ticks[1:])),.25)
        finally:
            timer.stop()

    def test_changed_folder_does_not_accept_old_scan(self):
        self.flow.select_mode('video_only')
        old=self.root/'old'; new=self.root/'new'
        old.mkdir(); new.mkdir()
        for folder in [old,new]: (folder/'video.mp4').write_bytes(folder.name.encode())
        entered=threading.Event()
        def scan(directory,*args,**kwargs):
            if directory==str(old):
                entered.set(); time.sleep(.25)
            path=Path(directory)/'video.mp4'
            return [VideoInfo(str(path),path.name,12,320,180,30,'h264',False,path.stat().st_size)],[]
        with patch('core.video_scanner.scan_directory',side_effect=scan):
            self.win.source_panel.folder_edit.setText(str(old))
            self.await_condition(entered.is_set)
            self.win.source_panel.folder_edit.setText(str(new))
            self.await_condition(self.flow.validated)
        self.assertEqual(self.win.source_videos[0].path,str(new/'video.mp4'))
        self.assertEqual(self.win.current_batch_plans[0][0].source_video_path,str(new/'video.mp4'))

    def test_changed_fps_discards_inflight_plan_and_automatically_rebuilds(self):
        self.load_sources()
        from core.export_planner import ExportPlanner
        create=ExportPlanner.create_manifest
        entered=threading.Event()
        def delayed(planner):
            entered.set(); time.sleep(.2)
            return create(planner)
        with patch('ui.background.ExportPlanner.create_manifest',new=delayed):
            self.flow.prepare()
            self.await_condition(entered.is_set)
            self.win.output_panel.combo_fps.setCurrentIndex(self.win.output_panel.combo_fps.findData(60))
            self.assertFalse(self.flow.validated())
            self.await_condition(self.flow.validated)
        self.assertEqual(sum(c.frame_count for c in self.win.current_batch_plans[0]),240)

    def test_close_during_background_hash_cancels_without_blocking_gui(self):
        self.load_sources()
        entered=threading.Event()
        def cancellable(*args,**kwargs):
            entered.set()
            while not kwargs['cancel_check']():
                time.sleep(.01)
            raise InterruptedError()
        self.win.show()
        with patch('ui.background.compute_media_content_hash',side_effect=cancellable):
            self.flow.prepare()
            self.await_condition(entered.is_set)
            started=time.monotonic()
            self.win.close()
            self.assertLess(time.monotonic()-started,.15)
            self.await_condition(lambda: not self.flow.background_running())
            QTest.qWait(150)
            self.assertFalse(self.win.isVisible())

    def test_settings_keep_export_preferences_outside_session_page(self):
        dialog=self.flow.settings_dialog
        self.assertTrue(dialog.isAncestorOf(self.win.output_panel))
        self.assertTrue(dialog.isAncestorOf(self.win.cut_panel.spin_seg_dur))
        self.assertFalse(self.flow.pages[1].isAncestorOf(self.win.output_panel))
        self.assertTrue(self.flow.pages[1].isAncestorOf(self.win.cut_panel.spin_secs))

    def test_preferences_survive_reopening_the_application(self):
        self.win.audio_panel.chk_auto_reshuffle.setChecked(False)
        self.win.output_panel.combo_quality.setCurrentIndex(self.win.output_panel.combo_quality.findData('very_high'))
        self.flow.settings_dialog.cache_enabled.setChecked(False)
        self.flow.settings_dialog.cache_quota.setValue(7)
        self.win.output_panel.combo_speed.setCurrentIndex(self.win.output_panel.combo_speed.findData('fast'))
        self.win.output_panel.combo_parallel.setCurrentIndex(self.win.output_panel.combo_parallel.findData(2))
        self.win.output_panel.chk_gpu_processing.setChecked(False)
        self.win.output_panel.combo_decoder.setCurrentIndex(self.win.output_panel.combo_decoder.findData('cpu'))
        from qfluentwidgets import isDarkTheme
        if not isDarkTheme():
            self.win._toggle_theme()
        settings._data={}
        settings.load()
        from ui.main_window import MainWindow
        reopened=MainWindow()
        try:
            self.assertFalse(reopened.audio_panel.get_config().auto_reshuffle_cycle)
            self.assertEqual(reopened.output_panel.get_config().quality,'very_high')
            self.assertFalse(reopened.workflow.settings_dialog.cache_enabled.isChecked())
            self.assertEqual(reopened.workflow.settings_dialog.cache_quota.value(),7)
            self.assertEqual(reopened.output_panel.get_config().render_speed,'fast')
            self.assertEqual(reopened.output_panel.get_config().parallel_segments,2)
            self.assertFalse(reopened.output_panel.get_config().gpu_processing)
            self.assertEqual(reopened.output_panel.get_config().decoder_mode,'cpu')
            from qfluentwidgets import isDarkTheme
            self.assertTrue(isDarkTheme())
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_cache_cleanup_preserves_source_like_files_and_subdirectories(self):
        cache=self.root/'cache'; cache.mkdir(exist_ok=True)
        owned=cache/('a'*24+'.mp4'); owned.write_bytes(b'cache')
        unrelated=cache/'video-nguon.mp4'; unrelated.write_bytes(b'original')
        nested=cache/'sub'; nested.mkdir(); (nested/('b'*24+'.mp4')).write_bytes(b'nested')
        dialog=self.flow.settings_dialog
        dialog.cache_path.setText(str(cache))
        dialog.inspect_cache(clear=True)
        self.await_condition(lambda: dialog.cache_worker is None)
        self.assertFalse(owned.exists())
        self.assertEqual(unrelated.read_bytes(),b'original')
        self.assertTrue((nested/('b'*24+'.mp4')).exists())

    def test_playlist_selector_and_tables_are_bounded_for_large_plans(self):
        self.prepare()
        table=self.win.table_view
        segments=self.win.pool.master_pool
        table.set_pool(segments*100)
        self.assertEqual(table.pool_table.rowCount(),200)
        table._turn_page('pool',1,table._display_pool)
        self.assertEqual(table.pool_table.item(0,0).text(),'#201')
        playlist=self.win.current_audio_playlists[0]
        table.set_audio_playlists([playlist,playlist*2])
        table.combo_audio_output.setCurrentIndex(1)
        self.assertEqual(table.audio_table.rowCount(),len(playlist)*2)

    def test_cancel_before_renderer_initialization_is_remembered(self):
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig
        from unittest.mock import MagicMock
        worker=VideoProcessorWorker([[]],OutputConfig(output_dir=str(self.root/'exports')))
        worker.cancel()
        engine=MagicMock()
        engine._is_cancelled=False
        engine.cancel.side_effect=lambda: setattr(engine,'_is_cancelled',True)
        with patch('core.video_processor.VideoProcessorEngine',return_value=engine):
            worker.run()
        engine.cancel.assert_called_once()
        engine.process_video.assert_not_called()

    def test_cancel_before_audio_renderer_initialization_is_remembered(self):
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig
        from unittest.mock import MagicMock
        worker=VideoProcessorWorker([],OutputConfig(output_dir=str(self.root/'exports'),export_target='audio_only'),audio_playlists=[[MagicMock()]])
        worker.cancel()
        engine=MagicMock()
        engine._is_cancelled=False
        engine.cancel.side_effect=lambda: setattr(engine,'_is_cancelled',True)
        with patch('core.video_processor.AudioProcessorEngine',return_value=engine):
            worker.run()
        engine.cancel.assert_called_once()
        engine.process_audio_playlist.assert_not_called()

    def prepare(self):
        self.load_sources()
        self.flow.go_to(2)
        with patch('ui.main_window.InfoBar.success'), patch('ui.main_window.InfoBar.error'):
            self.flow.prepare()
            self.await_preparation()
        self.assertTrue(self.flow.validated(), self.flow.plan_notice.text())

    def test_empty_sources_explain_why_and_cannot_skip_to_export(self):
        self.assertFalse(self.flow.confirm_button.isEnabled())
        self.flow.go_to(3)
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())
        self.assertIn('quét video', self.flow.footer_hint.text())

    def test_audio_mode_hides_video_and_plans_all_audio_outputs(self):
        self.load_sources()
        self.flow.select_mode('audio_only')
        self.win._on_source_scanned([])
        self.win.cut_panel.spin_batch_count.setValue(2)
        self.flow.go_to(2)
        with patch('ui.main_window.InfoBar.success'):
            self.flow.prepare()
            self.await_preparation()
        self.assertTrue(self.flow.validated())
        self.assertFalse(self.flow.video_toggle.isChecked())
        self.assertTrue(self.flow.video_body.isHidden())
        self.assertFalse(self.win.source_panel.isHidden())
        self.assertTrue(self.win.output_panel.video_options.isHidden())
        self.assertEqual(len(self.win.current_audio_playlists), 2)

    def test_review_opens_export_only_after_real_plan_is_built(self):
        self.prepare()
        self.flow.go_to(3)
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertTrue(self.win.progress_panel.btn_start.isEnabled())
        self.assertEqual(len(self.win.current_batch_plans), 1)

    def test_output_fps_change_requires_new_plan_and_uses_new_timebase(self):
        self.prepare()
        self.win.output_panel.combo_fps.setCurrentIndex(self.win.output_panel.combo_fps.findData(60))
        self.assertFalse(self.flow.validated())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())
        with patch('ui.main_window.InfoBar.success'):
            self.flow.prepare()
            self.await_preparation()
        self.assertTrue(self.flow.validated())
        self.assertEqual(sum(c.frame_count for c in self.win.current_batch_plans[0]), 4*60)

    def test_segment_length_change_discards_the_old_pool(self):
        self.prepare()
        self.assertIsNotNone(self.win.pool)
        self.win.cut_panel.spin_seg_dur.setValue(3)
        self.assertIsNone(self.win.pool)
        self.assertFalse(self.flow.validated())

    def test_manual_playlist_replacement_requires_full_review_again(self):
        self.prepare()
        with patch('ui.main_window.InfoBar.success'):
            self.win.progress_panel.audio_playlist_requested.emit()
        self.assertFalse(self.flow.validated())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())

    def test_unwritable_output_reports_start_error_without_secondary_crash(self):
        self.prepare()
        with patch('ui.main_window.os.makedirs', side_effect=PermissionError('Output denied')), \
             patch('ui.main_window.InfoBar.error'):
            self.win._on_start_rendering()
        self.assertIn('Output denied', self.win.progress_panel.last_error)
        self.assertIsNone(self.win.worker)

    def test_source_path_change_clears_loaded_results(self):
        self.prepare()
        self.win.source_panel.folder_edit.setText(str(self.root/'different-source'))
        self.assertEqual(self.win.source_videos, [])
        self.assertFalse(self.flow.validated())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())

    def test_failed_preparation_keeps_the_error_visible(self):
        self.load_sources()
        self.flow.go_to(2)
        with patch('ui.background.ExportPlanner.create_manifest', side_effect=RuntimeError('Không đủ nguồn cho kế hoạch này')):
            self.flow.prepare()
            self.await_preparation()
        self.flow.refresh()
        self.assertIn('Không đủ nguồn', self.flow.plan_notice.text())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())

    def test_running_job_locks_settings_and_failed_job_requires_review(self):
        self.prepare()
        self.win.progress_panel.set_running_state(True)
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertFalse(self.flow.pages[1].isEnabled())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())
        self.flow.finished(0, 1)
        self.assertFalse(self.flow.validated())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())

    def test_completed_job_offers_new_run_with_sources_preserved(self):
        self.prepare()
        self.flow.finished(1, 1)
        self.assertIn('hoàn tất', self.flow.result_label.text())
        self.assertFalse(self.win.progress_panel.btn_start.isEnabled())
        self.flow.new_run()
        self.assertFalse(self.flow.export_dialog.isVisible())
        self.assertFalse(self.flow.validated())
        self.assertEqual(len(self.win.source_videos), 1)
