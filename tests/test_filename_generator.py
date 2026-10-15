"""
Unit tests for core/filename_generator.py
Verifies the daily MMDD naming scheme for video and audio exports:
- 1st file: 1014.mp4 / 1014.mp3
- 2nd file: 1014(0).mp4 / 1014(0).mp3
- 3rd file: 1014(1).mp4 / 1014(1).mp3
- Multi-video batch reservations
- Respect for disk existence
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.filename_generator import (
    get_daily_base_name,
    resolve_base_prefix,
    get_next_output_path
)


class TestFilenameGenerator(unittest.TestCase):
    def test_daily_base_name(self):
        # 14th October 2026
        dt1 = datetime(2026, 10, 14, 15, 30, 0)
        self.assertEqual(get_daily_base_name(dt1), "1014")

        # 5th January
        dt2 = datetime(2026, 1, 5, 9, 0, 0)
        self.assertEqual(get_daily_base_name(dt2), "0105")

        # 9th December
        dt3 = datetime(2026, 12, 9, 12, 0, 0)
        self.assertEqual(get_daily_base_name(dt3), "1209")

    def test_resolve_base_prefix(self):
        dt = datetime(2026, 10, 14)
        self.assertEqual(resolve_base_prefix(None, dt), "1014")
        self.assertEqual(resolve_base_prefix("", dt), "1014")
        self.assertEqual(resolve_base_prefix("Random_Output", dt), "1014")
        self.assertEqual(resolve_base_prefix("auto", dt), "1014")
        self.assertEqual(resolve_base_prefix("{date}", dt), "1014")
        self.assertEqual(resolve_base_prefix("CustomPrefix", dt), "CustomPrefix")

    def test_sequence_empty_directory_video(self):
        dt = datetime(2026, 10, 14)
        with tempfile.TemporaryDirectory() as td:
            reserved = set()

            # 1st file
            p1 = get_next_output_path(td, "mp4", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(p1), "1014.mp4")

            # 2nd file
            p2 = get_next_output_path(td, "mp4", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(p2), "1014(0).mp4")

            # 3rd file
            p3 = get_next_output_path(td, "mp4", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(p3), "1014(1).mp4")

            # 4th file
            p4 = get_next_output_path(td, "mp4", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(p4), "1014(2).mp4")

    def test_sequence_with_existing_files_on_disk(self):
        dt = datetime(2026, 10, 14)
        with tempfile.TemporaryDirectory() as td:
            # Create 1014.mp4 on disk
            f1 = os.path.join(td, "1014.mp4")
            with open(f1, "w") as f:
                f.write("test")

            # Next file should be 1014(0).mp4
            next_p = get_next_output_path(td, "mp4", prefix=None, now=dt)
            self.assertEqual(os.path.basename(next_p), "1014(0).mp4")

            # Create 1014(0).mp4 on disk
            f2 = os.path.join(td, "1014(0).mp4")
            with open(f2, "w") as f:
                f.write("test")

            # Next file should be 1014(1).mp4
            next_p2 = get_next_output_path(td, "mp4", prefix=None, now=dt)
            self.assertEqual(os.path.basename(next_p2), "1014(1).mp4")

    def test_sequence_audio_export(self):
        dt = datetime(2026, 10, 14)
        with tempfile.TemporaryDirectory() as td:
            reserved = set()

            # 1st audio
            a1 = get_next_output_path(td, "mp3", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(a1), "1014.mp3")

            # 2nd audio
            a2 = get_next_output_path(td, "mp3", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(a2), "1014(0).mp3")

            # 3rd audio
            a3 = get_next_output_path(td, "mp3", prefix=None, reserved_paths=reserved, now=dt)
            self.assertEqual(os.path.basename(a3), "1014(1).mp3")

    def test_audio_and_video_do_not_conflict(self):
        dt = datetime(2026, 10, 14)
        with tempfile.TemporaryDirectory() as td:
            # Create 1014.mp4 on disk
            f1 = os.path.join(td, "1014.mp4")
            with open(f1, "w") as f:
                f.write("test")

            # Audio export should still start at 1014.mp3
            a1 = get_next_output_path(td, "mp3", prefix=None, now=dt)
            self.assertEqual(os.path.basename(a1), "1014.mp3")

    def test_worker_output_filenames(self):
        from unittest.mock import patch, MagicMock
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig, SegmentInfo, AudioPlaylistItem

        with tempfile.TemporaryDirectory() as td:
            # Batch of 3 videos
            plans = [[MagicMock(spec=SegmentInfo)], [MagicMock(spec=SegmentInfo)], [MagicMock(spec=SegmentInfo)]]
            out_cfg = OutputConfig(output_dir=td, output_prefix="")
            worker = VideoProcessorWorker(batch_plans=plans, output_config=out_cfg)

            started_paths = []
            worker.video_started.connect(lambda cur, tot, p: started_paths.append(os.path.basename(p)))

            with patch("core.video_processor.VideoProcessorEngine.process_video_plan", return_value=True):
                worker.run()

            today_str = datetime.now().strftime("%m%d")
            self.assertEqual(len(started_paths), 3)
            self.assertEqual(started_paths[0], f"{today_str}.mp4")
            self.assertEqual(started_paths[1], f"{today_str}(0).mp4")
            self.assertEqual(started_paths[2], f"{today_str}(1).mp4")

    def test_worker_standalone_audio_filenames(self):
        from unittest.mock import patch, MagicMock
        from core.video_processor import VideoProcessorWorker
        from core.config import OutputConfig, AudioPlaylistItem

        with tempfile.TemporaryDirectory() as td:
            item = MagicMock(spec=AudioPlaylistItem)
            out_cfg = OutputConfig(export_target="audio_only", output_dir=td, output_prefix="", audio_output_format="mp3")
            worker = VideoProcessorWorker(batch_plans=[], output_config=out_cfg, audio_playlists=[[item]])

            started_paths = []
            worker.video_started.connect(lambda cur, tot, p: started_paths.append(os.path.basename(p)))

            with patch("core.video_processor.AudioProcessorEngine.process_audio_playlist", return_value=True):
                worker.run()

            today_str = datetime.now().strftime("%m%d")
            self.assertEqual(len(started_paths), 1)
            self.assertEqual(started_paths[0], f"{today_str}.mp3")


if __name__ == "__main__":
    unittest.main()
