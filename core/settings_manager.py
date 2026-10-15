"""
Settings Manager — persists user preferences and directory paths across sessions.
Stores configuration in a fixed persistent user directory (%APPDATA% on Windows,
~/.config on Linux/macOS) so settings survive across EXE updates and directory moves.
"""
import os
import json
import shutil
from typing import Any, Dict, Optional

from core.app_paths import get_settings_json_path, get_app_dir

DEFAULT_SETTINGS: Dict[str, Any] = {
    # ── Thư mục nguồn & quét ──
    "video_folder": "",
    "audio_folder": "",
    "recursive_video": False,
    "recursive_audio": False,

    # ── Cắt đoạn & Phân bổ ──
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
    "trim_last_segment": True,
    "unique_policy": "unique_batch",
    "anti_consecutive_policy": "soft",
    "use_history": False,
    "auto_reshuffle_audio": True,

    # ── Cấu hình âm thanh ──
    "audio_mode": "fit_target",
    "export_target": "video_with_random_audio",
    "audio_output_format": "mp3",
    "video_audio_mode": "none",
    "bgm_path": "",

    # ── Cấu hình xuất video ──
    "output_folder": "",
    "output_prefix": "",
    "resolution": "1920x1080",
    "fps": 30,
    "aspect_ratio_mode": "fit",
    "quality": "high",

    # ── Tăng tốc & phần cứng ──
    "hw_accel": "auto",
    "render_speed": "balanced",
    "parallel_segments": 0,
    "gpu_processing": True,
    "decoder_mode": "auto",

    # ── Bộ nhớ đệm (Cache) ──
    "cache_enabled": True,
    "cache_max_gb": 5,
    "cache_folder": "",

    # ── Giao diện người dùng ──
    "theme": "dark",
}


class SettingsManager:
    """Singleton-style settings manager for reading and writing user preferences."""

    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._file_path: Optional[str] = None
            cls._instance._data = {}
            cls._instance.load()
        return cls._instance

    @property
    def file_path(self) -> str:
        return self._file_path or get_settings_json_path()

    def set_file_path(self, path: Optional[str]):
        """Redirect settings file to a custom path (e.g. for testing)."""
        self._file_path = path
        self.load()

    def reset_file_path(self):
        """Reset settings file back to default persistent path."""
        self._file_path = None
        self.load()

    def load(self) -> Dict[str, Any]:
        """Loads settings from JSON file, falling back to defaults."""
        self._data = dict(DEFAULT_SETTINGS)
        target = self.file_path

        # If persistent file doesn't exist yet, check if legacy file exists in local app dir
        if not os.path.isfile(target):
            legacy_file = os.path.join(get_app_dir(), "user_settings.json")
            if os.path.isfile(legacy_file) and os.path.abspath(legacy_file) != os.path.abspath(target):
                try:
                    os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
                    shutil.copy2(legacy_file, target)
                except Exception:
                    pass

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
