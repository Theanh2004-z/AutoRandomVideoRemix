"""
Unit tests for C04 — Audio Planner and Export Master Planner.
"""
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import VideoInfo, AudioInfo, AudioConfig, OutputConfig
from core.media_catalog import MediaCatalog
from core.models import NormalizedCutConfig
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.errors import InsufficientAudioError, InsufficientSegmentsError
from core.audio_planner import AudioPlanner
from core.export_planner import ExportPlanner


class TestV3AudioExportPlanner(unittest.TestCase):
    def setUp(self):
        self.cat = MediaCatalog()
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
            duration=60.0,
            width=1920,
            height=1080,
            fps=30.0,
            codec="h264",
            has_audio=True,
            size_bytes=1000000,
            is_valid=True
        )
        self.cat.add_video(self.v1)
        self.cat.add_video(self.v2)
        self.video_segs = self.cat.slice_all(segment_duration=10.0, fps=30.0)

        self.a1 = AudioInfo(
            path="C:/media/song_1.mp3",
            filename="song_1.mp3",
            duration=45.0,
            sample_rate=48000,
            channels=2,
            codec="mp3",
            size_bytes=500000,
            is_valid=True
        )
        self.a2 = AudioInfo(
            path="C:/media/song_2.mp3",
            filename="song_2.mp3",
            duration=50.0,
            sample_rate=48000,
            channels=2,
            codec="mp3",
            size_bytes=550000,
            is_valid=True
        )
        self.audio_tracks = [self.a1, self.a2]

    # ------------------------------------------------------------------
    # AudioPlanner Tests
    # ------------------------------------------------------------------
    def test_audio_planner_fit_target_exact_trim(self):
        # 45s + 50s = 95s per cycle. Request 60s -> First track 45s, second track trimmed to 15s
        cfg = AudioConfig(mode="fit_target", target_duration=60.0)
        ap = AudioPlanner(self.audio_tracks, cfg, seed=10)
        playlist = ap.plan_single_playlist(target_duration=60.0)

        self.assertEqual(len(playlist), 2)
        self.assertTrue(playlist[-1].is_trimmed)
        total_dur = sum(item.effective_duration for item in playlist)
        self.assertAlmostEqual(total_dur, 60.0, places=3)

    def test_audio_planner_keep_full_tracks_no_trim(self):
        # Request 60s in keep_full_tracks mode -> 45s + 50s = 95s total, neither is trimmed
        cfg = AudioConfig(mode="keep_full_tracks", target_duration=60.0)
        ap = AudioPlanner(self.audio_tracks, cfg, seed=10)
        playlist = ap.plan_single_playlist(target_duration=60.0)

        self.assertEqual(len(playlist), 2)
        self.assertFalse(playlist[-1].is_trimmed)
        total_dur = sum(item.effective_duration for item in playlist)
        self.assertAlmostEqual(total_dur, 95.0, places=3)

    def test_audio_planner_anti_adjacent_cycle_boundary(self):
        # Request 250s (~3 cycles). Across cycles, track K[0] != track K-1[-1]
        cfg = AudioConfig(mode="fit_target", no_consecutive_same_track=True)
        ap = AudioPlanner(self.audio_tracks, cfg, seed=42)
        playlist = ap.plan_single_playlist(target_duration=250.0)

        self.assertTrue(len(playlist) >= 4)
        for i in range(1, len(playlist)):
            self.assertNotEqual(
                playlist[i].source_audio_path,
                playlist[i - 1].source_audio_path,
                f"Consecutive same track found at position {i}"
            )

    # ------------------------------------------------------------------
    # ExportPlanner Tests
    # ------------------------------------------------------------------
    def test_export_planner_validate_plan(self):
        cut_cfg = NormalizedCutConfig(segment_duration=10.0, target_total_duration=30.0, batch_count=1)
        audio_cfg = AudioConfig(mode="fit_target", target_duration=30.0)
        out_cfg = OutputConfig(export_target="video_with_random_audio")

        # Valid configuration
        ep = ExportPlanner(self.video_segs, self.audio_tracks, cut_cfg, audio_cfg, out_cfg, seed=1)
        ok, msg = ep.validate_plan()
        self.assertTrue(ok)

        # Insufficient video footage validation
        huge_cut_cfg = NormalizedCutConfig(segment_duration=10.0, target_total_duration=5000.0, batch_count=1, unique_policy=UniquePolicy.UNIQUE_BATCH)
        ep_fail = ExportPlanner(self.video_segs, self.audio_tracks, huge_cut_cfg, audio_cfg, out_cfg, seed=1)
        ok, msg = ep_fail.validate_plan()
        self.assertFalse(ok)
        self.assertIn("Không đủ đoạn", msg)

    def test_export_planner_keep_full_tracks_audio_dictates_duration(self):
        # In keep_full_tracks mode, audio determines total video duration
        cut_cfg = NormalizedCutConfig(segment_duration=10.0, target_total_duration=60.0, batch_count=1, trim_last_segment=True)
        # 45s + 50s = 95s
        audio_cfg = AudioConfig(mode="keep_full_tracks", target_duration=60.0)
        out_cfg = OutputConfig(export_target="video_with_random_audio")

        ep = ExportPlanner(self.video_segs, self.audio_tracks, cut_cfg, audio_cfg, out_cfg, seed=5)
        manifest = ep.create_manifest()

        self.assertEqual(manifest.total_videos, 1)
        v_plan = manifest.video_batches[0]
        a_plan = manifest.audio_playlists[0]

        # Total audio duration is 95s
        total_audio = sum(item["effective_duration"] for item in a_plan)
        self.assertAlmostEqual(total_audio, 95.0, places=2)

        # Video plan must expand to match 95s!
        total_video = sum(c.effective_duration for c in v_plan)
        self.assertAlmostEqual(total_video, 95.0, places=2)

    def test_export_planner_audio_only_batches(self):
        cut_cfg = NormalizedCutConfig(target_total_duration=40.0, batch_count=3)
        audio_cfg = AudioConfig(mode="fit_target", target_duration=40.0)
        out_cfg = OutputConfig(export_target="audio_only")

        ep = ExportPlanner(self.video_segs, self.audio_tracks, cut_cfg, audio_cfg, out_cfg, seed=7)
        manifest = ep.create_manifest()

        self.assertEqual(len(manifest.video_batches), 0)
        self.assertEqual(len(manifest.audio_playlists), 3)
        for pl in manifest.audio_playlists:
            dur = sum(item["effective_duration"] for item in pl)
            self.assertAlmostEqual(dur, 40.0, places=2)


if __name__ == "__main__":
    unittest.main()
