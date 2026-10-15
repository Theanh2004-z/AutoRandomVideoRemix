"""
Unit tests for C02 — Media Catalog, Snapshots, and Plan Manifest.
"""
import unittest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import VideoInfo, AudioInfo
from core.media_catalog import MediaCatalog, MediaAsset, CatalogSegment, CatalogSnapshot
from core.plan_manifest import PlanClip, PlanManifest


class TestV3CatalogManifest(unittest.TestCase):
    def setUp(self):
        self.v1 = VideoInfo(
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
        self.v2 = VideoInfo(
            path="C:/media/vid_B.mp4",
            filename="vid_B.mp4",
            duration=23.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            has_audio=True,
            size_bytes=400000,
            is_valid=True
        )

    def test_catalog_deterministic_snapshot(self):
        cat1 = MediaCatalog()
        cat1.add_video(self.v1)
        cat1.add_video(self.v2)
        snap1 = cat1.create_snapshot()

        cat2 = MediaCatalog()
        # Add in reverse order
        cat2.add_video(self.v2)
        cat2.add_video(self.v1)
        snap2 = cat2.create_snapshot()

        # Hashes must be identical regardless of insertion order
        self.assertEqual(snap1.snapshot_hash, snap2.snapshot_hash)
        self.assertEqual(len(snap1.assets), 2)

    def test_catalog_slicing_examples(self):
        cat = MediaCatalog()
        asset = MediaAsset.from_video_info(self.v2)  # 23.0s

        # 23s video with segment_duration=10.0 and merge_remainder=True -> 10s + 13s
        segs = cat.slice_asset(asset, segment_duration=10.0, fps=30.0, merge_remainder=True)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0].duration_sec, 10.0)
        self.assertAlmostEqual(segs[1].duration_sec, 13.0)
        self.assertEqual(segs[0].start_frame, 0)
        self.assertEqual(segs[0].end_frame, 300)
        self.assertEqual(segs[1].start_frame, 300)
        self.assertEqual(segs[1].end_frame, 690)

    def test_plan_clip_trimming(self):
        cat = MediaCatalog()
        asset = MediaAsset.from_video_info(self.v1)
        segs = cat.slice_asset(asset, segment_duration=10.0)
        first_seg = segs[0]

        # Regular clip
        clip = PlanClip.from_catalog_segment(first_seg, target_video_index=1, plan_position=1)
        self.assertFalse(clip.is_trimmed)
        self.assertEqual(clip.effective_duration, 10.0)

        # Trimmed clip (trimmed to 4.5s)
        trimmed = PlanClip.from_catalog_segment(first_seg, target_video_index=1, plan_position=2, trim_duration=4.5)
        self.assertTrue(trimmed.is_trimmed)
        self.assertEqual(trimmed.effective_duration, 4.5)
        self.assertEqual(trimmed.trim_end_sec, 4.5)

    def test_manifest_reproducibility_and_serialization(self):
        cat = MediaCatalog()
        cat.add_video(self.v1)
        snap = cat.create_snapshot()
        segs = cat.slice_all(snap, segment_duration=10.0)

        clips = [PlanClip.from_catalog_segment(s, target_video_index=1, plan_position=i+1) for i, s in enumerate(segs[:3])]
        manifest1 = PlanManifest.create(seed=1234, catalog_snapshot_hash=snap.snapshot_hash, video_batches=[clips])
        manifest2 = PlanManifest.create(seed=1234, catalog_snapshot_hash=snap.snapshot_hash, video_batches=[clips])

        self.assertEqual(manifest1.manifest_id, manifest2.manifest_id)

        # JSON serialization
        js = manifest1.to_json()
        parsed = json.loads(js)
        self.assertEqual(parsed["manifest_id"], manifest1.manifest_id)
        self.assertEqual(len(parsed["video_batches"][0]), 3)


if __name__ == "__main__":
    unittest.main()
