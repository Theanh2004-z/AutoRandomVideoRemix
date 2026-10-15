"""
Unit tests for C01 — Domain Models, Policies, Timebase Arithmetic, and Config Adapters.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.policies import UniquePolicy, AntiConsecutivePolicy, AudioMatchMode
from core.errors import ConfigValidationError
from core.timebase import (
    sec_to_frames,
    frames_to_sec,
    sec_to_samples,
    samples_to_sec,
    quantize_duration_to_frames,
    quantize_time_range_frames,
)
from core.models import NormalizedCutConfig, ConfigAdapterV2, AssetFingerprint
from core.config import CutConfig


class TestV3DomainModels(unittest.TestCase):
    # -------------------------------------------------------------
    # Timebase Arithmetic
    # -------------------------------------------------------------
    def test_timebase_sec_to_frames(self):
        self.assertEqual(sec_to_frames(1.0, 30.0), 30)
        self.assertEqual(sec_to_frames(0.5, 30.0), 15)
        self.assertEqual(sec_to_frames(1.0, 60.0), 60)
        self.assertEqual(sec_to_frames(0.0, 30.0), 0)

    def test_timebase_frames_to_sec(self):
        self.assertAlmostEqual(frames_to_sec(30, 30.0), 1.0)
        self.assertAlmostEqual(frames_to_sec(15, 30.0), 0.5)
        self.assertEqual(frames_to_sec(0, 30.0), 0.0)

    def test_timebase_audio_samples(self):
        self.assertEqual(sec_to_samples(1.0, 48000), 48000)
        self.assertAlmostEqual(samples_to_sec(48000, 48000), 1.0)
        self.assertEqual(sec_to_samples(0.5, 44100), 22050)

    def test_quantize_duration_to_frames_no_empty_clip(self):
        # A tiny fraction of a second must quantize to at least 1 frame, never 0
        q_dur = quantize_duration_to_frames(0.0001, 30.0)
        self.assertGreater(q_dur, 0.0)
        self.assertEqual(sec_to_frames(q_dur, 30.0), 1)

    def test_quantize_time_range_frames(self):
        q_start, q_end, frames = quantize_time_range_frames(0.0, 10.0, 30.0)
        self.assertEqual(q_start, 0.0)
        self.assertEqual(q_end, 10.0)
        self.assertEqual(frames, 300)

    # -------------------------------------------------------------
    # Config Validation
    # -------------------------------------------------------------
    def test_normalized_config_valid(self):
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=60.0,
            batch_count=1,
            unique_policy=UniquePolicy.UNIQUE_BATCH
        )
        cfg.validate()  # should not raise

    def test_normalized_config_invalid_duration(self):
        cfg = NormalizedCutConfig(segment_duration=-5.0)
        with self.assertRaises(ConfigValidationError):
            cfg.validate()

    def test_normalized_config_invalid_batch_count(self):
        cfg = NormalizedCutConfig(batch_count=0)
        with self.assertRaises(ConfigValidationError):
            cfg.validate()

    # -------------------------------------------------------------
    # Legacy V2.5 Adapter
    # -------------------------------------------------------------
    def test_adapter_allow_reuse_maps_to_cycle_output(self):
        legacy = CutConfig(allow_reuse=True, no_repeat_across_batch=True)
        v3_cfg = ConfigAdapterV2.from_v2_cut_config(legacy)
        self.assertEqual(v3_cfg.unique_policy, UniquePolicy.CYCLE_OUTPUT)

    def test_adapter_no_repeat_batch_maps_to_unique_batch(self):
        legacy = CutConfig(allow_reuse=False, no_repeat_across_batch=True)
        v3_cfg = ConfigAdapterV2.from_v2_cut_config(legacy)
        self.assertEqual(v3_cfg.unique_policy, UniquePolicy.UNIQUE_BATCH)

    def test_adapter_reuse_false_and_no_repeat_false_maps_to_unique_output(self):
        legacy = CutConfig(allow_reuse=False, no_repeat_across_batch=False)
        v3_cfg = ConfigAdapterV2.from_v2_cut_config(legacy)
        self.assertEqual(v3_cfg.unique_policy, UniquePolicy.UNIQUE_OUTPUT)

    def test_adapter_anti_consecutive_mapping(self):
        legacy_soft = CutConfig(no_consecutive_same_source=True)
        self.assertEqual(ConfigAdapterV2.from_v2_cut_config(legacy_soft).anti_consecutive_policy, AntiConsecutivePolicy.SOFT)

        legacy_off = CutConfig(no_consecutive_same_source=False)
        self.assertEqual(ConfigAdapterV2.from_v2_cut_config(legacy_off).anti_consecutive_policy, AntiConsecutivePolicy.OFF)


if __name__ == "__main__":
    unittest.main()
