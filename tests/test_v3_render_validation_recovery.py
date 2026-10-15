"""
Unit tests for C07 — Output Validation, Atomic Commit, and Crash Recovery.
"""
import unittest
import sys
import os
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures_media import create_synthetic_video, create_synthetic_audio
from core.output_validator import OutputValidator, ValidationResult
from core.output_commit import OutputCommit
from core.history_repository import HistoryRepository
from core.job_recovery import JobRecovery


class TestV3RenderValidationRecovery(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.repo = HistoryRepository(os.path.join(self.td, "test_hist.db"))
        self.recovery = JobRecovery(self.repo, journal_dir=self.td)

    # -------------------------------------------------------------
    # OutputValidator Tests
    # -------------------------------------------------------------
    def test_validator_valid_video(self):
        vid_path = os.path.join(self.td, "valid.mp4")
        create_synthetic_video(vid_path, duration=3.0, width=640, height=360, fps=30.0, with_audio=True)

        res = OutputValidator.validate_video(
            file_path=vid_path,
            expected_duration=3.0,
            expected_width=640,
            expected_height=360,
            expected_fps=30.0,
            require_audio=True
        )
        self.assertTrue(res.is_valid, f"Validation failed: {res.error_msg}")
        self.assertAlmostEqual(res.actual_duration, 3.0, delta=0.5)
        self.assertEqual(res.actual_width, 640)
        self.assertEqual(res.actual_height, 360)
        self.assertTrue(res.has_video)
        self.assertTrue(res.has_audio)

    def test_validator_mismatched_resolution(self):
        vid_path = os.path.join(self.td, "mismatch_res.mp4")
        create_synthetic_video(vid_path, duration=2.0, width=640, height=360)

        res = OutputValidator.validate_video(
            file_path=vid_path,
            expected_duration=2.0,
            expected_width=1920,
            expected_height=1080
        )
        self.assertFalse(res.is_valid)
        self.assertIn("Sai độ phân giải", res.error_msg)

    def test_validator_valid_audio(self):
        aud_path = os.path.join(self.td, "valid.mp3")
        create_synthetic_audio(aud_path, duration=4.0)

        res = OutputValidator.validate_audio(aud_path, expected_duration=4.0)
        self.assertTrue(res.is_valid, f"Audio validation failed: {res.error_msg}")
        self.assertAlmostEqual(res.actual_duration, 4.0, delta=0.5)

    # -------------------------------------------------------------
    # OutputCommit Tests
    # -------------------------------------------------------------
    def test_atomic_commit_promotion_success(self):
        staging = os.path.join(self.td, "staged.mp4")
        create_synthetic_video(staging, duration=2.0, width=640, height=360)
        final_dest = os.path.join(self.td, "out", "published.mp4")

        valid_res = ValidationResult(is_valid=True)
        ok, msg = OutputCommit.publish_file(staging, final_dest, valid_res)

        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(final_dest))
        self.assertFalse(os.path.isfile(staging))  # Staging promoted and gone

    def test_atomic_commit_rejection_discards_staging(self):
        staging = os.path.join(self.td, "corrupt_staged.mp4")
        with open(staging, "w") as f:
            f.write("corrupt")
        final_dest = os.path.join(self.td, "out", "should_not_exist.mp4")

        invalid_res = ValidationResult(is_valid=False, error_msg="Corrupt header")
        ok, msg = OutputCommit.publish_file(staging, final_dest, invalid_res)

        self.assertFalse(ok)
        self.assertFalse(os.path.isfile(final_dest))
        self.assertFalse(os.path.isfile(staging))  # Discarded safely

    # -------------------------------------------------------------
    # JobRecovery Tests
    # -------------------------------------------------------------
    def test_job_recovery_cleanup_orphans(self):
        # Create orphaned staging file and temp directory
        orphan_staging = os.path.join(self.td, "_staging_9999.mp4")
        with open(orphan_staging, "w") as f:
            f.write("junk")

        orphan_temp_dir = os.path.join(self.td, "_temp_remix_abc123")
        os.makedirs(orphan_temp_dir, exist_ok=True)
        with open(os.path.join(orphan_temp_dir, "seg.mp4"), "w") as f:
            f.write("seg junk")

        # Run recovery
        report = self.recovery.recover_interrupted_jobs([self.td])
        self.assertEqual(report["cleaned_staging_files"], 1)
        self.assertEqual(report["cleaned_temp_workspaces"], 1)

        self.assertFalse(os.path.isfile(orphan_staging))
        self.assertFalse(os.path.isdir(orphan_temp_dir))


if __name__ == "__main__":
    unittest.main()
