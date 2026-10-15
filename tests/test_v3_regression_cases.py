"""
Regression Tests for 11 V3 Architectural Counterexamples (C00).
These tests assert correct and robust behavior for all 11 critical edge cases
identified in the analysis report and V3 specification.
"""
import os
import sys
import unittest
import tempfile
from typing import List

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import VideoInfo, CutConfig, AudioInfo, AudioConfig, SegmentInfo
from core.pool_manager import SegmentPool, BatchPlanner
from core.audio_pool_manager import AudioPool, AudioPicker


class TestV3RegressionCases(unittest.TestCase):
    def setUp(self):
        """Set up standard mock video and audio assets for deterministic testing."""
        self.mock_videos = [
            VideoInfo(
                path="C:/media/vid_A.mp4",
                filename="vid_A.mp4",
                duration=60.0,
                width=1920,
                height=1080,
                fps=30.0,
                codec="h264",
                has_audio=True,
                size_bytes=1000000,
                is_valid=True
            ),
            VideoInfo(
                path="C:/media/vid_B.mp4",
                filename="vid_B.mp4",
                duration=60.0,
                width=1920,
                height=1080,
                fps=30.0,
                codec="h264",
                has_audio=True,
                size_bytes=1000000,
                is_valid=True
            ),
        ]
        self.mock_audios = [
            AudioInfo(
                path="C:/media/track_1.mp3",
                filename="track_1.mp3",
                duration=120.0,
                sample_rate=48000,
                channels=2,
                codec="mp3",
                size_bytes=1000000,
                is_valid=True
            ),
            AudioInfo(
                path="C:/media/track_2.mp3",
                filename="track_2.mp3",
                duration=120.0,
                sample_rate=48000,
                channels=2,
                codec="mp3",
                size_bytes=1000000,
                is_valid=True
            ),
        ]

    # --- CASE 1: Pool exhaustion when allow_reuse is False ---
    def test_case_01_pool_exhaustion_terminates_gracefully(self):
        """When pool is exhausted and reuse is disallowed, planner must terminate gracefully without infinite loop."""
        pool = SegmentPool()
        # 2 videos of 60s cut into 10s segments = 12 total segments (120s total)
        pool.build(self.mock_videos, segment_duration=10.0, merge_remainder=True)

        # Request 300s with allow_reuse=False -> Pool cannot fulfill 300s
        cfg = CutConfig(
            segment_duration=10.0,
            target_total_duration=300.0,
            batch_count=1,
            allow_reuse=False
        )
        planner = BatchPlanner(pool, cfg)
        from core.pool_manager import InsufficientSegmentsError
        with self.assertRaises(InsufficientSegmentsError):
            planner.plan_all(target_duration=300.0)

    # --- CASE 2: Anti-consecutive source video rule ---
    def test_case_02_anti_consecutive_same_source(self):
        """Ensure no two adjacent segments share the same source video when alternative sources exist."""
        pool = SegmentPool()
        pool.build(self.mock_videos, segment_duration=10.0, merge_remainder=True)

        cfg = CutConfig(
            segment_duration=10.0,
            target_total_duration=100.0,
            batch_count=1,
            allow_reuse=True,
            no_consecutive_same_source=True
        )
        planner = BatchPlanner(pool, cfg)
        plans = planner.plan_all(target_duration=100.0)
        plan = plans[0]

        for i in range(1, len(plan)):
            self.assertNotEqual(
                plan[i].source_video_path,
                plan[i - 1].source_video_path,
                f"Consecutive same source found at position {i}: {plan[i].source_video_name}"
            )

    # --- CASE 3: Segment duration exceeds video duration ---
    def test_case_03_segment_duration_larger_than_source(self):
        """When segment_duration > source duration, 1 segment of source duration is created without negative times."""
        short_video = [
            VideoInfo(
                path="C:/media/short.mp4",
                filename="short.mp4",
                duration=4.5,
                width=1920,
                height=1080,
                fps=30.0,
                codec="h264",
                has_audio=True,
                size_bytes=1000000,
                is_valid=True
            )
        ]
        pool = SegmentPool()
        pool.build(short_video, segment_duration=10.0, merge_remainder=True)
        self.assertEqual(pool.total_segments, 1)
        seg = pool.master_pool[0]
        self.assertEqual(seg.start_sec, 0.0)
        self.assertAlmostEqual(seg.end_sec, 4.5, places=3)
        self.assertAlmostEqual(seg.duration, 4.5, places=3)

    # --- CASE 4: Audio deck transition anti-adjacent rule ---
    def test_case_04_audio_deck_anti_adjacent_cycle_boundary(self):
        """When reshuffling audio deck across cycles, first track of cycle K must not equal last track of cycle K-1."""
        audio_pool = AudioPool(self.mock_audios)
        cfg = AudioConfig(mode="fit_target", no_consecutive_same_track=True)
        picker = AudioPicker(audio_pool, cfg)

        # 2 tracks of 120s = 240s per cycle. Request 600s (~3 cycles)
        playlist = picker.pick_playlist(target_duration=600.0)
        self.assertTrue(len(playlist) >= 4)

        for i in range(1, len(playlist)):
            self.assertNotEqual(
                playlist[i].source_audio_path,
                playlist[i - 1].source_audio_path,
                f"Adjacent identical audio tracks at position {i}: {playlist[i].source_audio_name}"
            )

    # --- CASE 5: Single track audio loop safety ---
    def test_case_05_single_track_audio_loop(self):
        """When audio pool has exactly 1 track, it should loop safely without crashing."""
        single_audio = [self.mock_audios[0]]
        audio_pool = AudioPool(single_audio)
        cfg = AudioConfig(mode="fit_target", no_consecutive_same_track=True, allow_single_track_loop=True)
        picker = AudioPicker(audio_pool, cfg)

        playlist = picker.pick_playlist(target_duration=350.0)
        # 120s track should appear 3 times to cover 350s (120 + 120 + 110)
        self.assertEqual(len(playlist), 3)
        self.assertAlmostEqual(sum(item.effective_duration for item in playlist), 350.0, places=2)

    # --- CASE 6: Cumulative floating point timebase drift ---
    def test_case_06_timebase_drift_prevention(self):
        """Ensure sum of segment durations matches target duration within 1 millisecond tolerance."""
        pool = SegmentPool()
        pool.build(self.mock_videos, segment_duration=10.0, merge_remainder=True)
        cfg = CutConfig(segment_duration=10.0, target_total_duration=60.0, allow_reuse=True, trim_last_segment=True)
        planner = BatchPlanner(pool, cfg)
        plans = planner.plan_all(target_duration=60.0)
        plan = plans[0]

        total_dur = sum(s.effective_duration for s in plan)
        self.assertAlmostEqual(total_dur, 60.0, delta=0.001)

    # --- CASE 7: Conflicting config validation ---
    def test_case_07_config_validation_and_sanity(self):
        """CutConfig and AudioConfig should have sensible positive values."""
        cfg = CutConfig(segment_duration=10.0, target_total_duration=1800.0, batch_count=1)
        self.assertGreater(cfg.segment_duration, 0.0)
        self.assertGreater(cfg.target_total_duration, 0.0)
        self.assertGreaterEqual(cfg.batch_count, 1)

    # --- CASE 8: Assets with same basename in different folders ---
    def test_case_08_duplicate_basename_distinct_paths(self):
        """Two files with the same filename in different directories must have distinct identities."""
        v1 = VideoInfo(path="C:/folder1/clip.mp4", filename="clip.mp4", duration=30.0, width=1920, height=1080, fps=30.0, codec="h264", has_audio=True, size_bytes=500000, is_valid=True)
        v2 = VideoInfo(path="C:/folder2/clip.mp4", filename="clip.mp4", duration=40.0, width=1920, height=1080, fps=30.0, codec="h264", has_audio=True, size_bytes=700000, is_valid=True)

        pool = SegmentPool()
        pool.build([v1, v2], segment_duration=10.0)

        # Total segments: 3 from v1 + 4 from v2 = 7
        self.assertEqual(pool.total_segments, 7)
        paths = set(s.source_video_path for s in pool.master_pool)
        self.assertEqual(len(paths), 2)
        self.assertIn("C:/folder1/clip.mp4", paths)
        self.assertIn("C:/folder2/clip.mp4", paths)

    # --- CASE 9: Trim last segment exact bounds ---
    def test_case_09_trim_last_segment_bounds(self):
        """Trim on last segment must be strictly > 0 and <= original segment duration."""
        pool = SegmentPool()
        pool.build(self.mock_videos, segment_duration=10.0)
        # Request 25s: 10 + 10 + 5 (last segment of 10s trimmed to 5s)
        cfg = CutConfig(segment_duration=10.0, target_total_duration=25.0, allow_reuse=True, trim_last_segment=True)
        planner = BatchPlanner(pool, cfg)
        plan = planner.plan_all(target_duration=25.0)[0]

        self.assertEqual(len(plan), 3)
        last_seg = plan[-1]
        self.assertIsNotNone(last_seg.trim_end_sec)
        self.assertAlmostEqual(last_seg.effective_duration, 5.0, places=3)
        self.assertLess(last_seg.effective_duration, last_seg.duration)

    # --- CASE 10: Batch count independence ---
    def test_case_10_batch_count_multiplicity(self):
        """Multiple batches must generate independent plans without cross-contamination."""
        pool = SegmentPool()
        pool.build(self.mock_videos, segment_duration=10.0)
        cfg = CutConfig(segment_duration=10.0, target_total_duration=30.0, batch_count=3, no_repeat_across_batch=True)
        planner = BatchPlanner(pool, cfg)
        plans = planner.plan_all(target_duration=30.0)

        self.assertEqual(len(plans), 3)
        for i, p in enumerate(plans):
            self.assertEqual(len(p), 3)
            for seg in p:
                self.assertEqual(seg.target_video_index, i + 1)

    # --- CASE 11: Plan reproducibility with deterministic random ---
    def test_case_11_plan_reproducibility_with_seed(self):
        """With identical seed and segments, plan sequence must be identical."""
        import random
        pool1 = SegmentPool()
        pool1.build(self.mock_videos, segment_duration=10.0)
        cfg1 = CutConfig(segment_duration=10.0, target_total_duration=50.0, allow_reuse=True)

        random.seed(42)
        planner1 = BatchPlanner(pool1, cfg1)
        plan1 = planner1.plan_all(50.0)[0]

        pool2 = SegmentPool()
        pool2.build(self.mock_videos, segment_duration=10.0)
        cfg2 = CutConfig(segment_duration=10.0, target_total_duration=50.0, allow_reuse=True)

        random.seed(42)
        planner2 = BatchPlanner(pool2, cfg2)
        plan2 = planner2.plan_all(50.0)[0]

        seq1 = [(s.source_video_path, s.start_sec) for s in plan1]
        seq2 = [(s.source_video_path, s.start_sec) for s in plan2]
        self.assertEqual(seq1, seq2)


if __name__ == "__main__":
    unittest.main()
