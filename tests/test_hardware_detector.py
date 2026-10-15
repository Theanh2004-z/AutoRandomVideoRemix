"""
Unit test for GPU detection and encoder resolution (NVIDIA, AMD, Intel, CPU fallback).
"""
import unittest
from core.hardware_detector import detect_hardware, get_system_gpus, HardwareProfile
from core.config import OutputConfig
from core.video_processor import VideoProcessorEngine


class TestHardwareDetection(unittest.TestCase):
    def test_detect_hardware_profile(self):
        """Profile must return a valid active_encoder and non-empty primary GPU string."""
        profile = detect_hardware()
        self.assertIsInstance(profile, HardwareProfile)
        self.assertIn(profile.active_encoder, ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
        self.assertTrue(len(profile.primary_gpu_name) > 0)
        self.assertTrue(len(profile.encoder_name_display) > 0)

    def test_video_processor_engine_encoders(self):
        """Engine must resolve valid ffmpeg flags for auto and cpu modes."""
        # Auto mode
        cfg_auto = OutputConfig(hw_accel="auto", quality="high")
        engine_auto = VideoProcessorEngine(cfg_auto)
        enc_auto, opts_auto = engine_auto._determine_encoder()
        self.assertIn(enc_auto, ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"])
        self.assertIn("-c:v", opts_auto)

        # Force CPU mode
        cfg_cpu = OutputConfig(hw_accel="cpu", quality="high")
        engine_cpu = VideoProcessorEngine(cfg_cpu)
        enc_cpu, opts_cpu = engine_cpu._determine_encoder()
        self.assertEqual(enc_cpu, "libx264")
        self.assertIn("libx264", opts_cpu)

        # Force CPU fallback flag
        enc_fb, opts_fb = engine_auto._determine_encoder(force_cpu=True)
        self.assertEqual(enc_fb, "libx264")


if __name__ == "__main__":
    unittest.main()
