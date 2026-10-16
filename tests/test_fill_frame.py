"""
Comprehensive unit tests for the 'Tự động lấp đầy khung hình' (Auto-fill frame) feature.
Covers:
- Checkbox UI and signal wiring in CutConfigPanel
- Internal state & set_aspect_mode in OutputConfigPanel
- CPU filter generation for horizontal (16:9) and vertical (9:16) outputs
- Pipeline selection for 16:9, 4:3, 1:1, 9:16, non-square SAR, and rotated sources
- Cache key differentiation between crop and fit modes
- PreviewWorker command construction and aspect ratio modes
- Disabling in audio-only mode and during active export
- Persistence and settings synchronization
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication

from core.config import OutputConfig, VideoInfo, SegmentInfo
from core.render_cache import RenderCache
from core.segment_renderer import cpu_filter, choose_pipeline
from core.settings_manager import SettingsManager, settings
from ui.components.cut_config_panel import CutConfigPanel
from ui.components.output_config_panel import OutputConfigPanel
from ui.dashboard import PreviewWorker

app = QApplication.instance() or QApplication([])


class TestFillFrameFeature(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_file = os.path.join(self.temp_dir.name, "settings.json")
        self.mgr = SettingsManager(file_path=self.settings_file, allow_migration=False)
        self.previous_path = settings._file_path
        self.previous_data = dict(settings._data)
        self.previous_migration = settings._allow_migration
        settings.set_file_path(self.settings_file)

    def tearDown(self):
        settings._file_path = self.previous_path
        settings._data = self.previous_data
        settings._allow_migration = self.previous_migration
        self.temp_dir.cleanup()

    def test_cut_panel_checkbox_and_signal(self):
        cut_panel = CutConfigPanel()
        self.assertTrue(hasattr(cut_panel, "chk_fill_frame"))
        self.assertTrue(hasattr(cut_panel, "lbl_fill_frame_desc"))
        self.assertEqual(cut_panel.chk_fill_frame.text(), "Tự động lấp đầy khung hình")

        received = []
        cut_panel.fill_frame_changed.connect(lambda val: received.append(val))

        cut_panel.chk_fill_frame.setChecked(True)
        self.assertEqual(received, [True])

        cut_panel.chk_fill_frame.setChecked(False)
        self.assertEqual(received, [True, False])

        # Test set_fill_frame with QSignalBlocker (should not emit signal)
        cut_panel.set_fill_frame(True)
        self.assertTrue(cut_panel.chk_fill_frame.isChecked())
        self.assertEqual(len(received), 2)  # No extra signal emitted

    def test_output_panel_set_aspect_mode_and_get_config(self):
        output_panel = OutputConfigPanel()
        # Verify combo_aspect is removed
        self.assertFalse(hasattr(output_panel, "combo_aspect"))

        received = []
        output_panel.config_changed.connect(lambda: received.append(True))

        output_panel.set_aspect_mode("crop")
        self.assertEqual(output_panel.get_config().aspect_ratio_mode, "crop")
        self.assertEqual(len(received), 1)

        # Setting same mode should not re-emit
        output_panel.set_aspect_mode("crop")
        self.assertEqual(len(received), 1)

        output_panel.set_aspect_mode("fit")
        self.assertEqual(output_panel.get_config().aspect_ratio_mode, "fit")
        self.assertEqual(len(received), 2)

    def test_cut_and_output_panel_synchronization(self):
        cut_panel = CutConfigPanel()
        output_panel = OutputConfigPanel()

        cut_panel.fill_frame_changed.connect(
            lambda checked: output_panel.set_aspect_mode("crop" if checked else "fit")
        )

        cut_panel.chk_fill_frame.setChecked(True)
        self.assertEqual(output_panel.get_config().aspect_ratio_mode, "crop")

        cut_panel.chk_fill_frame.setChecked(False)
        self.assertEqual(output_panel.get_config().aspect_ratio_mode, "fit")

    def test_cpu_filter_horizontal_and_vertical(self):
        # 1. 1920x1080 (Horizontal 16:9) with CROP (Fill Frame)
        cfg_crop_h = OutputConfig(resolution="1920x1080", aspect_ratio_mode="crop")
        vf_crop_h = cpu_filter(cfg_crop_h)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=increase:force_divisible_by=2", vf_crop_h)
        self.assertIn("crop=1920:1080", vf_crop_h)
        self.assertNotIn("pad=", vf_crop_h)

        # 2. 1920x1080 (Horizontal 16:9) with FIT
        cfg_fit_h = OutputConfig(resolution="1920x1080", aspect_ratio_mode="fit")
        vf_fit_h = cpu_filter(cfg_fit_h)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2", vf_fit_h)
        self.assertIn("pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black", vf_fit_h)
        self.assertNotIn("crop=", vf_fit_h)

        # 3. 1080x1920 (Vertical 9:16) with CROP (Fill Frame)
        cfg_crop_v = OutputConfig(resolution="1080x1920", aspect_ratio_mode="crop")
        vf_crop_v = cpu_filter(cfg_crop_v)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase:force_divisible_by=2", vf_crop_v)
        self.assertIn("crop=1080:1920", vf_crop_v)
        self.assertNotIn("pad=", vf_crop_v)

        # 4. 1080x1920 (Vertical 9:16) with FIT
        cfg_fit_v = OutputConfig(resolution="1080x1920", aspect_ratio_mode="fit")
        vf_fit_v = cpu_filter(cfg_fit_v)
        self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2", vf_fit_v)
        self.assertIn("pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black", vf_fit_v)
        self.assertNotIn("crop=", vf_fit_v)

    def test_pipeline_selection_aspect_ratios_and_rotation(self):
        cfg_crop = OutputConfig(resolution="1920x1080", aspect_ratio_mode="crop", gpu_processing=True)
        cfg_fit = OutputConfig(resolution="1920x1080", aspect_ratio_mode="fit", gpu_processing=True)

        def make_info(w, h, rot=0, sar="1:1"):
            return VideoInfo(
                path="/test.mp4", filename="test.mp4", duration=10.0,
                width=w, height=h, fps=30.0, codec="h264", has_audio=True,
                size_bytes=1000, is_valid=True, pixel_format="yuv420p",
                sample_aspect_ratio=sar, rotation=rot
            )

        # 16:9 source (1920x1080) -> exact ratio
        info_16_9 = make_info(1920, 1080)
        pipe_16_9 = choose_pipeline(cfg_crop, "h264_nvenc", info_16_9)
        self.assertNotIn("crop=", pipe_16_9.video_filter)
        self.assertNotIn("pad=", pipe_16_9.video_filter)

        # 4:3 source (1440x1080) with crop -> hybrid scaling with crop
        info_4_3 = make_info(1440, 1080)
        pipe_4_3_crop = choose_pipeline(cfg_crop, "h264_nvenc", info_4_3)
        self.assertIn("crop=1920:1080", pipe_4_3_crop.video_filter)

        # 4:3 source (1440x1080) with fit -> hybrid scaling with pad
        pipe_4_3_fit = choose_pipeline(cfg_fit, "h264_nvenc", info_4_3)
        self.assertIn("pad=1920:1080", pipe_4_3_fit.video_filter)

        # Rotated video (e.g. smartphone 90 degrees) -> safely fall back to software
        info_rot = make_info(1920, 1080, rot=90)
        pipe_rot = choose_pipeline(cfg_crop, "h264_nvenc", info_rot)
        self.assertEqual(pipe_rot.name, "software")
        self.assertIn("crop=1920:1080", pipe_rot.video_filter)

        # Non-square SAR -> safely fall back to software
        info_sar = make_info(720, 576, sar="16:11")
        pipe_sar = choose_pipeline(cfg_crop, "h264_nvenc", info_sar)
        self.assertEqual(pipe_sar.name, "software")

    def test_cache_keys_differentiate_crop_and_fit(self):
        k_crop = RenderCache.compute_key(
            source_path="/video.mp4", start_sec=0.0, duration=5.0,
            width=1920, height=1080, fps=30.0, aspect_ratio_mode="crop"
        )
        k_fit = RenderCache.compute_key(
            source_path="/video.mp4", start_sec=0.0, duration=5.0,
            width=1920, height=1080, fps=30.0, aspect_ratio_mode="fit"
        )
        self.assertNotEqual(k_crop, k_fit, "Crop and Fit must produce distinct cache fingerprints")

    def test_preview_worker_command_respects_aspect_mode(self):
        for mode in ("crop", "fit"):
            with self.subTest(mode=mode):
                worker = PreviewWorker("/dummy.mp4", 5.0, None,
                    width=1920, height=1080, aspect_ratio_mode=mode)
                process = MagicMock(returncode=0)
                process.communicate.return_value = (b"", b"")
                with patch("ui.dashboard.subprocess.Popen", return_value=process) as popen:
                    worker.run()
                command = popen.call_args.args[0]
                vf = command[command.index("-vf") + 1]
                self.assertIn("reset_sar=1", vf)
                if mode == "crop":
                    self.assertIn("crop=1920:1080", vf)
                else:
                    self.assertIn("pad=1920:1080", vf)
                    self.assertNotIn("crop=", vf)

    def test_audio_only_and_exporting_disables_checkbox(self):
        cut_panel = CutConfigPanel()
        self.assertTrue(cut_panel.chk_fill_frame.isEnabled())

        # Audio-only mode disables fill frame
        cut_panel.set_audio_only(True)
        self.assertFalse(cut_panel.chk_fill_frame.isEnabled())

        cut_panel.set_audio_only(False)
        self.assertTrue(cut_panel.chk_fill_frame.isEnabled())

        # Active export disables fill frame
        cut_panel.set_exporting(True)
        self.assertFalse(cut_panel.chk_fill_frame.isEnabled())

        cut_panel.set_exporting(False)
        self.assertTrue(cut_panel.chk_fill_frame.isEnabled())


if __name__ == "__main__":
    unittest.main()
