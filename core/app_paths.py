"""
Application Paths Management Module (V3).
Controls locations for application data, configuration, and SQLite databases.
Supports portable mode and isolated temporary directories for unit testing.
"""
import os
import sys
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
    """Returns the base application root directory."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """
    Returns the active data storage directory.
    If test data dir is set, returns that. Otherwise returns <app_dir>/history or app data.
    """
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        return _TEST_DATA_DIR

    # Default to local history directory in app root for portability
    data_dir = os.path.join(get_app_dir(), "history")
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def get_history_db_path() -> str:
    """Returns the path to the SQLite history database."""
    return os.path.join(get_data_dir(), "export_history.db")


def get_settings_json_path() -> str:
    """Returns the path to user_settings.json."""
    global _TEST_DATA_DIR
    if _TEST_DATA_DIR:
        return os.path.join(_TEST_DATA_DIR, "user_settings.json")
    return os.path.join(get_app_dir(), "user_settings.json")
