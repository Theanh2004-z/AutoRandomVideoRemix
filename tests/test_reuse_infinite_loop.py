"""
Unit test to verify that RandomPicker and BatchPlanner never hit an infinite loop
even when target duration has floating-point precision differences and pool duration
is much shorter than target duration with allow_reuse=True.
"""
import unittest
from core.config import CutConfig, SegmentInfo
from core.pool_manager import SegmentPool, BatchPlanner, RandomPicker


class TestReuseLoopSafety(unittest.TestCase):
    def setUp(self):
        self.segs = []
        durations = [11.6, 17.1, 7.4, 14.9, 11.6, 22.4, 9.6, 9.1, 55.4, 23.7, 15.0, 22.1]
        for i, d in enumerate(durations):
            self.segs.append(
                SegmentInfo(
                    segment_id=f"seg_{i}",
                    segment_index=i + 1,
                    source_video_name=f"vid_{i}.mp4",
                    source_video_path=f"E:/vid_{i}.mp4",
                    start_sec=0.0,
                    end_sec=d,
                    duration=d,
                )
            )
        # Extra segments to simulate real pool
        for i in range(9):
            self.segs.append(
                SegmentInfo(
                    segment_id=f"seg_extra_{i}",
                    segment_index=12 + i + 1,
                    source_video_name=f"vid_{i % 12}.mp4",
                    source_video_path=f"E:/vid_{i % 12}.mp4",
                    start_sec=10.0,
                    end_sec=18.0,
                    duration=8.0,
                )
            )

        self.pool = SegmentPool()
        self.pool._master = self.segs
        self.pool._draw = list(self.segs)

    def test_precision_loop_safety(self):
        """Verify that floating point precision does not loop infinitely."""
        cfg = CutConfig(
            segment_duration=8.0,
            target_total_duration=600.0,
            batch_count=3,
            allow_reuse=True,
            no_repeat_across_batch=True,
            no_consecutive_same_source=True,
            trim_last_segment=True,
            merge_remainder=True,
        )
        planner = BatchPlanner(self.pool, cfg)
        plans = planner.plan_all(cfg.target_total_duration)
        self.assertEqual(len(plans), 3)
        for i, p in enumerate(plans):
            tot = sum(s.effective_duration for s in p)
            self.assertAlmostEqual(tot, 600.0, places=1)


if __name__ == "__main__":
    unittest.main()
