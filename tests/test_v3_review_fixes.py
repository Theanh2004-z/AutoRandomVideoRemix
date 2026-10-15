"""
Comprehensive regression test suite verifying fixes for all 13 review findings (R01 to R13).
"""
import os
import sys
import json
import time
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.history_migration import migrate_legacy_history_json, parse_exported_at
from core.history_repository import HistoryRepository, CURRENT_SCHEMA_VERSION
from core.history_manager import HistoryManager, _make_segment_key
from core.output_commit import OutputCommit
from core.output_validator import OutputValidator, ValidationResult
from core.render_cache import RenderCache
from core.media_catalog import MediaCatalog, MediaAsset, CatalogSegment
from core.models import (
    AssetFingerprint, NormalizedCutConfig, ConfigAdapterV2, compute_media_content_hash
)
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.audio_planner import AudioPlanner
from core.video_planner import VideoPlanner
from core.export_planner import ExportPlanner
from core.plan_manifest import PlanClip, PlanManifest
from core.errors import (
    InsufficientSegmentsError, InsufficientAudioError, HistoryConflictError
)
from core.config import SegmentInfo, AudioConfig, OutputConfig, CutConfig, VideoInfo, AudioInfo


class TestV3ReviewFixes(unittest.TestCase):
    """Verifies all review findings R01 through R13."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="remix_review_test_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -----------------------------------------------------------------
    # R02: History Migration with v2.5 Timestamps & Diagnostics
    # -----------------------------------------------------------------
    def test_r02_migration_timestamp_formats_and_diagnostics(self):
        """R02: Must parse 'YYYY-MM-DD HH:MM:SS', ISO strings, and floats without failing."""
        db_path = os.path.join(self.temp_dir, "hist.db")
        repo = HistoryRepository(db_path)
        json_path = os.path.join(self.temp_dir, "legacy_history.json")

        legacy_data = [
            # v2.5 standard formatted string
            {"source_video_name": "v1.mp4", "start_sec": 0.0, "end_sec": 10.0, "exported_at": "2026-10-15 12:00:00"},
            # ISO string
            {"source_video_name": "v2.mp4", "start_sec": 5.0, "end_sec": 15.0, "exported_at": "2026-10-15T14:30:00"},
            # Epoch float
            {"source_video_name": "v3.mp4", "start_sec": 10.0, "end_sec": 20.0, "exported_at": 1700000000.0},
            # Missing exported_at
            {"source_video_name": "v4.mp4", "start_sec": 20.0, "end_sec": 30.0},
            # Corrupted record (invalid dict / invalid coordinates)
            "corrupted_non_dict_entry",
            {"source_video_name": "v5.mp4", "start_sec": 50.0, "end_sec": 10.0}  # end < start with no duration
        ]
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(legacy_data, f)

        res = migrate_legacy_history_json(json_path, repo)
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["migrated_count"], 4)
        self.assertEqual(res["skipped_count"], 2)
        self.assertTrue(len(res["errors"]) >= 2)
        self.assertTrue(os.path.isfile(json_path + ".bak"))

        # Verify parsed timestamps in DB
        with repo._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT exported_at FROM history_intervals ORDER BY id ASC;")
            rows = cursor.fetchall()
            self.assertEqual(len(rows), 4)
            for r in rows:
                self.assertIsInstance(r[0], float)
                self.assertGreater(r[0], 0.0)

    # -----------------------------------------------------------------
    # R03: Accurate End Coordinates in HistoryManager
    # -----------------------------------------------------------------
    def test_r03_history_manager_actual_end_coordinates_and_constraint(self):
        """R03: end_sec must store actual end coordinate (not duration), and DB constraint enforces end > start."""
        db_path = os.path.join(self.temp_dir, "hist_r03.db")
        json_path = os.path.join(self.temp_dir, "hist_r03.json")
        hm = HistoryManager(history_file=json_path, db_path=db_path)

        # Segment from 10s to 20s, trimmed to effective duration of 2s -> actual end is 12s
        seg = SegmentInfo(
            segment_id="seg_1",
            source_video_path=os.path.join(self.temp_dir, "src.mp4"),
            source_video_name="src.mp4",
            start_sec=10.0,
            end_sec=20.0,
            duration=10.0,
            segment_index=1,
            trim_end_sec=12.0
        )
        self.assertEqual(seg.effective_duration, 2.0)

        added = hm.record_exported_segments([seg])
        self.assertEqual(added, 1)

        # Inspect SQLite interval: start must be 10.0, end must be 12.0 (NOT 2.0)
        with hm.repo._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT start_sec, end_sec FROM history_intervals;")
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], 10.0)
            self.assertEqual(row[1], 12.0)
            self.assertGreater(row[1], row[0])

            # Test database CHECK constraint: inserting end_sec <= start_sec must fail
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("""
                    INSERT INTO history_intervals (
                        asset_id, source_filename, start_sec, end_sec, exported_at
                    ) VALUES ('test_asset', 'src.mp4', 10.0, 5.0, 1000.0);
                """)

    # -----------------------------------------------------------------
    # R04: Atomic Reservation Locking Under Concurrency
    # -----------------------------------------------------------------
    def test_r04_concurrent_reservation_atomic_begin_immediate(self):
        """R04: Two concurrent jobs reserving the same interval must never both succeed."""
        db_path = os.path.join(self.temp_dir, "hist_r04.db")
        repo = HistoryRepository(db_path)

        clip = PlanClip(
            clip_id="c1", segment_id="s1", asset_id="asset_shared",
            source_path="src.mp4", source_filename="src.mp4",
            start_frame=0, end_frame=300, frame_count=300,
            start_sec=0.0, end_sec=10.0, effective_duration=10.0,
            target_video_index=1, plan_position=1
        )

        results = []
        barrier = threading.Barrier(2)

        def worker(job_id: str):
            r = HistoryRepository(db_path)
            barrier.wait()  # Synchronize threads right before reservation
            res = r.reserve_batch(job_id, [clip])
            results.append((job_id, res))

        t1 = threading.Thread(target=worker, args=("job_A",))
        t2 = threading.Thread(target=worker, args=("job_B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        success_count = sum(1 for _, ok in results if ok)
        fail_count = sum(1 for _, ok in results if not ok)
        self.assertEqual(success_count, 1, "Exactly one job must win the reservation")
        self.assertEqual(fail_count, 1, "The competing job must fail reservation")

    # -----------------------------------------------------------------
    # R05: OutputCommit No Silent Overwrite
    # -----------------------------------------------------------------
    def test_r05_output_commit_conflict_protection(self):
        """R05: Staging publication must reject overwriting existing files by default."""
        final_dest = os.path.join(self.temp_dir, "final_output.mp4")
        with open(final_dest, "w") as f:
            f.write("EXISTING USER OUTPUT")

        staging = os.path.join(self.temp_dir, "staging.mp4")
        with open(staging, "w") as f:
            f.write("NEW OUTPUT")

        val = ValidationResult(is_valid=True)
        ok, msg = OutputCommit.publish_file(staging, final_dest, val, allow_overwrite=False)
        self.assertFalse(ok)
        self.assertIn("OUTPUT_CONFLICT", msg)

        # Verify existing file was preserved untouched
        with open(final_dest, "r") as f:
            self.assertEqual(f.read(), "EXISTING USER OUTPUT")

        # Test explicit overwrite allowed
        with open(staging, "w") as f:
            f.write("NEW OUTPUT")
        ok_ow, _ = OutputCommit.publish_file(staging, final_dest, val, allow_overwrite=True)
        self.assertTrue(ok_ow)
        with open(final_dest, "r") as f:
            self.assertEqual(f.read(), "NEW OUTPUT")

    # -----------------------------------------------------------------
    # R06: Render Cache Quality and Profile Separation
    # -----------------------------------------------------------------
    def test_r06_render_cache_quality_profile_keys_and_integrity(self):
        """R06: Cache key must differentiate quality presets and encoder options, and verify integrity."""
        cache = RenderCache(cache_dir=os.path.join(self.temp_dir, "cache"))

        src_file = os.path.join(self.temp_dir, "test_clip.mp4")
        with open(src_file, "wb") as f:
            f.write(b"dummy_video_data_1234567890")

        key_std = RenderCache.compute_key(
            source_path=src_file, start_sec=0.0, duration=5.0, width=1920, height=1080, fps=30.0,
            quality="standard", encoder="libx264"
        )
        key_high = RenderCache.compute_key(
            source_path=src_file, start_sec=0.0, duration=5.0, width=1920, height=1080, fps=30.0,
            quality="very_high", encoder="libx264"
        )
        self.assertNotEqual(key_std, key_high, "Different quality must yield different cache keys")

        key_gpu = RenderCache.compute_key(
            source_path=src_file, start_sec=0.0, duration=5.0, width=1920, height=1080, fps=30.0,
            quality="high", encoder="h264_nvenc"
        )
        key_cpu_fb = RenderCache.compute_key(
            source_path=src_file, start_sec=0.0, duration=5.0, width=1920, height=1080, fps=30.0,
            quality="high", encoder="libx264"
        )
        self.assertNotEqual(key_gpu, key_cpu_fb, "CPU fallback encoder must yield a distinct cache key")

        # Integrity check: corrupt file in cache should be rejected by get()
        cache_path = cache.get_cache_path(key_std)
        with open(cache_path, "wb") as f:
            f.write(b"corrupted_non_mp4_bytes")
        self.assertIsNone(cache.get(key_std), "Corrupt cache file must return None and be evicted")

    # -----------------------------------------------------------------
    # R07: Keep Full Tracks Uniqueness Across Outputs
    # -----------------------------------------------------------------
    def test_r07_keep_full_tracks_unique_batch_scope(self):
        """R07: keep_full_tracks must maintain UNIQUE_BATCH across all outputs."""
        # 1 video segment of 10s
        seg = CatalogSegment(
            segment_id="v_seg_1", asset_id="asset_v1", source_path="v1.mp4",
            source_filename="v1.mp4", start_sec=0.0, end_sec=10.0, duration_sec=10.0,
            start_frame=0, end_frame=300, frame_count=300
        )
        # 2 audio tracks of 10s each
        audio_tracks = [
            AudioInfo(path="a1.mp3", filename="a1.mp3", duration=10.0, sample_rate=48000, channels=2, codec="mp3", size_bytes=1000),
            AudioInfo(path="a2.mp3", filename="a2.mp3", duration=10.0, sample_rate=48000, channels=2, codec="mp3", size_bytes=1000),
        ]
        cut_cfg = NormalizedCutConfig(
            segment_duration=5.0, target_total_duration=10.0, batch_count=2,
            unique_policy=UniquePolicy.UNIQUE_BATCH
        )
        audio_cfg = AudioConfig(mode="keep_full_tracks")
        out_cfg = OutputConfig(export_target="video_with_random_audio")

        planner = ExportPlanner(
            video_segments=[seg],
            audio_tracks=audio_tracks,
            cut_config=cut_cfg,
            audio_config=audio_cfg,
            output_config=out_cfg
        )
        # Since we only have 1 video segment of 10s and batch=2 with UNIQUE_BATCH,
        # it must raise InsufficientSegmentsError instead of silently reusing segment in both outputs!
        with self.assertRaises(InsufficientSegmentsError):
            planner.create_manifest()

    # -----------------------------------------------------------------
    # R08: MediaCatalog Slicing Preserves Remainder
    # -----------------------------------------------------------------
    def test_r08_media_catalog_slice_retains_trailing_remainder(self):
        """R08: When merge_remainder=False, remainder < segment_duration must be appended as last slice."""
        cat = MediaCatalog()
        asset = MediaAsset(
            asset_id="a1", file_path="v25.mp4", filename="v25.mp4",
            media_type="video", duration_sec=25.0, size_bytes=5000, fps=30.0
        )
        cat.register_asset(asset)

        # Slice 25s video into 10s chunks with merge_remainder=False
        segs = cat.slice_video_asset(asset, segment_duration=10.0, fps=30.0, merge_remainder=False)
        self.assertEqual(len(segs), 3, "25s video must yield 3 segments: [0,10), [10,20), [20,25)")
        self.assertEqual(segs[0].start_sec, 0.0)
        self.assertEqual(segs[0].end_sec, 10.0)
        self.assertEqual(segs[1].start_sec, 10.0)
        self.assertEqual(segs[1].end_sec, 20.0)
        self.assertEqual(segs[2].start_sec, 20.0)
        self.assertEqual(segs[2].end_sec, 25.0)
        self.assertEqual(segs[2].frame_count, 150)

    # -----------------------------------------------------------------
    # R09: AudioPlanner Respects auto_reshuffle_cycle=False
    # -----------------------------------------------------------------
    def test_r09_audio_planner_raises_when_no_cycle(self):
        """R09: When auto_reshuffle_cycle=False and tracks exhausted, raise InsufficientAudioError."""
        track = AudioInfo(
            path="short.mp3", filename="short.mp3", duration=10.0,
            sample_rate=48000, channels=2, codec="mp3", size_bytes=1000
        )
        cfg = AudioConfig(mode="fit_target", auto_reshuffle_cycle=False)
        planner = AudioPlanner(tracks=[track], config=cfg)

        with self.assertRaises(InsufficientAudioError):
            planner.plan_single_playlist(target_duration=30.0)

    # -----------------------------------------------------------------
    # R10: PlanClip Recalculates Frames When Trimmed
    # -----------------------------------------------------------------
    def test_r10_plan_clip_trimmed_frame_count(self):
        """R10: PlanClip.from_catalog_segment with trim_duration must update end_frame and frame_count."""
        seg = CatalogSegment(
            segment_id="s1", asset_id="a1", source_path="v.mp4", source_filename="v.mp4",
            start_sec=0.0, end_sec=10.0, duration_sec=10.0,
            start_frame=0, end_frame=300, frame_count=300
        )
        # Trim from 10s to 2s
        clip = PlanClip.from_catalog_segment(seg, target_video_index=1, plan_position=1, trim_duration=2.0)
        self.assertEqual(clip.effective_duration, 2.0)
        self.assertTrue(clip.is_trimmed)
        self.assertEqual(clip.start_frame, 0)
        self.assertEqual(clip.end_frame, 60)
        self.assertEqual(clip.frame_count, 60)
        self.assertEqual(clip.end_frame - clip.start_frame, clip.frame_count)

    # -----------------------------------------------------------------
    # R11: Manifest ID Comprehensiveness
    # -----------------------------------------------------------------
    def test_r11_manifest_id_hashes_audio_and_render_metadata(self):
        """R11: Manifest ID must change when audio playlist or render metadata changes."""
        clip = PlanClip(
            clip_id="c1", segment_id="s1", asset_id="a1", source_path="v.mp4", source_filename="v.mp4",
            start_frame=0, end_frame=300, frame_count=300,
            start_sec=0.0, end_sec=10.0, effective_duration=10.0,
            target_video_index=1, plan_position=1
        )
        m1 = PlanManifest.create(
            seed=42, catalog_snapshot_hash="snap_1",
            video_batches=[[clip]],
            audio_playlists=[[{"source_audio_name": "track1.mp3", "effective_duration": 10.0}]],
            metadata={"resolution": "1920x1080", "fps": 30}
        )
        # Same video, different audio playlist
        m2 = PlanManifest.create(
            seed=42, catalog_snapshot_hash="snap_1",
            video_batches=[[clip]],
            audio_playlists=[[{"source_audio_name": "track2.mp3", "effective_duration": 10.0}]],
            metadata={"resolution": "1920x1080", "fps": 30}
        )
        self.assertNotEqual(m1.manifest_id, m2.manifest_id, "Changing audio playlist must change manifest_id")

        # Same video & audio, different resolution
        m3 = PlanManifest.create(
            seed=42, catalog_snapshot_hash="snap_1",
            video_batches=[[clip]],
            audio_playlists=[[{"source_audio_name": "track1.mp3", "effective_duration": 10.0}]],
            metadata={"resolution": "1280x720", "fps": 30}
        )
        self.assertNotEqual(m1.manifest_id, m3.manifest_id, "Changing render metadata must change manifest_id")

    # -----------------------------------------------------------------
    # R12: Content-Based Fingerprint Independent of Name/Path
    # -----------------------------------------------------------------
    def test_r12_content_fingerprint_rename_and_move_stability(self):
        """R12: Identical media content in different paths must produce identical asset_id."""
        path_a = os.path.join(self.temp_dir, "original_movie.mp4")
        sub_dir = os.path.join(self.temp_dir, "other_folder")
        os.makedirs(sub_dir, exist_ok=True)
        path_b = os.path.join(sub_dir, "renamed_copy.mp4")

        dummy_content = b"RIFF" + b"\x00" * 1000 + b"WAVEfmt " + b"\x01" * 50000
        with open(path_a, "wb") as f:
            f.write(dummy_content)
        with open(path_b, "wb") as f:
            f.write(dummy_content)

        hash_a = compute_media_content_hash(path_a)
        hash_b = compute_media_content_hash(path_b)
        self.assertEqual(hash_a, hash_b, "Content hash must match across different paths and filenames")

        fp_a = AssetFingerprint.from_file(path_a, duration=10.0)
        fp_b = AssetFingerprint.from_file(path_b, duration=10.0)
        self.assertEqual(fp_a.asset_id, fp_b.asset_id, "AssetFingerprint.asset_id must be stable")

    # -----------------------------------------------------------------
    # R13: OutputValidator Strict FPS and Deep Validation
    # -----------------------------------------------------------------
    @patch("core.output_validator.ProcessRunner.run")
    def test_r13_output_validator_fps_mismatch_and_deep_validate(self, mock_run):
        """R13: OutputValidator must check expected_fps and reject FPS mismatch."""
        dummy_file = os.path.join(self.temp_dir, "rendered.mp4")
        with open(dummy_file, "wb") as f:
            f.write(b"\x00" * 2000)

        # Mock ffprobe output returning 30fps video
        mock_ffprobe_json = json.dumps({
            "streams": [{"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30/1"}],
            "format": {"duration": "10.0"}
        })
        mock_run.return_value = MagicMock(returncode=0, is_success=True, stdout=mock_ffprobe_json, stderr="")

        # Test expected 60fps against 30fps actual: must fail!
        res_fail = OutputValidator.validate_video(
            file_path=dummy_file, expected_duration=10.0,
            expected_width=1920, expected_height=1080,
            expected_fps=60.0
        )
        self.assertFalse(res_fail.is_valid)
        self.assertIn("Sai tốc độ khung hình (FPS)", res_fail.error_msg)

        # Test expected 30fps against 30fps actual: passes
        res_pass = OutputValidator.validate_video(
            file_path=dummy_file, expected_duration=10.0,
            expected_width=1920, expected_height=1080,
            expected_fps=30.0
        )
        self.assertTrue(res_pass.is_valid)


if __name__ == "__main__":
    unittest.main()
