"""
History Manager — tracks and persists used video segments across export runs.
Integrates with SQLite-backed HistoryRepository (V3) while maintaining full
backward compatibility with legacy JSON history records.
"""
import os
import json
import time
from typing import List, Set, Dict, Any, Optional

from core.config import SegmentInfo
from core.app_paths import get_data_dir, get_history_db_path
from core.history_repository import HistoryRepository
from core.history_migration import migrate_legacy_history_json

HISTORY_DIR = get_data_dir()
HISTORY_FILE = os.path.join(HISTORY_DIR, "export_history.json")


def _make_segment_key(source_video_name: str, start_sec: float, end_sec: float) -> str:
    """Generate canonical unique key for a segment interval in a source video."""
    return f"{source_video_name}::{start_sec:.2f}::{end_sec:.2f}"


class HistoryManager:
    """Unified history manager bridging SQLite repository and legacy JSON access."""

    def __init__(self, history_file: str = HISTORY_FILE, db_path: Optional[str] = None):
        self.history_file = history_file
        self.db_path = db_path or (
            os.path.join(os.path.dirname(os.path.abspath(history_file)), "export_history.db")
            if history_file != HISTORY_FILE else get_history_db_path()
        )
        os.makedirs(os.path.dirname(os.path.abspath(self.history_file)), exist_ok=True)
        self.repo = HistoryRepository(self.db_path)

        # Run one-time migration if legacy JSON exists and SQLite is empty
        if os.path.isfile(self.history_file) and self.repo.get_total_records() == 0:
            migrate_legacy_history_json(self.history_file, self.repo)

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
        Append newly exported segments to history (both SQLite and JSON).
        Returns number of newly added records.
        """
        existing_records = self.load_history_records()
        existing_keys = {r.get("segment_key") for r in existing_records}

        now_str = time.strftime("%Y-%m-%d %H:%M:%S")
        added_count = 0

        with self.repo._get_connection() as conn:
            cursor = conn.cursor()
            for s in segments:
                key = _make_segment_key(s.source_video_name, s.start_sec, s.end_sec)
                if key not in existing_keys:
                    actual_end = s.trim_end_sec if s.trim_end_sec is not None else s.end_sec
                    if actual_end <= s.start_sec:
                        actual_end = round(s.start_sec + s.effective_duration, 6)
                    if actual_end <= s.start_sec:
                        actual_end = s.start_sec + 0.001

                    rec = {
                        "segment_key": key,
                        "source_video_name": s.source_video_name,
                        "source_video_path": s.source_video_path,
                        "start_sec": s.start_sec,
                        "end_sec": actual_end,
                        "duration": s.duration,
                        "effective_duration": s.effective_duration,
                        "exported_at": now_str
                    }
                    existing_records.append(rec)
                    existing_keys.add(key)
                    added_count += 1

                    # Also persist in SQLite
                    aid = f"asset_{s.source_video_name}"
                    cursor.execute("""
                        INSERT OR IGNORE INTO assets (asset_id, file_path, filename, file_size, duration_sec)
                        VALUES (?, ?, ?, ?, ?);
                    """, (aid, s.source_video_path, s.source_video_name, 0, max(s.end_sec, actual_end)))
                    cursor.execute("""
                        INSERT INTO history_intervals (
                            asset_id, source_filename, start_sec, end_sec,
                            start_frame, end_frame, job_id, output_video_index, exported_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (aid, s.source_video_name, s.start_sec, actual_end, 0, 0, "legacy_export", 1, time.time()))

            conn.commit()

        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(existing_records, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return added_count

    def clear_history(self) -> bool:
        """Clear all history records in both SQLite and JSON."""
        self.repo.clear_history()
        try:
            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump([], f)
            return True
        except Exception:
            return False

    def get_history_count(self) -> int:
        """Return total number of saved historical segments."""
        repo_count = self.repo.get_total_records()
        json_count = len(self.load_history_keys())
        return max(repo_count, json_count)
