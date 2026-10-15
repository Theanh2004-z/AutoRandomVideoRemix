"""
End-to-End Test for Audio & Video Processing with Synthetic Fixtures.
Runs 100% self-contained without requiring local user media.
"""
import os
import sys
import tempfile
import unittest

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from core.config import OutputConfig, AudioConfig, CutConfig
from core.video_scanner import probe_video_file
from core.audio_scanner import scan_audio_directory
from core.pool_manager import SegmentPool, BatchPlanner
from core.audio_pool_manager import AudioPool, AudioPicker
from core.video_processor import VideoProcessorEngine
from tests.fixtures_media import create_synthetic_video, create_synthetic_audio


class TestAudioMuxE2E(unittest.TestCase):
    def test_short_video_with_audio_mux(self):
        with tempfile.TemporaryDirectory() as td:
            video_file = os.path.join(td, "source_vid.mp4")
            audio_file = os.path.join(td, "source_aud.mp3")
            create_synthetic_video(video_file, duration=12.0, width=1280, height=720, fps=30)
            create_synthetic_audio(audio_file, duration=15.0)

            # 1. Probe source video
            vinfo = probe_video_file(video_file)
            self.assertTrue(vinfo.is_valid)

            # 2. Scan audio directory
            valid_audios, _ = scan_audio_directory(td)
            self.assertTrue(len(valid_audios) > 0)

            # 3. Build a small 10s video plan
            pool = SegmentPool()
            pool.build([vinfo], segment_duration=5.0, merge_remainder=True)
            self.assertTrue(pool.total_segments > 0)

            cut_cfg = CutConfig(segment_duration=5.0, target_total_duration=10.0, allow_reuse=True)
            planner = BatchPlanner(pool, cut_cfg)
            plans = planner.plan_all(10.0)
            plan = plans[0]

            # 4. Build audio playlist for 10s
            audio_pool = AudioPool(valid_audios)
            audio_cfg = AudioConfig(mode="fit_target")
            picker = AudioPicker(audio_pool, audio_cfg)
            playlist = picker.pick_playlist(target_duration=10.0)
            self.assertTrue(len(playlist) > 0)

            # 5. Process video with random_audio_pool
            out_file = os.path.join(td, "test_remix.mp4")
            out_cfg = OutputConfig(
                resolution="1280x720",
                fps=30,
                hw_accel="cpu",
                audio_mode="random_audio_pool"
            )

            engine = VideoProcessorEngine(
                output_config=out_cfg,
                on_log=lambda lvl, msg: None,
                on_progress=lambda pct, msg: None
            )

            success = engine.process_video_plan(
                plan=plan,
                output_file_path=out_file,
                video_index=1,
                total_videos=1,
                audio_playlist=playlist
            )

            self.assertTrue(success, "process_video_plan returned False")
            self.assertTrue(os.path.isfile(out_file), "Output file does not exist")

            # 6. Verify output has audio
            probe_res = probe_video_file(out_file)
            self.assertTrue(probe_res.is_valid)
            self.assertTrue(probe_res.has_audio, "CRITICAL: Output video does not have audio stream!")
            self.assertAlmostEqual(probe_res.duration, 10.0, delta=1.5)

    def test_standalone_audio_export(self):
        from core.audio_processor import AudioProcessorEngine

        with tempfile.TemporaryDirectory() as td:
            audio_file = os.path.join(td, "source_track.mp3")
            create_synthetic_audio(audio_file, duration=15.0)

            valid_audios, _ = scan_audio_directory(td)
            self.assertTrue(len(valid_audios) > 0)

            audio_pool = AudioPool(valid_audios)
            audio_cfg = AudioConfig(mode="fit_target")
            picker = AudioPicker(audio_pool, audio_cfg)
            playlist = picker.pick_playlist(target_duration=8.0)

            out_mp3 = os.path.join(td, "test_audio.mp3")
            engine = AudioProcessorEngine(on_log=lambda lvl, msg: None)
            ok = engine.process_audio_playlist(playlist, out_mp3, "mp3")
            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(out_mp3))
            self.assertTrue(os.path.getsize(out_mp3) > 1000)

    def test_gui_export_target_defaults_and_sync(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv)
        from ui.main_window import MainWindow
        w = MainWindow()

        try:
            # 1. Default should be video_with_random_audio
            self.assertEqual(w.audio_panel.get_export_target(), "video_with_random_audio")

            # 2. Area C combo_audio should be disabled with status message
            self.assertFalse(w.output_panel.combo_audio.isEnabled())
            self.assertIn("thư mục âm thanh", w.output_panel.lbl_audio_status.text())

            # 3. Switching to video_only in AudioPanel should re-enable combo_audio
            w.audio_panel.combo_export_target.setCurrentIndex(2)
            self.assertEqual(w.audio_panel.get_export_target(), "video_only")
            self.assertTrue(w.output_panel.combo_audio.isEnabled())

            # 4. Switching back to video_with_random_audio
            w.audio_panel.combo_export_target.setCurrentIndex(0)
            self.assertEqual(w.audio_panel.get_export_target(), "video_with_random_audio")
            self.assertFalse(w.output_panel.combo_audio.isEnabled())
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
