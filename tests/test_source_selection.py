"""Source checkbox routing, scan suspension and integrated title bar behavior."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from PySide6.QtCore import QPoint,Qt,QThread,Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox
from core.settings_manager import settings
from core.config import VideoInfo
import tests.test_guided_workflow as guided


class SourceSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):guided.GuidedWorkflowTests.setUpClass.__func__(cls)
    setUp=guided.GuidedWorkflowTests.setUp
    tearDown=guided.GuidedWorkflowTests.tearDown
    load_sources=guided.GuidedWorkflowTests.load_sources
    await_condition=guided.GuidedWorkflowTests.await_condition

    def test_checkboxes_choose_all_four_targets_and_keep_section_headers_visible(self):
        self.win.show()
        self.assertFalse(hasattr(self.flow,'mode_combo'))
        for video,audio,target in ((True,False,'video_only'),(False,False,'none'),(False,True,'audio_only'),(True,True,'video_with_random_audio')):
            self.flow.video_toggle.setChecked(video);self.flow.audio_toggle.setChecked(audio)
            self.assertEqual(self.win.audio_panel.get_export_target(),target)
            self.assertEqual(settings.get('export_target'),target)
            self.assertEqual(not self.flow.video_body.isHidden(),video)
            self.assertEqual(not self.flow.audio_body.isHidden(),audio)
            self.assertFalse(self.win.source_panel.isHidden())
            self.assertFalse(self.win.audio_panel.isHidden())

    def test_neither_source_selected_blocks_preparation_and_export(self):
        self.load_sources()
        self.flow.video_toggle.setChecked(False);self.flow.audio_toggle.setChecked(False)
        self.flow.prepare()
        self.assertIsNone(self.flow.prepare_worker)
        self.assertFalse(self.flow.validated())
        self.flow.open_export();self.assertFalse(self.flow.confirm_button.isEnabled())
        self.flow.confirm_button.click();self.assertIsNone(self.win.worker)
        self.assertIn('Tích Video',self.flow.footer_hint.text())

    def test_persisted_audio_only_and_none_restore_checkboxes(self):
        from ui.main_window import MainWindow
        for target in ('audio_only','none'):
            self.flow.select_mode(target);self.flow.save_button.click()
            settings._data={};settings.load()
            reopened=MainWindow()
            try:
                self.assertFalse(reopened.workflow.video_toggle.isChecked())
                self.assertEqual(reopened.workflow.audio_toggle.isChecked(),target=='audio_only')
                self.assertEqual(reopened.audio_panel.get_export_target(),target)
            finally:
                reopened.close();reopened.deleteLater();self.app.processEvents()

    def test_disabled_source_does_not_start_scanning_on_folder_change(self):
        self.flow.select_mode('none')
        with patch('ui.auto_scan.MediaScanWorker') as worker:
            self.win.source_panel.folder_edit.setText(str(self.root))
            self.win.audio_panel.folder_edit.setText(str(self.root))
            QTest.qWait(700)
            self.assertFalse(worker.called)
            self.assertFalse(self.win.source_panel.auto_scan.timer.isActive())
            self.assertFalse(self.win.audio_panel.auto_scan.timer.isActive())

    def test_turning_source_off_cancels_scan_and_rejects_late_results(self):
        release=threading.Event();created=[]
        path=self.root/'video.mp4';path.write_bytes(b'synthetic fixture')
        info=VideoInfo(str(path),path.name,12,320,180,30,'h264',False,17)
        class SlowScan(QThread):
            progress=Signal(int,int,str);scanned=Signal(list,list);failed=Signal(str)
            def __init__(self,folder,recursive,media_type,parent):
                super().__init__(parent);self.folder=folder;self.recursive=recursive;self.cancelled=False;created.append(self)
            def cancel(self):self.cancelled=True
            def run(self):
                release.wait(2);self.scanned.emit([info],[])
        self.flow.select_mode('video_only')
        with patch('ui.auto_scan.MediaScanWorker',SlowScan):
            self.win.source_panel.folder_edit.setText(str(self.root));self.win.source_panel.auto_scan.start()
            self.assertEqual(len(created),1)
            self.flow.video_toggle.setChecked(False)
            self.assertTrue(created[0].cancelled)
            release.set();self.await_condition(lambda:self.win.source_panel.scanner_thread is None)
            self.assertEqual(self.win.source_videos,[])
            self.assertEqual(self.win.source_panel.valid_videos,[])
            self.flow.video_toggle.setChecked(True)
            self.assertTrue(self.win.source_panel.auto_scan.enabled)
            self.assertTrue(self.win.source_panel.auto_scan.timer.isActive())

    def test_title_bar_contains_actions_and_window_controls_in_one_row(self):
        self.win.show();QTest.qWait(100)
        header=self.flow.header
        self.assertIs(self.win.menuWidget(),header)
        self.assertIs(self.win.titleBar,header)
        self.assertTrue(self.win.windowFlags() & Qt.FramelessWindowHint)
        export_y=self.flow.export_button.mapTo(self.win,self.flow.export_button.rect().center()).y()
        close_y=header.closeBtn.mapTo(self.win,header.closeBtn.rect().center()).y()
        self.assertLessEqual(abs(export_y-close_y),2)
        self.assertGreaterEqual(self.flow.mapTo(self.win,QPoint()).y(),header.height())
        for widget in (self.flow.save_button,self.flow.settings_button,self.flow.export_button,header.closeBtn):
            point=widget.mapTo(header,widget.rect().center())
            self.assertFalse(header.canDrag(point))

    def test_maximize_restore_minimize_and_double_click(self):
        self.win.show();QTest.qWait(50)
        header=self.flow.header
        header.maxBtn.click();self.assertTrue(self.win.isMaximized())
        header.maxBtn.click();self.assertFalse(self.win.isMaximized())
        point=header.content.mapTo(header,QPoint(self.flow.save_status.geometry().right()+25,32))
        self.assertTrue(header.canDrag(point))
        QTest.mouseDClick(header,Qt.LeftButton,pos=point)
        self.assertTrue(self.win.isMaximized())
        header.maxBtn.click();header.minBtn.click();self.assertTrue(self.win.isMinimized())
        self.win.showNormal()

    def test_close_button_preserves_running_job_confirmation(self):
        self.win.show()
        worker=MagicMock();worker.isRunning.return_value=True;self.win.worker=worker
        with patch('ui.main_window.QMessageBox.question',return_value=QMessageBox.No) as question:
            self.flow.header.closeBtn.click()
        self.assertTrue(question.called);self.assertTrue(self.win.isVisible())
        self.assertFalse(worker.cancel.called)
        self.win.worker=None
