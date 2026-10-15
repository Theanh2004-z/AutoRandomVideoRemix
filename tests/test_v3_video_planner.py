"""
Unit tests for C03 — Video Planner Engine with Frame Timeline and Explicit Policies.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import VideoInfo
from core.media_catalog import MediaCatalog
from core.models import NormalizedCutConfig
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.errors import InsufficientSegmentsError
from core.video_planner import VideoPlanner


class TestV3VideoPlanner(unittest.TestCase):
    def setUp(self):
        self.cat = MediaCatalog()
        # Create 2 video sources: 60s each
        v1 = VideoInfo(
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
        )
        v2 = VideoInfo(
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
        )
        self.cat.add_video(v1)
        self.cat.add_video(v2)
        # Sliced into 10s segments = 6 + 6 = 12 total segments (120s total)
        self.segments = self.cat.slice_all(segment_duration=10.0, fps=30.0)

    def test_unique_batch_policy(self):
        # 12 segments = 120s total. Request 2 batches of 50s = 100s total.
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=50.0,
            batch_count=2,
            unique_policy=UniquePolicy.UNIQUE_BATCH,
            fps=30.0
        )
        planner = VideoPlanner(self.segments, cfg, seed=42)
        plans = planner.plan_batch()
        self.assertEqual(len(plans), 2)

        v1_segs = set(c.segment_id for c in plans[0])
        v2_segs = set(c.segment_id for c in plans[1])

        # Strict disjoint set between video 1 and video 2
        overlap = v1_segs.intersection(v2_segs)
        self.assertEqual(len(overlap), 0, f"Overlap found in UNIQUE_BATCH: {overlap}")

    def test_unique_batch_exhaustion_raises(self):
        # Request 2 batches of 70s = 140s total, but pool only has 120s
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=70.0,
            batch_count=2,
            unique_policy=UniquePolicy.UNIQUE_BATCH,
            fps=30.0
        )
        planner = VideoPlanner(self.segments, cfg, seed=42)
        with self.assertRaises(InsufficientSegmentsError):
            planner.plan_batch()

    def test_cycle_output_policy_allows_reuse(self):
        # Request 1 batch of 200s with pool of 120s -> CYCLE_OUTPUT should refill
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=200.0,
            batch_count=1,
            unique_policy=UniquePolicy.CYCLE_OUTPUT,
            fps=30.0
        )
        planner = VideoPlanner(self.segments, cfg, seed=42)
        plans = planner.plan_batch()
        self.assertEqual(len(plans), 1)
        plan = plans[0]

        total_dur = sum(c.effective_duration for c in plan)
        self.assertAlmostEqual(total_dur, 200.0, places=2)
        self.assertEqual(len(plan), 20)

    def test_anti_consecutive_soft_policy(self):
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=100.0,
            batch_count=1,
            unique_policy=UniquePolicy.CYCLE_OUTPUT,
            anti_consecutive_policy=AntiConsecutivePolicy.SOFT,
            fps=30.0
        )
        planner = VideoPlanner(self.segments, cfg, seed=99)
        plan = planner.plan_batch()[0]

        for i in range(1, len(plan)):
            self.assertNotEqual(
                plan[i].asset_id,
                plan[i - 1].asset_id,
                f"Consecutive same source found at {i}"
            )

    def test_trim_last_segment_exact_frames(self):
        # 10s segments, target 25s -> Last segment should be trimmed to exactly 5s (150 frames)
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=25.0,
            batch_count=1,
            trim_last_segment=True,
            fps=30.0
        )
        planner = VideoPlanner(self.segments, cfg, seed=1)
        plan = planner.plan_batch()[0]

        self.assertEqual(len(plan), 3)
        self.assertTrue(plan[-1].is_trimmed)
        self.assertAlmostEqual(plan[-1].effective_duration, 5.0, places=3)
        total_dur = sum(c.effective_duration for c in plan)
        self.assertAlmostEqual(total_dur, 25.0, places=3)

    def test_deterministic_reproducibility(self):
        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=40.0,
            batch_count=1,
            unique_policy=UniquePolicy.UNIQUE_BATCH,
            fps=30.0
        )
        planner1 = VideoPlanner(self.segments, cfg, seed=777)
        plan1 = planner1.plan_batch()[0]

        planner2 = VideoPlanner(self.segments, cfg, seed=777)
        plan2 = planner2.plan_batch()[0]

        clips1 = [c.clip_id for c in plan1]
        clips2 = [c.clip_id for c in plan2]
        self.assertEqual(clips1, clips2)


if __name__ == "__main__":
    unittest.main()
