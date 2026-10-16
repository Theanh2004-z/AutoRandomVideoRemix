"""Dashboard layout, export confirmation and saved settings interaction checks."""
import json
from unittest.mock import patch
import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from core.settings_manager import settings
import tests.test_guided_workflow as guided


class DashboardInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guided.GuidedWorkflowTests.setUpClass.__func__(cls)
    setUp=guided.GuidedWorkflowTests.setUp
    tearDown=guided.GuidedWorkflowTests.tearDown
    load_sources=guided.GuidedWorkflowTests.load_sources
    await_preparation=guided.GuidedWorkflowTests.await_preparation
    await_condition=guided.GuidedWorkflowTests.await_condition
    prepare=guided.GuidedWorkflowTests.prepare

    def test_main_sections_fit_on_one_laptop_screen(self):
        self.win.resize(1366,768);self.win.show();self.load_sources();QTest.qWait(200)
        for widget in (self.win.source_panel,self.win.audio_panel,self.win.cut_panel,self.flow.checks_widget,self.flow.export_button):
            self.assertTrue(widget.isVisible(),widget.objectName())
            pos=widget.mapTo(self.win,QPoint(0,0))
            self.assertGreaterEqual(pos.y(),0)
            self.assertLessEqual(pos.y()+widget.height(),self.win.height(),widget.objectName())
        self.assertFalse(self.flow.export_dialog.isVisible())

    def test_export_header_opens_confirmation_without_starting_a_job(self):
        self.flow.export_button.click()
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertEqual(self.flow.export_stack.currentIndex(),0)
        self.assertIsNone(self.win.worker)
        self.assertFalse(self.flow.confirm_button.isEnabled())
        self.flow.confirm_button.click();self.assertIsNone(self.win.worker)

    def test_dialog_changes_refresh_plan_before_confirming(self):
        self.prepare();self.flow.open_export()
        self.assertTrue(self.flow.confirm_button.isEnabled())
        self.flow.confirm_count.setValue(2)
        self.assertEqual(self.win.cut_panel.spin_batch_count.value(),2)
        self.assertFalse(self.flow.validated())
        self.assertFalse(self.flow.confirm_button.isEnabled())
        self.flow.prepare();self.await_preparation();self.flow.refresh()
        self.assertEqual(len(self.win.current_batch_plans),2)
        self.assertTrue(self.flow.confirm_button.isEnabled())

    def test_hidden_progress_can_be_reopened_without_cancelling(self):
        self.prepare();self.flow.running_changed(True)
        self.assertEqual(self.flow.export_stack.currentIndex(),1)
        self.flow.export_dialog.reject()
        self.assertTrue(self.flow._running)
        self.flow.export_button.click()
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertFalse(self.win.source_panel.isEnabled())
        self.flow.finished(0,1)

    def test_save_button_persists_current_preferences_to_disk(self):
        self.win.cut_panel.spin_mins.setValue(8)
        self.win.output_panel.combo_speed.setCurrentIndex(self.win.output_panel.combo_speed.findData('fast'))
        self.win.output_panel.edit_prefix.setText('Loạt thử nghiệm')
        self.flow.save_button.click()
        saved=json.loads(self.root.joinpath('settings.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['target_mins'],8)
        self.assertEqual(saved['render_speed'],'fast')
        self.assertEqual(saved['output_prefix'],'Loạt thử nghiệm')
        self.assertEqual(self.flow.save_status.text(),'Đã lưu thiết lập')

    def test_failed_save_is_visible_to_user(self):
        with patch.object(settings,'save',return_value=False):self.flow.save_button.click()
        self.assertIn('Chưa lưu được',self.flow.save_status.text())

    def test_auto_save_switch_toggles_and_persists_settings(self):
        self.flow.auto_save_switch.setChecked(False)
        self.assertFalse(settings.get('auto_save'))
        self.win.cut_panel.spin_mins.setValue(12)
        self.flow.auto_save_switch.setChecked(True)
        self.assertTrue(settings.get('auto_save'))
        saved=json.loads(self.root.joinpath('settings.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['target_mins'],12)

    def test_export_progress_capcut_style_controls_and_cancel_button(self):
        self.prepare()
        self.flow.open_export()
        self.assertFalse(self.flow.open_output_button.isVisible())
        self.assertFalse(self.flow.new_run_button.isVisible())
        self.assertFalse(self.win.progress_panel.action_widget.isVisible())

        self.assertTrue(hasattr(self.win.progress_panel, 'bar_video'))
        self.assertTrue(hasattr(self.win.progress_panel, 'bar_audio'))
        self.assertEqual(self.win.progress_panel.bar_overall, self.win.progress_panel.bar_video)

        # Top 'X' close button is removed from window title bar
        flags = self.flow.export_dialog.windowFlags()
        self.assertFalse(bool(flags & Qt.WindowCloseButtonHint))

        self.assertEqual(self.flow.dialog_close.text(), 'Đóng')

        self.flow.running_changed(True)
        self.assertEqual(self.flow.dialog_close.text(), 'Hủy')

        cancelled = []
        self.win.progress_panel.cancel_requested.connect(lambda: cancelled.append(True))
        with patch('qfluentwidgets.MessageBox.exec', return_value=True):
            self.flow.dialog_close.click()
        self.assertEqual(len(cancelled), 1)

        self.flow.finished(1, 1)
        self.assertEqual(self.flow.dialog_close.text(), 'Đóng')

    def test_window_geometry_persistence_and_restoration(self):
        self.win.resize(1200, 780)
        self.win.move(100, 150)
        self.win.save_window_geometry()

        self.assertEqual(settings.get('window_width'), 1200)
        self.assertEqual(settings.get('window_height'), 780)
        self.assertEqual(settings.get('window_x'), 100)
        self.assertEqual(settings.get('window_y'), 150)
        self.assertTrue(bool(settings.get('window_geometry')))

        self.win.resize(1100, 740)
        self.win.move(0, 0)
        self.win._restore_window_geometry()
        self.assertEqual(self.win.width(), 1200)
        self.assertEqual(self.win.height(), 780)

    def test_new_run_workflow_after_finished_and_cancelled(self):
        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)
        self.assertFalse(self.flow.new_run_button.isVisible())
        self.assertFalse(self.flow.open_output_button.isVisible())

        # 1. Finished scenario
        self.flow.finished(1, 1)
        self.assertTrue(self.flow.new_run_button.isVisible())
        self.assertTrue(self.flow.new_run_button.isEnabled())
        self.assertTrue(self.flow.open_output_button.isVisible())
        self.assertTrue(self.flow.open_output_button.isEnabled())

        # Click "Lượt mới"
        self.flow.new_run_button.click()
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertEqual(self.flow.dialog_title.text(), 'Kiểm tra và xuất')
        self.assertFalse(self.flow.confirm_button.isEnabled())

        # Await new preparation
        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())
        self.assertEqual(len(self.win.current_batch_plans), 1)

        # 2. Cancelled scenario
        self.flow.running_changed(True)
        self.flow.finished(0, 1)
        self.assertTrue(self.flow.new_run_button.isVisible())
        self.assertTrue(self.flow.new_run_button.isEnabled())
        self.assertFalse(self.flow.open_output_button.isEnabled())

        # Click "Lượt mới" after cancel
        self.flow.new_run_button.click()
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertFalse(self.flow.confirm_button.isEnabled())
        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())

    def test_progress_panel_modes_and_visibility(self):
        panel = self.win.progress_panel

        # Mode 1: video_with_random_audio -> both bars visible
        panel.set_export_mode("video_with_random_audio")
        self.assertFalse(panel.video_container.isHidden())
        self.assertFalse(panel.audio_container.isHidden())

        # Mode 2: audio_only -> video bar hidden, audio bar visible
        panel.set_export_mode("audio_only")
        self.assertTrue(panel.video_container.isHidden())
        self.assertFalse(panel.audio_container.isHidden())

        # Mode 3a: video_only without audio -> video bar visible, audio bar hidden
        panel.set_export_mode("video_only", has_audio=False)
        self.assertFalse(panel.video_container.isHidden())
        self.assertTrue(panel.audio_container.isHidden())

        # Mode 3b: video_only with audio mode "none" -> audio bar hidden
        panel.set_export_mode("video_only", audio_mode="none")
        self.assertFalse(panel.video_container.isHidden())
        self.assertTrue(panel.audio_container.isHidden())

        # Mode 3c: video_only with audio processing (e.g. BGM) -> audio bar visible
        panel.set_export_mode("video_only", has_audio=True)
        self.assertFalse(panel.video_container.isHidden())
        self.assertFalse(panel.audio_container.isHidden())

    def test_progress_bars_independent_updates_and_no_keyword_sniffing(self):
        panel = self.win.progress_panel
        panel.reset_bars()
        self.assertEqual(panel.bar_video.value(), 0)
        self.assertEqual(panel.bar_audio.value(), 0)

        # Explicit video progress
        panel.update_video_progress(45.0)
        self.assertEqual(panel.bar_video.value(), 45)
        self.assertEqual(panel.lbl_video_val.text(), "45%")
        self.assertEqual(panel.bar_audio.value(), 0)

        # Explicit audio progress
        panel.update_audio_progress(70.0)
        self.assertEqual(panel.bar_audio.value(), 70)
        self.assertEqual(panel.lbl_audio_val.text(), "70%")
        self.assertEqual(panel.bar_video.value(), 45)

        # Keyword sniffing removed: update_overall_progress must NOT touch bar_video or bar_audio
        panel.update_overall_progress(90.0, "Đang xử lý âm thanh audio playlist nhạc...")
        self.assertIn("90%", panel.lbl_overall.text())
        self.assertEqual(panel.bar_audio.value(), 70)
        self.assertEqual(panel.bar_video.value(), 45)

    def test_progress_bars_reset_on_new_file_in_batch(self):
        panel = self.win.progress_panel
        self.win.current_batch_plans = [[], []]

        # File 1 starts
        self.win._on_worker_video_started(1, 2, "output1.mp4")
        self.assertEqual(panel.bar_video.value(), 0)
        self.assertEqual(panel.bar_audio.value(), 0)

        # File 1 finishes processing
        panel.update_video_progress(100.0)
        panel.update_audio_progress(100.0)
        self.assertEqual(panel.bar_video.value(), 100)
        self.assertEqual(panel.bar_audio.value(), 100)

        # File 2 starts -> must reset to 0%, NOT keep 100% from previous file
        self.win._on_worker_video_started(2, 2, "output2.mp4")
        self.assertEqual(panel.bar_video.value(), 0)
        self.assertEqual(panel.lbl_video_val.text(), "0%")
        self.assertEqual(panel.bar_audio.value(), 0)
        self.assertEqual(panel.lbl_audio_val.text(), "0%")

        # File 2 progresses independently
        panel.update_video_progress(35.0)
        self.assertEqual(panel.bar_video.value(), 35)
        self.assertEqual(panel.bar_audio.value(), 0)

    def test_worker_signals_definitions_and_emissions(self):
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig

        worker = VideoProcessorWorker([], OutputConfig(export_target="video_with_random_audio"))
        self.assertTrue(hasattr(worker, 'overall_progress'))
        self.assertTrue(hasattr(worker, 'video_progress'))
        self.assertTrue(hasattr(worker, 'audio_progress'))

        received_video = []
        received_audio = []
        received_overall = []

        worker.video_progress.connect(lambda pct: received_video.append(pct))
        worker.audio_progress.connect(lambda pct: received_audio.append(pct))
        worker.overall_progress.connect(lambda pct, msg: received_overall.append((pct, msg)))

        worker.video_progress.emit(55.5)
        worker.audio_progress.emit(88.8)
        worker.overall_progress.emit(42.0, "Testing")

        self.assertEqual(received_video, [55.5])
        self.assertEqual(received_audio, [88.8])
        self.assertEqual(received_overall, [(42.0, "Testing")])

    def test_journey_success_close_export_again(self):
        self.prepare()
        self.flow.open_export()
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertTrue(self.flow.confirm_button.isEnabled())

        # Simulate export started and completed
        self.flow.running_changed(True)
        self.flow.finished(1, 1)
        self.assertEqual(self.flow.export_stack.currentIndex(), 1)
        self.assertEqual(self.flow.dialog_title.text(), 'Xuất hoàn tất')

        # Close dialog
        self.flow.export_dialog.reject()
        self.assertFalse(self.flow.export_dialog.isVisible())

        # Click home page "Xuất" button -> must prepare new run and open confirmation page
        self.flow.export_button.click()
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertEqual(self.flow.dialog_title.text(), 'Kiểm tra và xuất')
        self.assertFalse(self.flow.confirm_button.isEnabled())

        # Await new plan
        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())

    def test_journey_cancel_wait_stop_close_export_again(self):
        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)

        # Trigger cancel
        with patch('qfluentwidgets.MessageBox.exec', return_value=True):
            self.flow.dialog_close.click()
        self.assertTrue(self.flow._cancelling)
        self.assertEqual(self.flow.dialog_close.text(), 'Đang hủy…')

        # Worker stops and finishes
        self.flow.finished(0, 1)
        self.assertFalse(self.flow._running)
        self.assertFalse(self.flow._cancelling)
        self.assertEqual(self.flow._result_state, 'Đã hủy')
        self.assertEqual(self.flow.dialog_title.text(), 'Đã hủy xuất')
        self.assertEqual(self.flow.dialog_close.text(), 'Đóng')

        # Close dialog
        self.flow.export_dialog.reject()
        self.assertFalse(self.flow.export_dialog.isVisible())

        # Click home page "Xuất" button -> must prepare new run and open confirmation page
        self.flow.export_button.click()
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertEqual(self.flow.dialog_title.text(), 'Kiểm tra và xuất')

        # Await new plan
        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())

    def test_journey_export_error_export_again(self):
        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)

        # Finished with failure (0/1 succeeded, not cancelled)
        self.flow.finished(0, 1)
        self.assertEqual(self.flow._result_state, 'Xuất thất bại')
        self.assertEqual(self.flow.dialog_title.text(), 'Xuất thất bại')

        # Close dialog
        self.flow.export_dialog.reject()
        self.assertFalse(self.flow.export_dialog.isVisible())

        # Click home page "Xuất" button -> must prepare new run and open confirmation page
        self.flow.export_button.click()
        self.assertTrue(self.flow.export_dialog.isVisible())
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertEqual(self.flow.dialog_title.text(), 'Kiểm tra và xuất')

        # Await new plan
        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())

    def test_journey_multi_file_cancel_after_one_success_new_run(self):
        self.win.cut_panel.spin_batch_count.setValue(2)
        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)

        # 1 video succeeded, then user cancelled
        self.flow._cancelling = True
        self.flow.finished(1, 2)
        self.assertEqual(self.flow._result_state, 'Đã hủy')
        self.assertTrue(self.flow.open_output_button.isEnabled())

        # Click home page "Xuất"
        self.flow.export_dialog.reject()
        self.flow.export_button.click()
        self.assertEqual(self.flow.export_stack.currentIndex(), 0)
        self.assertEqual(self.flow.dialog_title.text(), 'Kiểm tra và xuất')

        self.await_preparation()
        self.flow.refresh()
        self.assertTrue(self.flow.confirm_button.isEnabled())
        self.assertEqual(len(self.win.current_batch_plans), 2)

    def test_journey_repeated_export_clicks_while_running_or_cancelling(self):
        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)

        # Multiple clicks while running
        self.flow.export_button.click()
        self.assertEqual(self.flow.export_stack.currentIndex(), 1)
        self.assertEqual(self.flow.dialog_title.text(), 'Đang xuất nội dung')

        # Confirm button must be hidden and clicking confirm does nothing
        self.assertFalse(self.flow.confirm_button.isVisible())
        with patch.object(self.win.progress_panel.btn_start, 'click') as mock_start:
            self.flow.confirm_export()
            mock_start.assert_not_called()

        # Enter cancelling state
        self.flow._cancelling = True
        self.flow.refresh()
        self.assertEqual(self.flow.export_button.text(), 'Đang hủy…')

        # Clicking export while cancelling keeps stack at progress and does not start new run
        self.flow.export_button.click()
        self.assertEqual(self.flow.export_stack.currentIndex(), 1)
        with patch.object(self.win.progress_panel.btn_start, 'click') as mock_start:
            self.flow.confirm_export()
            mock_start.assert_not_called()

        self.flow.finished(0, 1)

    def test_journey_three_consecutive_runs_preserve_settings(self):
        # Configure initial settings
        self.win.cut_panel.spin_secs.setValue(4)
        self.win.output_panel.edit_prefix.setText('ConsecutiveTest')
        initial_outdir = self.win.output_panel.get_config().output_dir

        for run_idx in range(1, 4):
            self.prepare()
            self.flow.open_export()
            self.assertEqual(self.flow.export_stack.currentIndex(), 0)
            self.assertTrue(self.flow.confirm_button.isEnabled())

            # Export running & finishing
            self.flow.running_changed(True)
            self.flow.finished(1, 1)
            self.assertEqual(self.flow.dialog_title.text(), 'Xuất hoàn tất')

            # Settings preserved
            self.assertEqual(self.win.cut_panel.spin_secs.value(), 4)
            self.assertEqual(self.win.output_panel.edit_prefix.text(), 'ConsecutiveTest')
            self.assertEqual(self.win.output_panel.get_config().output_dir, initial_outdir)

            # Start next run via home button
            self.flow.export_dialog.reject()
            self.flow.export_button.click()
            self.assertEqual(self.flow.export_stack.currentIndex(), 0)

    def test_cancelled_batch_finished_does_not_show_error_notification(self):
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig

        self.prepare()
        self.flow.open_export()
        self.flow.running_changed(True)

        worker = VideoProcessorWorker([], OutputConfig())
        worker._cancel_requested = True
        self.win.worker = worker

        warnings_shown = []
        errors_shown = []

        with patch('qfluentwidgets.InfoBar.warning', side_effect=lambda **kw: warnings_shown.append(kw)), \
             patch('qfluentwidgets.InfoBar.error', side_effect=lambda **kw: errors_shown.append(kw)):

            # First simulate workflow.finished executing before main_window handler
            self.flow.finished(0, 1)

            # Then main_window's worker finished handler executes
            self.win._on_worker_batch_finished(0, 1)

            # Also any late error signal from worker
            self.win._on_worker_error("Late ffmpeg cancel exit code")

        # Must show "Đã hủy xuất", NOT "Hoàn tất (có lỗi)"
        self.assertTrue(any(w.get('title') == 'Đã hủy xuất' for w in warnings_shown))
        self.assertFalse(any(w.get('title') == 'Hoàn tất (có lỗi)' for w in warnings_shown))

        # Must NOT show error InfoBar
        self.assertEqual(len(errors_shown), 0)

        # Workflow result message must not be overwritten by error
        self.assertIn("Tiến trình đã hủy", self.flow.result_label.text())





