"""
Application Paths Management Module (V3).
Controls locations for application data, configuration, and SQLite databases.
Stores persistent user settings and history in a permanent directory (%APPDATA% on Windows,
~/.config on Linux/macOS) so that updating/moving the EXE does not lose settings.
Supports automatic migration of legacy configuration and isolated directories for unit tests.
"""
import os
import sys
import shutil
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
            shutil.copy2(legacy_db, target_db)
        except Exception:
            pass

    return persistent_dir


def get_history_db_path() -> str:
    """Returns the path to the SQLite history database."""
    return os.path.join(get_data_dir(), "export_history.db")


def get_settings_json_path() -> str:
    """
    Returns the path to user_settings.json in the persistent user directory.
    Automatically imports old settings from local app_dir if present.
    """
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        return os.path.join(_TEST_DATA_DIR, "user_settings.json")

    persistent_settings = os.path.join(get_persistent_data_dir(), "user_settings.json")
    if not os.path.isfile(persistent_settings):
        # Auto-migrate from legacy local app directory if exists
        local_settings = os.path.join(get_app_dir(), "user_settings.json")
        if os.path.isfile(local_settings):
            try:
                shutil.copy2(local_settings, persistent_settings)
            except Exception:
                pass

    return persistent_settings


def get_cache_dir() -> str:
    """Returns the persistent cache directory."""
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        cache_dir = os.path.join(_TEST_DATA_DIR, "cache")
    else:
        cache_dir = os.path.join(get_persistent_data_dir(), "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir
