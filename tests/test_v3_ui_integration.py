"""
V3 UI Integration Tests (Đợt 5 - C08).
Verifies:
- CutConfigPanel uniqueness policy & anti-consecutive selectors and backward-compat sync.
- Plan invalidation and staleness tracking in MainWindow.
- Batch audio-only playlist planning and execution.
- Graceful worker and process tree termination.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)

from core.config import CutConfig, VideoInfo, AudioInfo, AudioConfig, OutputConfig
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.settings_manager import settings
from ui.components.cut_config_panel import CutConfigPanel
from ui.main_window import MainWindow
from core.video_processor import VideoProcessorWorker, VideoProcessorEngine
from core.audio_processor import AudioProcessorEngine


class TestCutConfigPanelPolicies(unittest.TestCase):
    """Verifies Area B CutConfigPanel policy controls and synchronization."""

    def setUp(self):
        self.panel = CutConfigPanel()

    def test_uniqueness_policy_selection(self):
        # Unique Batch
        self.panel.set_unique_policy("unique_batch")
        self.assertEqual(self.panel.get_unique_policy(), "unique_batch")
        cfg = self.panel.get_config()
        self.assertTrue(cfg.no_repeat_across_batch)
        self.assertFalse(cfg.allow_reuse)

        # Unique Output
        self.panel.set_unique_policy("unique_output")
        self.assertEqual(self.panel.get_unique_policy(), "unique_output")
        cfg = self.panel.get_config()
        self.assertFalse(cfg.no_repeat_across_batch)
        self.assertFalse(cfg.allow_reuse)

        # Cycle Output
        self.panel.set_unique_policy("cycle_output")
        self.assertEqual(self.panel.get_unique_policy(), "cycle_output")
        cfg = self.panel.get_config()
        self.assertFalse(cfg.no_repeat_across_batch)
        self.assertTrue(cfg.allow_reuse)

    def test_legacy_checkbox_sync(self):
        # Setting allow_reuse checkbox True -> changes policy to cycle_output
        self.panel.set_unique_policy("unique_batch")
        self.panel.chk_allow_reuse.setChecked(True)
        self.assertEqual(self.panel.get_unique_policy(), "cycle_output")

        # Setting no_repeat_batch checkbox True -> changes policy to unique_batch
        self.panel.chk_no_repeat_batch.setChecked(True)
        self.assertEqual(self.panel.get_unique_policy(), "unique_batch")

    def test_anti_consecutive_policy_selection(self):
        self.panel.set_anti_consecutive_policy("soft")
        self.assertEqual(self.panel.get_anti_consecutive_policy(), "soft")
        cfg = self.panel.get_config()
        self.assertTrue(cfg.no_consecutive_same_source)

        self.panel.set_anti_consecutive_policy("strict")
        self.assertEqual(self.panel.get_anti_consecutive_policy(), "strict")
        cfg = self.panel.get_config()
        self.assertTrue(cfg.no_consecutive_same_source)

        self.panel.set_anti_consecutive_policy("off")
        self.assertEqual(self.panel.get_anti_consecutive_policy(), "off")
        cfg = self.panel.get_config()
        self.assertFalse(cfg.no_consecutive_same_source)


class TestMainWindowPlanInvalidation(unittest.TestCase):
    """Verifies that MainWindow correctly tracks plan staleness on configuration changes."""

    def setUp(self):
        self.win = MainWindow()

    def test_initial_state_stale(self):
        self.assertTrue(self.win._plan_is_stale)
        self.assertEqual(len(self.win.current_batch_plans), 0)

    def test_plan_invalidated_on_cut_config_change(self):
        # Simulate an existing active plan
        self.win._plan_is_stale = False
        self.win.current_batch_plans = [[MagicMock()]]

        # Trigger cut config change
        self.win._on_cut_config_changed()

        self.assertTrue(self.win._plan_is_stale)
        self.assertEqual(len(self.win.current_batch_plans), 0)
        self.assertIn("hết hạn", self.win.table_view.lbl_plan_summary.text())

    def test_plan_invalidated_on_audio_config_change(self):
        self.win._plan_is_stale = False
        self.win._on_audio_config_changed()
        self.assertTrue(self.win._plan_is_stale)

    def test_plan_invalidated_on_source_scanned(self):
        self.win._plan_is_stale = False
        self.win._on_source_scanned([])
        self.assertTrue(self.win._plan_is_stale)

    def test_plan_invalidated_on_build_or_shuffle_pool(self):
        self.win._plan_is_stale = False
        self.win.current_batch_plans = [[MagicMock()]]
        self.win._on_build_pool()
        self.assertTrue(self.win._plan_is_stale)
        self.assertEqual(len(self.win.current_batch_plans), 0)


class TestBatchAudioPlanningAndExecution(unittest.TestCase):
    """Verifies K-batch independent playlist generation and worker execution."""

    def setUp(self):
        self.win = MainWindow()
        # Mock 4 valid audio tracks in the audio pool
        tracks = [
            AudioInfo(path=f"/fake/track_{i}.mp3", filename=f"track_{i}.mp3", duration=60.0,
                      sample_rate=44100, channels=2, codec="mp3", size_bytes=1000)
            for i in range(4)
        ]
        from core.audio_pool_manager import AudioPool
        self.win.audio_pool = AudioPool(tracks)

    def test_batch_audio_planning_generates_k_playlists(self):
        playlists = self.win._generate_audio_playlists(batch_count=3)
        self.assertIsNotNone(playlists)
        self.assertEqual(len(playlists), 3)
        self.assertFalse(self.win._plan_is_stale)

    def test_worker_processes_all_audio_playlists(self):
        # Create a mock AudioProcessorEngine
        playlists = [
            [MagicMock(effective_duration=30.0)],
            [MagicMock(effective_duration=30.0)]
        ]
        out_cfg = OutputConfig(export_target="audio_only", output_dir=".")

        worker = VideoProcessorWorker(
            batch_plans=[],
            output_config=out_cfg,
            audio_playlists=playlists
        )

        started_batches = []
        started_videos = []
        completed_videos = []

        worker.batch_started.connect(lambda count: started_batches.append(count))
        worker.video_started.connect(lambda cur, tot, path: started_videos.append((cur, tot)))
        worker.video_completed.connect(lambda idx, path: completed_videos.append(idx))

        with patch("core.video_processor.AudioProcessorEngine") as MockAudioEngine, \
             patch("core.video_processor.get_next_output_path", return_value="dummy.mp3"):
            mock_inst = MagicMock()
            mock_inst._is_cancelled = False
            mock_inst.process_audio_playlist.return_value = True
            MockAudioEngine.return_value = mock_inst

            worker.run()

        self.assertEqual(started_batches, [2])
        self.assertEqual(started_videos, [(1, 2), (2, 2)])
        self.assertEqual(completed_videos, [1, 2])


class TestProcessRunnerEngineTermination(unittest.TestCase):
    """Verifies that engines call ProcessRunner.kill_process_tree on cancel."""

    @patch("core.process_runner.ProcessRunner.kill_process_tree")
    def test_video_engine_cancel_kills_process_tree(self, mock_kill):
        engine = VideoProcessorEngine(OutputConfig())
        fake_subproc = MagicMock()
        fake_subproc.pid = 9999
        engine._current_subprocess = fake_subproc

        engine.cancel()
        self.assertTrue(engine._is_cancelled)
        mock_kill.assert_called_once_with(9999)

    @patch("core.process_runner.ProcessRunner.kill_process_tree")
    def test_audio_engine_cancel_kills_process_tree(self, mock_kill):
        engine = AudioProcessorEngine()
        fake_subproc = MagicMock()
        fake_subproc.pid = 8888
        engine._current_subprocess = fake_subproc

        engine.cancel()
        self.assertTrue(engine._is_cancelled)
        mock_kill.assert_called_once_with(8888)


if __name__ == "__main__":
    unittest.main()
