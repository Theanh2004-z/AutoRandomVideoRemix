"""
Unit tests for C05 — SQLite Persistence, Interval Overlap, Reservations, and Migration.
"""
import unittest
import sys
import os
import tempfile
import json
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.errors import HistoryConflictError
from core.app_paths import set_test_data_dir, reset_data_dir, get_history_db_path
from core.history_repository import HistoryRepository, CURRENT_SCHEMA_VERSION
from core.history_migration import migrate_legacy_history_json
from core.history_manager import HistoryManager
from core.plan_manifest import PlanClip
from core.media_catalog import CatalogSegment


class TestV3Persistence(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        set_test_data_dir(self.td)
        self.db_path = get_history_db_path()
        self.repo = HistoryRepository(self.db_path)

    def tearDown(self):
        reset_data_dir()

    def test_schema_init_and_version(self):
        self.assertTrue(os.path.isfile(self.db_path))
        with self.repo._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM schema_version;")
            v = cursor.fetchone()[0]
            self.assertEqual(v, CURRENT_SCHEMA_VERSION)

    def test_interval_overlap_detection(self):
        asset_id = "asset_vid_123"
        self.repo.register_asset(asset_id, "C:/vids/v1.mp4", "v1.mp4", 100000, 60.0)

        # Record interval [10.0, 20.0]
        clip = PlanClip(
            clip_id="c1", segment_id="s1", asset_id=asset_id,
            source_path="C:/vids/v1.mp4", source_filename="v1.mp4",
            start_frame=300, end_frame=600, frame_count=300,
            start_sec=10.0, end_sec=20.0, effective_duration=10.0,
            target_video_index=1, plan_position=1
        )
        self.repo.commit_job_history("job_1", [clip])
        self.assertEqual(self.repo.get_total_records(), 1)

        # 1. Overlapping intervals:
        # [15.0, 25.0] overlaps [10.0, 20.0]
        self.assertTrue(self.repo.is_interval_used(asset_id, 15.0, 25.0))
        # [5.0, 15.0] overlaps [10.0, 20.0]
        self.assertTrue(self.repo.is_interval_used(asset_id, 5.0, 15.0))
        # [12.0, 18.0] completely inside
        self.assertTrue(self.repo.is_interval_used(asset_id, 12.0, 18.0))

        # 2. Non-overlapping intervals:
        # [0.0, 9.9] does not overlap
        self.assertFalse(self.repo.is_interval_used(asset_id, 0.0, 9.9))
        # [20.1, 30.0] does not overlap
        self.assertFalse(self.repo.is_interval_used(asset_id, 20.1, 30.0))
        # Different asset does not overlap
        self.assertFalse(self.repo.is_interval_used("other_asset", 10.0, 20.0))

    def test_reservation_locking_and_conflict(self):
        asset_id = "asset_vid_456"
        clip_job1 = PlanClip(
            clip_id="c_j1", segment_id="s_j1", asset_id=asset_id,
            source_path="C:/v.mp4", source_filename="v.mp4",
            start_frame=0, end_frame=300, frame_count=300,
            start_sec=0.0, end_sec=10.0, effective_duration=10.0,
            target_video_index=1, plan_position=1
        )

        # Job 1 reserves [0.0, 10.0]
        ok = self.repo.reserve_batch("job_A", [clip_job1])
        self.assertTrue(ok)

        # Job 2 attempts to reserve overlapping interval [5.0, 15.0] -> Must fail!
        clip_job2 = PlanClip(
            clip_id="c_j2", segment_id="s_j2", asset_id=asset_id,
            source_path="C:/v.mp4", source_filename="v.mp4",
            start_frame=150, end_frame=450, frame_count=300,
            start_sec=5.0, end_sec=15.0, effective_duration=10.0,
            target_video_index=1, plan_position=1
        )
        ok_job2 = self.repo.reserve_batch("job_B", [clip_job2])
        self.assertFalse(ok_job2, "Concurrent reservation should have been blocked!")

        # After Job 1 releases reservations, Job 2 can now reserve!
        self.repo.release_reservations("job_A")
        ok_retry = self.repo.reserve_batch("job_B", [clip_job2])
        self.assertTrue(ok_retry)

    def test_legacy_json_migration(self):
        json_path = os.path.join(self.td, "legacy_history.json")
        sample_records = [
            {"source_video_name": "test1.mp4", "start_sec": 0.0, "end_sec": 10.0, "exported_at": 1700000000.0},
            {"source_video_name": "test2.mp4", "start_sec": 10.0, "end_sec": 20.0, "exported_at": 1700000000.0},
        ]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(sample_records, f)

        res = migrate_legacy_history_json(json_path, self.repo)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["migrated_count"], 2)
        self.assertTrue(os.path.isfile(res["backup_path"]))

        # Database should now have 2 records
        self.assertEqual(self.repo.get_total_records(), 2)

    def test_history_manager_backward_compatibility(self):
        hm = HistoryManager(
            history_file=os.path.join(self.td, "export_history.json"),
            db_path=os.path.join(self.td, "hm_test.db")
        )
        from core.config import SegmentInfo
        seg = SegmentInfo(
            segment_id="seg_001",
            source_video_path="C:/vids/sample.mp4",
            source_video_name="sample.mp4",
            start_sec=0.0,
            end_sec=10.0,
            duration=10.0,
            segment_index=1
        )
        added = hm.record_exported_segments([seg])
        self.assertEqual(added, 1)
        self.assertEqual(hm.get_history_count(), 1)

        # Clear history
        self.assertTrue(hm.clear_history())
        self.assertEqual(hm.get_history_count(), 0)


if __name__ == "__main__":
    unittest.main()
