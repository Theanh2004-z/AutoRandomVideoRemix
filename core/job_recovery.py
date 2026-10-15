"""
Job Recovery and Crash Journal Module (V3).
Manages crash journals, detects interrupted render jobs, and performs clean recovery:
- Cleans up orphaned staging files and temp workspace directories
- Releases stale reservations from crashed processes
- Reconciles published outputs with uncommitted history
"""
import os
import glob
import time
import json
import shutil
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Any

from core.history_repository import HistoryRepository


@dataclass
class JournalEntry:
    job_id: str
    step: str  # STARTED | RESERVED | STAGED | VALIDATED | PUBLISHED | HISTORY_COMMITTED | FINISHED
    timestamp: float
    output_path: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class JobRecovery:
    """Manages crash journaling and post-crash state reconciliation."""

    def __init__(self, history_repo: Optional[HistoryRepository] = None, journal_dir: Optional[str] = None):
        self.repo = history_repo or HistoryRepository()
        self.journal_dir = journal_dir or os.path.dirname(self.repo.db_path)
        os.makedirs(self.journal_dir, exist_ok=True)

    def _get_journal_path(self, job_id: str) -> str:
        return os.path.join(self.journal_dir, f"journal_{job_id}.jsonl")

    def record_step(self, job_id: str, step: str, output_path: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        """Appends a journal entry for the job."""
        entry = JournalEntry(
            job_id=job_id,
            step=step,
            timestamp=time.time(),
            output_path=output_path,
            details=details
        )
        j_path = self._get_journal_path(job_id)
        try:
            with open(j_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception:
            pass

    def cleanup_job(self, job_id: str, temp_workspace: Optional[str] = None):
        """Cleans up temporary files and reservations after job ends (or upon cancellation)."""
        # 1. Release reservations
        self.repo.release_reservations(job_id)

        # 2. Remove temp workspace
        if temp_workspace and os.path.isdir(temp_workspace):
            try:
                shutil.rmtree(temp_workspace, ignore_errors=True)
            except Exception:
                pass

        # 3. Clean journal file
        j_path = self._get_journal_path(job_id)
        if os.path.isfile(j_path):
            try:
                os.remove(j_path)
            except Exception:
                pass

    def recover_interrupted_jobs(self, search_directories: List[str]) -> Dict[str, Any]:
        """
        Scans directories for leftover temporary staging files (_staging_*) or
        temp workspaces (_temp_remix_*), removes them, and cleans stale reservations.
        """
        cleaned_staging_count = 0
        cleaned_temp_dirs = 0

        for d in search_directories:
            if not os.path.isdir(d):
                continue

            # Look for _staging_* files
            staging_files = glob.glob(os.path.join(d, "_staging_*"))
            for sf in staging_files:
                try:
                    os.remove(sf)
                    cleaned_staging_count += 1
                except Exception:
                    pass

            # Look for _temp_remix_* folders
            temp_folders = glob.glob(os.path.join(d, "_temp_remix_*"))
            for tf in temp_folders:
                try:
                    shutil.rmtree(tf, ignore_errors=True)
                    cleaned_temp_dirs += 1
                except Exception:
                    pass

        # Also release any reservations older than 2 hours
        stale_time = time.time() - 7200
        with self.repo._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reservations WHERE reserved_at < ?;", (stale_time,))
            stale_released = cursor.rowcount
            conn.commit()

        return {
            "cleaned_staging_files": cleaned_staging_count,
            "cleaned_temp_workspaces": cleaned_temp_dirs,
            "stale_reservations_released": stale_released
        }
