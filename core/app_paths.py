"""
Application Paths Management Module (V3).
Controls locations for application data, configuration, and SQLite databases.
Stores persistent user settings and history in a permanent directory (%APPDATA% on Windows,
~/.config on Linux/macOS) so that updating/moving the EXE does not lose settings.
Supports automatic migration of legacy configuration and isolated directories for unit tests.
"""
import os
import sys
import sqlite3
import tempfile
from contextlib import closing
from typing import Optional

_TEST_DATA_DIR: Optional[str] = None


def set_test_data_dir(path: Optional[str]):
    """Set an isolated test data directory for unit and integration testing."""
    global _TEST_DATA_DIR
    _TEST_DATA_DIR = path
    if path:
        os.makedirs(path, exist_ok=True)


def reset_data_dir():
    """Reset data directory back to standard application directory."""
    global _TEST_DATA_DIR
    _TEST_DATA_DIR = None


def get_app_dir() -> str:
    """Returns the base application executable/script root directory."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_persistent_data_dir() -> str:
    """
    Returns the permanent user data directory across all application versions and updates.
    Windows: %APPDATA%/AutoRandomVideoRemix
    Other: ~/.config/AutoRandomVideoRemix
    """
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        return _TEST_DATA_DIR

    appdata = os.environ.get("APPDATA")
    if appdata:
        base = os.path.join(appdata, "AutoRandomVideoRemix")
    else:
        base = os.path.join(os.path.expanduser("~"), ".config", "AutoRandomVideoRemix")
    os.makedirs(base, exist_ok=True)
    return base


def get_data_dir() -> str:
    """
    Returns the active data storage directory (history DB, etc.).
    Uses persistent user data directory so history is not lost across versions.
    Auto-migrates any legacy history DB from the local app directory if present.
    """
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        return _TEST_DATA_DIR

    persistent_dir = os.path.join(get_persistent_data_dir(), "history")
    os.makedirs(persistent_dir, exist_ok=True)

    # Migrate legacy history DB if present in local app_dir and not yet in persistent_dir
    legacy_db = os.path.join(get_app_dir(), "history", "export_history.db")
    target_db = os.path.join(persistent_dir, "export_history.db")
    if not os.path.isfile(target_db) and os.path.isfile(legacy_db):
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".remix-history-", suffix=".db", dir=persistent_dir)
            os.close(descriptor)
            try:
                from pathlib import Path
                with closing(sqlite3.connect(Path(legacy_db).resolve().as_uri() + "?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(temporary)) as destination:
                        source.backup(destination)
                # Install only if another app has not already created the shared DB.
                try:
                    if os.name == "nt":
                        os.rename(temporary, target_db)  # Windows rename refuses to replace an existing file.
                    else:
                        os.link(temporary, target_db)
                except FileExistsError:
                    pass
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except Exception:
            if not os.path.isfile(target_db):
                return os.path.dirname(legacy_db)

    return persistent_dir


def get_history_db_path() -> str:
    """Returns the path to the SQLite history database."""
    return os.path.join(get_data_dir(), "export_history.db")


def get_settings_json_path() -> str:
    """Permanent user preferences; migration is handled atomically by the manager."""
    if _TEST_DATA_DIR:
        return os.path.join(_TEST_DATA_DIR, "user_settings.json")
    return os.path.join(get_persistent_data_dir(), "user_settings.json")


def get_legacy_settings_paths():
    """Previous application-local locations; tests never import real user data."""
    if _TEST_DATA_DIR:
        return []
    app_dir = get_app_dir()
    paths = [os.path.join(app_dir, "user_settings.json")]
    if getattr(sys, "frozen", False):
        paths.append(os.path.join(app_dir, "_internal", "user_settings.json"))
        if hasattr(sys, "_MEIPASS"):
            paths.append(os.path.join(sys._MEIPASS, "user_settings.json"))
    return list(dict.fromkeys(paths))


def get_cache_dir() -> str:
    """Returns the persistent cache directory."""
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        cache_dir = os.path.join(_TEST_DATA_DIR, "cache")
    else:
        cache_dir = os.path.join(get_persistent_data_dir(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir
