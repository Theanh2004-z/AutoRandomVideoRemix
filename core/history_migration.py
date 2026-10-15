"""
History Migration Module (V3).
Safely migrates legacy JSON export_history.json records into the SQLite HistoryRepository.
Creates automatic backups before migration and provides verifiable statistics.
"""
import os
import json
import shutil
import hashlib
import time
from typing import Dict, Any, Optional

from core.history_repository import HistoryRepository


def migrate_legacy_history_json(
    json_path: str,
    repo: HistoryRepository
) -> Dict[str, Any]:
    """
    Migrates legacy export_history.json into the SQLite repository.
    Creates a .bak backup file before touching the database.
    """
    if not os.path.isfile(json_path):
        return {"status": "no_legacy_file", "migrated_count": 0, "backup_path": None}

    # 1. Create backup
    bak_path = json_path + ".bak"
    try:
        shutil.copy2(json_path, bak_path)
    except Exception as e:
        return {"status": "backup_failed", "error": str(e), "migrated_count": 0}

    # 2. Read legacy records
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            return {"status": "invalid_format", "migrated_count": 0, "backup_path": bak_path}
    except Exception as e:
        return {"status": "read_failed", "error": str(e), "migrated_count": 0, "backup_path": bak_path}

    # 3. Migrate into SQLite
    migrated_count = 0
    with repo._get_connection() as conn:
        cursor = conn.cursor()
        for r in records:
            name = r.get("source_video_name") or r.get("filename") or "unknown"
            start_s = float(r.get("start_sec", 0.0))
            end_s = float(r.get("end_sec", 0.0))
            exp_at = float(r.get("exported_at", time.time()))
            v_idx = int(r.get("video_index", 1))

            # Deterministic asset id for legacy records
            aid = "legacy_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]

            # Register asset
            cursor.execute("""
                INSERT OR IGNORE INTO assets (asset_id, file_path, filename, file_size, duration_sec)
                VALUES (?, ?, ?, ?, ?);
            """, (aid, name, name, 0, end_s))

            # Insert history interval
            cursor.execute("""
                INSERT INTO history_intervals (
                    asset_id, source_filename, start_sec, end_sec,
                    start_frame, end_frame, job_id, output_video_index, exported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (aid, name, start_s, end_s, 0, 0, "migrated_legacy_job", v_idx, exp_at))
            migrated_count += 1

        conn.commit()

    return {
        "status": "success",
        "migrated_count": migrated_count,
        "backup_path": bak_path
    }
