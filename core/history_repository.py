"""
SQLite History Repository Module (V3).
Provides persistent tracking of exported video intervals with:
- Schema versioning
- Asset ID based interval overlap queries (replacing brittle filename keys)
- Pre-export job reservations to prevent concurrent duplicate exports
- Transactional commits and rollbacks
"""
import sqlite3
import time
import os
from typing import List, Dict, Optional, Tuple, Any

from core.errors import HistoryConflictError
from core.app_paths import get_history_db_path
from core.plan_manifest import PlanClip
from core.media_catalog import CatalogSegment

CURRENT_SCHEMA_VERSION = 2


class HistoryRepository:
    """SQLite-backed history and reservation repository."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_history_db_path()
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_db(self):
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            # 1. Schema version table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL
                );
            """)

            cursor = conn.cursor()
            cursor.execute("SELECT MAX(version) FROM schema_version;")
            row = cursor.fetchone()
            curr_v = row[0] if row and row[0] is not None else 0

            if curr_v > CURRENT_SCHEMA_VERSION:
                conn.execute("ROLLBACK;")
                raise HistoryConflictError(
                    f"Cơ sở dữ liệu lịch sử phiên bản {curr_v} mới hơn phiên bản ứng dụng ({CURRENT_SCHEMA_VERSION})."
                )

            if curr_v == 0:
                # Initialize V2 Schema with domain check constraints
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS assets (
                        asset_id TEXT PRIMARY KEY,
                        file_path TEXT,
                        filename TEXT,
                        file_size INTEGER,
                        duration_sec REAL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS history_intervals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        asset_id TEXT NOT NULL,
                        source_filename TEXT,
                        start_sec REAL NOT NULL,
                        end_sec REAL NOT NULL,
                        start_frame INTEGER,
                        end_frame INTEGER,
                        job_id TEXT,
                        output_video_index INTEGER,
                        exported_at REAL NOT NULL,
                        CHECK(end_sec > start_sec),
                        FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_asset ON history_intervals(asset_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_hist_interval ON history_intervals(asset_id, start_sec, end_sec);")

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        asset_id TEXT NOT NULL,
                        start_sec REAL NOT NULL,
                        end_sec REAL NOT NULL,
                        reserved_at REAL NOT NULL,
                        CHECK(end_sec > start_sec)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_res_job ON reservations(job_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_res_overlap ON reservations(asset_id, start_sec, end_sec);")

                conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?);", (CURRENT_SCHEMA_VERSION, time.time()))
                conn.execute("COMMIT;")
            elif curr_v == 1:
                # Migrate V1 -> V2: sanitize legacy inverted intervals
                conn.execute("""
                    UPDATE history_intervals
                    SET end_sec = start_sec + 1.0
                    WHERE end_sec <= start_sec;
                """)
                conn.execute("""
                    DELETE FROM reservations
                    WHERE end_sec <= start_sec;
                """)
                conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?);", (2, time.time()))
                conn.execute("COMMIT;")
            else:
                conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def register_asset(self, asset_id: str, file_path: str, filename: str, file_size: int, duration_sec: float):
        """Registers or updates metadata for a media asset."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("""
                INSERT OR REPLACE INTO assets (asset_id, file_path, filename, file_size, duration_sec)
                VALUES (?, ?, ?, ?, ?);
            """, (asset_id, file_path, filename, file_size, duration_sec))
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def is_interval_used(self, asset_id: str, start_sec: float, end_sec: float, tolerance: float = 0.05) -> bool:
        """
        Checks whether [start_sec, end_sec] overlaps with any already exported interval
        or active reservation.
        Two intervals [A, B] and [C, D] overlap if max(A, C) < min(B, D) - tolerance.
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            # Check exported history
            cursor.execute("""
                SELECT 1 FROM history_intervals
                WHERE asset_id = ?
                  AND MAX(start_sec, ?) < (MIN(end_sec, ?) - ?)
                LIMIT 1;
            """, (asset_id, start_sec, end_sec, tolerance))
            if cursor.fetchone() is not None:
                return True

            # Check active reservations
            cursor.execute("""
                SELECT 1 FROM reservations
                WHERE asset_id = ?
                  AND MAX(start_sec, ?) < (MIN(end_sec, ?) - ?)
                LIMIT 1;
            """, (asset_id, start_sec, end_sec, tolerance))
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def reserve_batch(self, job_id: str, clips: List[PlanClip], max_retries: int = 5) -> bool:
        """
        Attempts to place a lock/reservation for all clips in a batch.
        Uses BEGIN IMMEDIATE transaction to guarantee atomic check-and-insert
        across concurrent threads and processes (R04).
        If any clip conflicts with existing history or reservations, rolls back and returns False.
        """
        now = time.time()
        for attempt in range(max_retries):
            conn = self._get_connection()
            try:
                conn.execute("BEGIN IMMEDIATE;")
                cursor = conn.cursor()
                for c in clips:
                    eff_end = c.trim_end_sec if c.trim_end_sec is not None else c.end_sec
                    if eff_end <= c.start_sec:
                        eff_end = round(c.start_sec + c.effective_duration, 6)
                    if eff_end <= c.start_sec:
                        eff_end = c.start_sec + 0.001

                    # Check conflict against committed history
                    cursor.execute("""
                        SELECT 1 FROM history_intervals
                        WHERE asset_id = ? AND MAX(start_sec, ?) < (MIN(end_sec, ?) - 0.05)
                        LIMIT 1;
                    """, (c.asset_id, c.start_sec, eff_end))
                    if cursor.fetchone() is not None:
                        conn.execute("ROLLBACK;")
                        return False

                    # Check conflict against other active reservations
                    cursor.execute("""
                        SELECT 1 FROM reservations
                        WHERE job_id != ? AND asset_id = ? AND MAX(start_sec, ?) < (MIN(end_sec, ?) - 0.05)
                        LIMIT 1;
                    """, (job_id, c.asset_id, c.start_sec, eff_end))
                    if cursor.fetchone() is not None:
                        conn.execute("ROLLBACK;")
                        return False

                # All clear: insert reservations
                for c in clips:
                    eff_end = c.trim_end_sec if c.trim_end_sec is not None else c.end_sec
                    if eff_end <= c.start_sec:
                        eff_end = round(c.start_sec + c.effective_duration, 6)
                    if eff_end <= c.start_sec:
                        eff_end = c.start_sec + 0.001
                    cursor.execute("""
                        INSERT INTO reservations (job_id, asset_id, start_sec, end_sec, reserved_at)
                        VALUES (?, ?, ?, ?, ?);
                    """, (job_id, c.asset_id, c.start_sec, eff_end, now))

                conn.execute("COMMIT;")
                return True
            except sqlite3.OperationalError as e:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    if attempt < max_retries - 1:
                        time.sleep(0.05 * (2 ** attempt))
                        continue
                raise
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except Exception:
                    pass
                raise
            finally:
                conn.close()
        return False

    def release_reservations(self, job_id: str):
        """Releases all active reservations held by job_id."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("DELETE FROM reservations WHERE job_id = ?;", (job_id,))
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def commit_job_history(self, job_id: str, clips: List[PlanClip]):
        """
        Atomically records all exported clips into history_intervals and removes job reservations.
        """
        now = time.time()
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cursor = conn.cursor()
            for c in clips:
                eff_end = c.trim_end_sec if c.trim_end_sec is not None else c.end_sec
                if eff_end <= c.start_sec:
                    eff_end = round(c.start_sec + c.effective_duration, 6)
                if eff_end <= c.start_sec:
                    eff_end = c.start_sec + 0.001

                # Ensure asset is recorded
                cursor.execute("""
                    INSERT OR IGNORE INTO assets (asset_id, file_path, filename, file_size, duration_sec)
                    VALUES (?, ?, ?, ?, ?);
                """, (c.asset_id, c.source_path, c.source_filename, 0, max(c.end_sec, eff_end)))

                cursor.execute("""
                    INSERT INTO history_intervals (
                        asset_id, source_filename, start_sec, end_sec,
                        start_frame, end_frame, job_id, output_video_index, exported_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    c.asset_id, c.source_filename, c.start_sec, eff_end,
                    c.start_frame, c.end_frame, job_id, c.target_video_index, now
                ))

            # Remove reservations for this job
            cursor.execute("DELETE FROM reservations WHERE job_id = ?;", (job_id,))
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def filter_unexported_segments(self, segments: List[CatalogSegment]) -> List[CatalogSegment]:
        """Filters a list of CatalogSegments, excluding any that overlap exported history."""
        unexported: List[CatalogSegment] = []
        for s in segments:
            if not self.is_interval_used(s.asset_id, s.start_sec, s.end_sec):
                unexported.append(s)
        return unexported

    def get_total_records(self) -> int:
        """Returns total count of exported intervals in history."""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM history_intervals;")
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def clear_history(self):
        """Clears all exported intervals and reservations."""
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute("DELETE FROM history_intervals;")
            conn.execute("DELETE FROM reservations;")
            conn.execute("COMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()
