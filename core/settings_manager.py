"""
Settings Manager — persists user preferences and directory paths across sessions.
Stores configuration in user_settings.json in the application root directory.
"""
import os
import json
from typing import Any, Dict

SETTINGS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "user_settings.json"
)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "video_folder": "",
    "audio_folder": "",
    "output_folder": "",
    "recursive_video": False,
    "recursive_audio": False,
    "segment_duration": 10.0,
    "target_hours": 0,
    "target_mins": 30,
    "target_secs": 0,
    "batch_count": 1,
    "merge_remainder": True,
    "allow_reuse": False,
    "no_repeat_batch": True,
    "no_consecutive_video": True,
    "no_consecutive_audio": True,
    "audio_mode": "fit_target",
    "export_target": "video_with_random_audio",
    "audio_output_format": "mp3",
    "resolution": "1920x1080",
    "fps": 30,
    "hw_accel": "auto",
}


class SettingsManager:
    """Singleton-style settings manager for reading and writing user preferences."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._file_path = SETTINGS_FILE
            cls._instance._data = {}
            cls._instance.load()
        return cls._instance

    @property
    def file_path(self) -> str:
        return getattr(self, "_file_path", SETTINGS_FILE)

    def set_file_path(self, path: str):
        """Redirect settings file to a custom path (e.g. for testing)."""
        self._file_path = path
        self.load()

    def reset_file_path(self):
        """Reset settings file back to default application path."""
        self._file_path = SETTINGS_FILE
        self.load()

    def load(self) -> Dict[str, Any]:
        """Loads settings from JSON file, falling back to defaults."""
        self._data = dict(DEFAULT_SETTINGS)
        target = self.file_path
        if os.path.isfile(target):
            try:
                with open(target, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        self._data.update(saved)
            except Exception:
                pass
        return self._data

    def save(self) -> bool:
        """Saves current settings to JSON file."""
        target = self.file_path
        try:
            os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False


    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value: Any, auto_save: bool = True):
        self._data[key] = value
        if auto_save:
            self.save()

    def update(self, mapping: Dict[str, Any], auto_save: bool = True):
        self._data.update(mapping)
        if auto_save:
            self.save()


# Global helper instance
settings = SettingsManager()
