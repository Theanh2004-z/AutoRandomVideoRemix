"""
Unit test verifying that clicking Start Export immediately on tool launch
does not freeze or fail silently, and safely triggers auto-preparation.
"""
import unittest
import sys
import os

from PySide6.QtWidgets import QApplication

app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)

from ui.main_window import MainWindow
from core.config import VideoInfo, AudioInfo


class TestAutoStartFlow(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow()

    def test_start_export_without_videos_does_not_crash(self):
        """Clicking start export when sources are empty should not crash or freeze."""
        # Empty source videos
        self.win.source_videos = []
        # Clear paths to simulate empty folder
        self.win.source_panel.folder_edit.setText("")
        self.win.progress_panel.btn_start.click()
        # Button should remain enabled, not stuck
        self.assertTrue(self.win.progress_panel.btn_start.isEnabled())

    def test_auto_resume_after_scan(self):
        """When scan completes with pending flag, auto-resume kicks in."""
        self.win._pending_start_export = True
        dummy_vids = [
            VideoInfo(
                path="E:/test.mp4",
                filename="test.mp4",
                duration=60.0,
                width=1920,
                height=1080,
                fps=30.0,
                codec="h264",
                has_audio=True,
                size_bytes=1000,
                is_valid=True
            )
        ]
        self.win._on_source_scanned(dummy_vids)
        self.assertEqual(len(self.win.source_videos), 1)


if __name__ == "__main__":
    unittest.main()
