"""
Unit Tests for AutoRandomVideoRemix — Video Engine & Audio Engine.
Verifies all algorithmic requirements from Part 1 and Part 2.
"""
import unittest
from core.config import VideoInfo, AudioInfo, CutConfig, AudioConfig
from core.segment_slicer import SegmentSlicer, slice_all_videos
from core.pool_manager import SegmentPool, BatchPlanner, RandomPicker, InsufficientSegmentsError
from core.audio_pool_manager import AudioPool, AudioPicker, InsufficientAudioError
from core.history_manager import HistoryManager, _make_segment_key


class TestVideoSegmentation(unittest.TestCase):
    """Verifies Section III video slicing and remainder merging rules."""

    def setUp(self):
        self.slicer_merge = SegmentSlicer(segment_duration=10.0, merge_remainder=True)
        self.slicer_no_merge = SegmentSlicer(segment_duration=10.0, merge_remainder=False)

    def _make_vid(self, name: str, dur: float) -> VideoInfo:
        return VideoInfo(path=f"/fake/{name}.mp4", filename=f"{name}.mp4", duration=dur,
                         width=1920, height=1080, fps=30.0, codec="h264", has_audio=False, size_bytes=1000)

    def test_example_35s(self):
        """Video 35s -> 10 + 10 + 15"""
        segs = self.slicer_merge.slice(self._make_vid("A", 35.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [10.0, 10.0, 15.0])
        self.assertEqual(segs[-1].end_sec, 35.0)

    def test_example_48s(self):
        """Video 48s -> 10 + 10 + 10 + 18"""
        segs = self.slicer_merge.slice(self._make_vid("B", 48.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [10.0, 10.0, 10.0, 18.0])

    def test_example_23s(self):
        """Video 23s -> 10 + 13"""
        segs = self.slicer_merge.slice(self._make_vid("C", 23.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [10.0, 13.0])

    def test_example_60s(self):
        """Video 60s -> 6 segments of 10s"""
        segs = self.slicer_merge.slice(self._make_vid("D", 60.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [10.0] * 6)

    def test_example_8s(self):
        """Video 8s -> 1 segment of 8s (no prior segment to merge)"""
        segs = self.slicer_merge.slice(self._make_vid("E", 8.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [8.0])

    def test_example_15s(self):
        """Video 15s -> 1 segment of 15s (10 + 5 remainder merged)"""
        segs = self.slicer_merge.slice(self._make_vid("F", 15.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [15.0])

    def test_no_merge_remainder(self):
        """When merge_remainder=False, 35s -> 10 + 10 + 10 + 5"""
        segs = self.slicer_no_merge.slice(self._make_vid("A", 35.0))
        durs = [s.duration for s in segs]
        self.assertEqual(durs, [10.0, 10.0, 10.0, 5.0])


class TestVideoBatchPlanning(unittest.TestCase):
    """Verifies Section V, VI, VII batch planning and exact duration."""

    def setUp(self):
        self.vids = [
            VideoInfo(f"/fake/A.mp4", "A.mp4", 35.0, 1920, 1080, 30.0, "h264", False, 1000),
            VideoInfo(f"/fake/B.mp4", "B.mp4", 48.0, 1920, 1080, 30.0, "h264", False, 1000),
            VideoInfo(f"/fake/C.mp4", "C.mp4", 23.0, 1920, 1080, 30.0, "h264", False, 1000),
            VideoInfo(f"/fake/D.mp4", "D.mp4", 60.0, 1920, 1080, 30.0, "h264", False, 1000),
        ]
        self.pool = SegmentPool()
        self.pool.build(self.vids, segment_duration=10.0, merge_remainder=True)

    def test_pool_size(self):
        """15 total segments, 166.0s total duration"""
        self.assertEqual(self.pool.total_segments, 15)
        self.assertEqual(self.pool.total_duration, 166.0)

    def test_target_duration_trim(self):
        """120s output target should be reached with exact sum of effective_duration"""
        cfg = CutConfig(segment_duration=10.0, target_total_duration=120.0, trim_last_segment=True)
        self.pool.shuffle()
        planner = BatchPlanner(self.pool, cfg)
        plans = planner.plan_all(120.0)
        self.assertEqual(len(plans), 1)
        plan = plans[0]
        total_eff = sum(s.effective_duration for s in plan)
        self.assertAlmostEqual(total_eff, 120.0, places=2)

    def test_no_consecutive_same_source(self):
        """No adjacent segments from same source video when multiple candidates available"""
        cfg = CutConfig(segment_duration=10.0, target_total_duration=80.0, no_consecutive_same_source=True)
        self.pool.shuffle()
        planner = BatchPlanner(self.pool, cfg)
        plan = planner.plan_all(80.0)[0]
        for i in range(len(plan) - 1):
            # If candidates are diverse, adjacent shouldn't share source
            self.assertNotEqual(plan[i].source_video_path, plan[i+1].source_video_path)


class TestAudioSystem(unittest.TestCase):
    """Verifies Part 2: Random Audio System."""

    def setUp(self):
        self.tracks = [
            AudioInfo("/music/A.mp3", "A.mp3", duration=240.0, sample_rate=44100, channels=2, codec="mp3", size_bytes=1000),  # 4m
            AudioInfo("/music/B.mp3", "B.mp3", duration=180.0, sample_rate=44100, channels=2, codec="mp3", size_bytes=1000),  # 3m
            AudioInfo("/music/C.mp3", "C.mp3", duration=360.0, sample_rate=44100, channels=2, codec="mp3", size_bytes=1000),  # 6m
            AudioInfo("/music/D.mp3", "D.mp3", duration=300.0, sample_rate=44100, channels=2, codec="mp3", size_bytes=1000),  # 5m
            AudioInfo("/music/E.mp3", "E.mp3", duration=120.0, sample_rate=44100, channels=2, codec="mp3", size_bytes=1000),  # 2m
        ]
        self.pool = AudioPool(self.tracks)

    def test_audio_pool_duration(self):
        """5 tracks total = 20 minutes (1200 seconds)"""
        self.assertEqual(self.pool.total_tracks, 5)
        self.assertEqual(self.pool.total_duration, 1200.0)

    def test_anti_adjacent_cycle_transition(self):
        """Track_cycle_k[0] != Track_cycle_k-1[-1]"""
        cfg = AudioConfig(
            target_duration=3600.0,  # 60 min -> 3 full cycles
            mode="fit_target",
            no_consecutive_same_track=True,
            auto_reshuffle_cycle=True
        )
        picker = AudioPicker(self.pool, cfg)
        for trial in range(20):
            playlist = picker.pick_playlist()
            for i in range(len(playlist) - 1):
                self.assertNotEqual(
                    playlist[i].source_audio_path,
                    playlist[i+1].source_audio_path,
                    f"Trial {trial}: Found adjacent duplicate at position {i}"
                )

    def test_mode_a_trim_last_track(self):
        """Mode A (fit_target) trims last track to hit exactly 1800s (30m)"""
        cfg = AudioConfig(target_duration=1800.0, mode="fit_target")
        picker = AudioPicker(self.pool, cfg)
        playlist = picker.pick_playlist()
        total_eff = sum(p.effective_duration for p in playlist)
        self.assertAlmostEqual(total_eff, 1800.0, places=2)
        # Check that last item is trimmed if accumulated was over
        self.assertTrue(playlist[-1].effective_duration <= playlist[-1].duration)

    def test_mode_b_keep_all_tracks(self):
        """Mode B (keep_all) does not trim last track; duration >= target"""
        cfg = AudioConfig(target_duration=1800.0, mode="keep_all")
        picker = AudioPicker(self.pool, cfg)
        playlist = picker.pick_playlist()
        total_eff = sum(p.effective_duration for p in playlist)
        self.assertGreaterEqual(total_eff, 1800.0)
        self.assertIsNone(playlist[-1].trim_end_sec)


class TestHistoryManager(unittest.TestCase):
    """Verifies segment history persistence and exclusion."""

    def test_record_and_clear_history(self):
        import tempfile
        import os
        from core.config import SegmentInfo
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            hm = HistoryManager(history_file=tmp_path)
            self.assertEqual(hm.get_history_count(), 0)

            seg = SegmentInfo(
                segment_id="test1",
                source_video_path="/test/vid.mp4",
                source_video_name="vid.mp4",
                start_sec=0.0,
                end_sec=10.0,
                duration=10.0,
                segment_index=1
            )
            added = hm.record_exported_segments([seg])
            self.assertEqual(added, 1)
            self.assertEqual(hm.get_history_count(), 1)

            # Record again should not duplicate
            added2 = hm.record_exported_segments([seg])
            self.assertEqual(added2, 0)
            self.assertEqual(hm.get_history_count(), 1)

            # Clear
            hm.clear_history()
            self.assertEqual(hm.get_history_count(), 0)
        finally:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()

