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
from datetime import datetime
from typing import Dict, Any, Optional, List

from core.history_repository import HistoryRepository


def parse_exported_at(val: Any) -> float:
    """
    Parses exported_at timestamp from various formats:
    - Numeric epoch float/int
    - v2.5 standard formatted string: 'YYYY-MM-DD HH:MM:SS'
    - ISO formatted string: 'YYYY-MM-DDTHH:MM:SS'
    Falls back to current epoch if missing or completely unparseable.
    """
    if val is None or val == "":
        return time.time()
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val_str = val.strip()
        try:
            return float(val_str)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f"
        ):
            try:
                dt = datetime.strptime(val_str, fmt)
                return dt.timestamp()
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(val_str).timestamp()
        except Exception:
            pass
    return time.time()


def migrate_legacy_history_json(
    json_path: str,
    repo: HistoryRepository
) -> Dict[str, Any]:
    """
    Migrates legacy export_history.json into the SQLite repository.
    Creates a .bak backup file before touching the database.
    Per-record parsing is resilient and reports comprehensive diagnostics.
    """
    if not os.path.isfile(json_path):
        return {"status": "no_legacy_file", "migrated_count": 0, "skipped_count": 0, "backup_path": None, "errors": []}

    # 1. Create backup
    bak_path = json_path + ".bak"
    try:
        shutil.copy2(json_path, bak_path)
    except Exception as e:
        return {"status": "backup_failed", "error": str(e), "migrated_count": 0, "skipped_count": 0, "errors": [str(e)]}

    # 2. Read legacy records
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            records = json.load(f)
        if not isinstance(records, list):
            return {"status": "invalid_format", "migrated_count": 0, "skipped_count": 0, "backup_path": bak_path, "errors": ["JSON root is not a list"]}
    except Exception as e:
        return {"status": "read_failed", "error": str(e), "migrated_count": 0, "skipped_count": 0, "backup_path": bak_path, "errors": [str(e)]}

    # 3. Migrate into SQLite
    migrated_count = 0
    skipped_count = 0
    errors: List[str] = []

    with repo._get_connection() as conn:
        cursor = conn.cursor()
        for idx, r in enumerate(records):
            if not isinstance(r, dict):
                skipped_count += 1
                errors.append(f"Record #{idx} is not a dict.")
                continue

            try:
                name = r.get("source_video_name") or r.get("filename") or "unknown"
                start_s = float(r.get("start_sec", 0.0))
                end_s = float(r.get("end_sec", 0.0))
                exp_at = parse_exported_at(r.get("exported_at"))
                v_idx = int(r.get("video_index", 1))

                # Handle duration vs end coordinate fix (R03 support)
                if end_s <= start_s:
                    eff_d = float(r.get("effective_duration", 0.0) or r.get("duration", 0.0))
                    if eff_d > 0:
                        end_s = start_s + eff_d
                    else:
                        skipped_count += 1
                        errors.append(f"Record #{idx} ({name}) has invalid range: start={start_s}, end={end_s}.")
                        continue

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
            except Exception as item_err:
                skipped_count += 1
                errors.append(f"Record #{idx} failed: {item_err}")

        conn.commit()

    return {
        "status": "success" if (migrated_count > 0 or not records) else "partial",
        "migrated_count": migrated_count,
        "skipped_count": skipped_count,
        "errors": errors,
        "backup_path": bak_path
    }
