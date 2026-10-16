"""Dashboard layout, export confirmation and saved settings interaction checks."""
import json
from unittest.mock import patch
import unittest

from PySide6.QtCore import QPoint
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
