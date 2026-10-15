"""
V3 Render Cache, Disk Estimator, and Scaling Benchmark Tests (Đợt 6 - C09).
Verifies:
- RenderCache key stability, atomic storage, hit/miss, and LRU eviction.
- DiskEstimator bitrate mapping, safety overhead calculation, and pre-flight space verification.
- High-scale performance: VideoPlanner and cache key generation across 1,000+ segments.
"""
import os
import time
import shutil
import tempfile
import unittest
from unittest.mock import patch

from core.render_cache import RenderCache
from core.disk_estimator import DiskEstimator, format_bytes, DiskSpaceCheckResult
from core.errors import InsufficientDiskSpaceError
from core.media_catalog import CatalogSegment
from core.models import NormalizedCutConfig
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.video_planner import VideoPlanner


class TestRenderCache(unittest.TestCase):
    """Verifies content-addressable render cache and LRU eviction."""

    def setUp(self):
        self.temp_root = tempfile.mkdtemp(prefix="remix_test_cache_")
        self.cache_dir = os.path.join(self.temp_root, "cache")
        self.source_dir = os.path.join(self.temp_root, "sources")
        os.makedirs(self.cache_dir, exist_ok=True)
        os.makedirs(self.source_dir, exist_ok=True)
        self.cache = RenderCache(cache_dir=self.cache_dir, max_size_bytes=1024 * 1024, enabled=True)

    def tearDown(self):
        shutil.rmtree(self.temp_root, ignore_errors=True)

    def test_compute_key_deterministic(self):
        k1 = RenderCache.compute_key(
            source_path="/videos/test.mp4",
            start_sec=10.0,
            duration=5.0,
            width=1920,
            height=1080,
            fps=30.0,
            source_size=10000,
            source_mtime=1234567.0
        )
        k2 = RenderCache.compute_key(
            source_path="/videos/test.mp4",
            start_sec=10.0,
            duration=5.0,
            width=1920,
            height=1080,
            fps=30.0,
            source_size=10000,
            source_mtime=1234567.0
        )
        self.assertEqual(k1, k2)

        # Different duration -> different key
        k3 = RenderCache.compute_key(
            source_path="/videos/test.mp4",
            start_sec=10.0,
            duration=6.0,
            width=1920,
            height=1080,
            fps=30.0,
            source_size=10000,
            source_mtime=1234567.0
        )
        self.assertNotEqual(k1, k3)

    def test_cache_put_and_get(self):
        dummy_file = os.path.join(self.source_dir, "test_render.mp4")
        with open(dummy_file, "wb") as f:
            f.write(b"SAMPLE_VIDEO_DATA_BYTES")

        key = "test_key_abc123"
        stored_path = self.cache.put(key, dummy_file)
        self.assertIsNotNone(stored_path)
        self.assertTrue(os.path.isfile(stored_path))

        # Retrieve
        hit = self.cache.get(key)
        self.assertEqual(hit, stored_path)
        with open(hit, "rb") as f:
            self.assertEqual(f.read(), b"SAMPLE_VIDEO_DATA_BYTES")

    def test_cache_miss(self):
        self.assertIsNone(self.cache.get("non_existent_key"))

    def test_lru_eviction(self):
        # Set small limit of 500 bytes
        small_cache = RenderCache(cache_dir=self.cache_dir, max_size_bytes=500, enabled=True)

        f1 = os.path.join(self.source_dir, "f1.mp4")
        with open(f1, "wb") as f:
            f.write(b"A" * 300)

        f2 = os.path.join(self.source_dir, "f2.mp4")
        with open(f2, "wb") as f:
            f.write(b"B" * 300)

        small_cache.put("key1", f1)
        time.sleep(0.01)
        small_cache.put("key2", f2)

        # Total size would be 600 > 500. key1 (oldest) should have been evicted
        self.assertIsNone(small_cache.get("key1"))
        self.assertIsNotNone(small_cache.get("key2"))

    def test_cache_clear_and_stats(self):
        f = os.path.join(self.source_dir, "f.mp4")
        with open(f, "wb") as file:
            file.write(b"HELLO_CACHE")
        self.cache.put("k1", f)

        stats = self.cache.get_stats()
        self.assertEqual(stats["file_count"], 1)
        self.assertGreater(stats["total_bytes"], 0)

        del_count = self.cache.clear()
        self.assertEqual(del_count, 1)
        self.assertEqual(self.cache.get_stats()["file_count"], 0)


class TestDiskEstimator(unittest.TestCase):
    """Verifies storage estimation and pre-flight checks."""

    def test_format_bytes(self):
        self.assertEqual(format_bytes(0), "0.00 B")
        self.assertEqual(format_bytes(1024), "1.00 KB")
        self.assertEqual(format_bytes(1024 * 1024), "1.00 MB")
        self.assertEqual(format_bytes(1024 * 1024 * 1024), "1.00 GB")

    def test_bitrate_resolution_scaling(self):
        self.assertEqual(DiskEstimator.determine_video_bitrate(3840, 2160), 28_000_000)
        self.assertEqual(DiskEstimator.determine_video_bitrate(2560, 1440), 16_000_000)
        self.assertEqual(DiskEstimator.determine_video_bitrate(1920, 1080), 8_000_000)
        self.assertEqual(DiskEstimator.determine_video_bitrate(1280, 720), 4_500_000)
        self.assertEqual(DiskEstimator.determine_video_bitrate(640, 480), 2_000_000)

    def test_estimate_required_bytes(self):
        # 60s of 1080p video: (8 Mbps + 192 kbps) = 8.192 Mbps -> ~1 MB/s -> 60 MB raw * 2.0 safety factor = ~122 MB
        est = DiskEstimator.estimate_required_bytes(total_duration_sec=60.0, width=1920, height=1080)
        self.assertGreater(est, 100 * 1024 * 1024)
        self.assertLess(est, 150 * 1024 * 1024)

    @patch("shutil.disk_usage")
    def test_check_disk_space_sufficient(self, mock_usage):
        # 10 GB available
        from collections import namedtuple
        Usage = namedtuple("Usage", ["total", "used", "free"])
        mock_usage.return_value = Usage(total=50*1024**3, used=40*1024**3, free=10*1024**3)

        res = DiskEstimator.check_disk_space("C:/dummy", required_bytes=1024**3)
        self.assertTrue(res.is_sufficient)
        self.assertIn("Đủ dung lượng", res.message)

    @patch("shutil.disk_usage")
    def test_check_disk_space_insufficient(self, mock_usage):
        # Only 200 MB free, but requires 1 GB + 500 MB headroom
        from collections import namedtuple
        Usage = namedtuple("Usage", ["total", "used", "free"])
        mock_usage.return_value = Usage(total=50*1024**3, used=49.8*1024**3, free=200*1024**2)

        res = DiskEstimator.check_disk_space("C:/dummy", required_bytes=1024**3)
        self.assertFalse(res.is_sufficient)
        self.assertIn("Không đủ dung lượng", res.message)

        with self.assertRaises(InsufficientDiskSpaceError):
            DiskEstimator.verify_disk_space("C:/dummy", required_bytes=1024**3)


class TestScalingAndPerformance(unittest.TestCase):
    """High-load benchmark: 1,000+ segments scaling verification."""

    def test_planner_scaling_1000_segments(self):
        # Generate 1,200 synthetic CatalogSegment items across 20 source videos
        segments = []
        for i in range(1200):
            vid_idx = i % 20
            seg = CatalogSegment(
                segment_id=f"seg_{i:04d}",
                asset_id=f"asset_{vid_idx:02d}",
                source_path=f"E:/sources/video_{vid_idx:02d}.mp4",
                source_filename=f"video_{vid_idx:02d}.mp4",
                start_frame=0,
                end_frame=300,
                frame_count=300,
                start_sec=0.0,
                end_sec=10.0,
                duration_sec=10.0
            )
            segments.append(seg)

        cfg = NormalizedCutConfig(
            segment_duration=10.0,
            target_total_duration=1800.0,  # 30 min = 180 clips
            batch_count=5,                 # 5 batches = 900 clips total
            unique_policy=UniquePolicy.UNIQUE_BATCH,
            anti_consecutive_policy=AntiConsecutivePolicy.SOFT
        )

        planner = VideoPlanner(segments, cfg, seed=42)
        start_t = time.perf_counter()
        plans = planner.plan_batch()
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0

        self.assertEqual(len(plans), 5)
        self.assertEqual(len(plans[0]), 180)
        # 1,200 segments planned across 5 videos must execute within 500ms
        self.assertLess(elapsed_ms, 500.0, f"Planner too slow: took {elapsed_ms:.2f}ms")

    def test_cache_key_generation_performance(self):
        start_t = time.perf_counter()
        for i in range(1000):
            RenderCache.compute_key(
                source_path=f"E:/sources/video_{i%10}.mp4",
                start_sec=float(i),
                duration=10.0,
                width=1920,
                height=1080,
                fps=30.0,
                source_size=500000,
                source_mtime=1700000000.0
            )
        elapsed_ms = (time.perf_counter() - start_t) * 1000.0
        # 1,000 hash calculations must execute well within 100ms
        self.assertLess(elapsed_ms, 100.0, f"Hash too slow: took {elapsed_ms:.2f}ms")


if __name__ == "__main__":
    unittest.main()
