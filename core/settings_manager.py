"""Persistent per-user preferences with legacy migration and atomic writes."""
import json
import math
import os
import tempfile
import threading
from typing import Any, Dict

from core.app_paths import get_settings_json_path, get_legacy_settings_paths

SETTINGS_FILE = get_settings_json_path()

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
    "quality": "high",
    "aspect_ratio_mode": "fit",
    "video_audio_mode": "none",
    "bgm_path": "",
    "output_prefix": "",
    "render_speed": "balanced",
    "parallel_segments": 0,
    "gpu_processing": True,
    "decoder_mode": "auto",
    "trim_last_segment": True,
    "use_history": False,
    "unique_policy": "unique_batch",
    "anti_consecutive_policy": "soft",
    "auto_reshuffle_audio": True,
    "cache_enabled": True,
    "cache_max_gb": 5,
    "cache_folder": "",
    "scratch_folder": "",
    "auto_cleanup": True,
    "temp_retention_hours": 24,
    "storage_min_free_gb": 2,
    "theme": "dark",
    "auto_save": True,
    "window_geometry": "",
    "window_x": None,
    "window_y": None,
    "window_width": 1320,
    "window_height": 900,
    "window_maximized": False,
    "check_updates_on_startup": True,
    "last_update_check": "",
}

_ENUMS = {
    "audio_mode": {"fit_target", "keep_all"},
    "export_target": {"video_with_random_audio", "video_only", "audio_only", "none"},
    "audio_output_format": {"mp3", "m4a", "wav"},
    "resolution": {"1920x1080", "1280x720", "3840x2160", "1080x1920"},
    "fps": {24, 25, 30, 60},
    "hw_accel": {"auto", "cpu", "nvenc", "qsv", "amf"},
    "quality": {"standard", "high", "very_high"},
    "aspect_ratio_mode": {"fit", "crop"},
    "video_audio_mode": {"none", "keep", "bgm"},
    "render_speed": {"fast", "balanced", "quality"},
    "parallel_segments": {0, 1, 2},
    "decoder_mode": {"auto", "gpu", "cpu"},
    "unique_policy": {None, "unique_batch", "unique_output", "cycle_output"},
    "anti_consecutive_policy": {None, "soft", "strict", "off"},
    "theme": {"light", "dark"},
}
_RANGES = {
    "segment_duration": (1, 3600), "target_hours": (0, 48),
    "target_mins": (0, 59), "target_secs": (0, 59),
    "batch_count": (1, 100), "cache_max_gb": (1, 500),
    "temp_retention_hours": (1, 720), "storage_min_free_gb": (1, 100),
    "window_width": (500, 7680), "window_height": (400, 4320),
}


def _validated(saved):
    data = dict(DEFAULT_SETTINGS)
    for key, value in saved.items():
        if key not in DEFAULT_SETTINGS:
            data[key] = value
            continue
        default = DEFAULT_SETTINGS[key]
        if key in _ENUMS:
            if type(value) not in (str, int, type(None)) or value not in _ENUMS[key]:
                continue
        elif isinstance(default, bool):
            if type(value) is not bool:
                continue
        elif key in _RANGES:
            if type(value) not in (int, float) or not math.isfinite(value):
                continue
            low, high = _RANGES[key]
            if not low <= value <= high or (type(default) is int and int(value) != value):
                continue
            value = type(default)(value)
        elif key in ("window_x", "window_y"):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                continue
            value = int(value) if value is not None else None
        elif isinstance(default, str) and not isinstance(value, str):
            continue
        data[key] = value
    if "unique_policy" not in saved and "allow_reuse" in saved:
        data["unique_policy"] = "cycle_output" if data["allow_reuse"] else "unique_batch" if data["no_repeat_batch"] else "unique_output"
    if "anti_consecutive_policy" not in saved and "no_consecutive_video" in saved:
        data["anti_consecutive_policy"] = "soft" if data["no_consecutive_video"] else "off"
    return data


def _read_settings(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _atomic_write(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".remix-settings-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.isfile(temporary):
            os.unlink(temporary)


class SettingsManager:
    """Shared preferences; changing a control saves immediately and safely."""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._lock = threading.RLock()
            instance._file_path = None
            instance._allow_migration = True
            instance._data = {}
            instance.last_error = ""
            instance.load()
            cls._instance = instance
        return cls._instance

    @property
    def file_path(self):
        return self._file_path or get_settings_json_path()

    def set_file_path(self, path):
        """Use an isolated path without importing another installation's data."""
        if path is None:
            return self.reset_file_path()
        with self._lock:
            self._file_path = os.fspath(path)
            self._allow_migration = False
            self.load()

    def reset_file_path(self):
        with self._lock:
            self._file_path = None
            self._allow_migration = True
            self.load()

    def load(self):
        with self._lock:
            target = self.file_path
            self.last_error = ""
            saved = _read_settings(target)
            restore = False
            if saved is None:
                saved = _read_settings(target + ".bak")
                restore = saved is not None
            # Never replace an existing shared file with older installation data.
            if saved is None and self._allow_migration and not os.path.exists(target):
                for legacy in get_legacy_settings_paths():
                    if os.path.abspath(legacy) == os.path.abspath(target):
                        continue
                    saved = _read_settings(legacy)
                    if saved is not None:
                        restore = True
                        break
            self._data = _validated(saved or {})
            if restore:
                self.save()
            return self._data

    def save(self):
        with self._lock:
            try:
                # A valid previous version remains recoverable if a later file is damaged.
                previous = _read_settings(self.file_path)
                if previous is not None:
                    _atomic_write(self.file_path + ".bak", previous)
                _atomic_write(self.file_path, self._data)
                self.last_error = ""
                return True
            except (OSError, ValueError, TypeError) as error:
                self.last_error = str(error)
                return False

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default if default is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key, value, auto_save=True):
        with self._lock:
            self._data[key] = value
            if auto_save and (self._data.get("auto_save", True) or key == "auto_save"):
                self.save()

    def update(self, mapping, auto_save=True):
        with self._lock:
            self._data.update(mapping)
            if auto_save and (self._data.get("auto_save", True) or "auto_save" in mapping):
                self.save()


settings = SettingsManager()
