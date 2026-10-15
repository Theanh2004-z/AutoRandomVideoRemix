"""
History Manager — tracks and persists used video segments across export runs.
Stores history in history/export_history.json so that content creators
never duplicate already used segments in future videos.
"""
import os
import json
import time
from typing import List, Set, Dict, Any
from core.config import SegmentInfo

HISTORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "history")
HISTORY_FILE = os.path.join(HISTORY_DIR, "export_history.json")


def _make_segment_key(source_video_name: str, start_sec: float, end_sec: float) -> str:
    """Generate canonical unique key for a segment interval in a source video."""
    return f"{source_video_name}::{start_sec:.2f}::{end_sec:.2f}"


class HistoryManager:
    """Manages reading and writing of export history."""

    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        os.makedirs(os.path.dirname(self.history_file), exist_ok=True)

    def load_history_records(self) -> List[Dict[str, Any]]:
        """Load all raw records from history file."""
        if not os.path.isfile(self.history_file):
            return []
        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
        return []

    def load_history_keys(self) -> Set[str]:
        """Return set of canonical keys for all previously exported segments."""
        records = self.load_history_records()
        keys = set()
        for r in records:
            k = r.get("segment_key")
            if k:
                keys.add(k)
        return keys

    def record_exported_segments(self, segments: List[SegmentInfo]) -> int:
        """
        Append newly exported segments to history.
        Returns number of newly added records.
        """
        existing_records = self.load_history_records()
        existing_keys = {r.get("segment_key") for r in existing_records}

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        added_count = 0

        for s in segments:
            key = _make_segment_key(s.source_video_name, s.start_sec, s.end_sec)
            if key not in existing_keys:
                rec = {
                    "segment_key": key,
                    "source_video_name": s.source_video_name,
                    "source_video_path": s.source_video_path,
                    "start_sec": s.start_sec,
                    "end_sec": s.end_sec,
                    "duration": s.duration,
                    "effective_duration": s.effective_duration,
                    "exported_at": now_str
                }
                existing_records.append(rec)
                existing_keys.add(key)
                added_count += 1

        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(existing_records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return added_count

    def clear_history(self) -> bool:
        """Clear all history records."""
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump([], f)
            return True
        except Exception:
            return False

    def get_history_count(self) -> int:
        """Return total number of saved historical segments."""
        return len(self.load_history_keys())
