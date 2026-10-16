"""
Unit and Integration Tests for Settings Persistence and Migration.
Verifies:
- Settings are saved to and loaded from fixed persistent user directory.
- Legacy settings file in local app dir is auto-migrated if persistent file does not exist.
- All setting keys (sources, output, acceleration, cache, theme) are properly preserved.
- Theme switching saves and restores light/dark mode preference.
"""
import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from core import app_paths

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.app_paths import (
    get_persistent_data_dir, get_settings_json_path, get_data_dir,
    get_cache_dir, set_test_data_dir, reset_data_dir, get_app_dir
)
from core.settings_manager import SettingsManager, DEFAULT_SETTINGS


class TestSettingsPersistence(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="remix_settings_test_")
        self.path_patch = patch.object(app_paths, "_TEST_DATA_DIR", self.temp_dir)
        self.path_patch.start()
        # Point settings to test path
        self.test_settings_file = os.path.join(self.temp_dir, "user_settings.json")
        self.mgr = SettingsManager()
        self.previous_path = self.mgr._file_path
        self.previous_data = dict(self.mgr._data)
        self.previous_migration = self.mgr._allow_migration
        self.mgr.set_file_path(self.test_settings_file)

    def tearDown(self):
        self.mgr._file_path = self.previous_path
        self.mgr._data = self.previous_data
        self.mgr._allow_migration = self.previous_migration
        self.path_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_default_settings_keys(self):
        """Verifies all required configuration keys exist in default settings."""
        required_keys = [
            # Thư mục nguồn
            "video_folder", "audio_folder", "recursive_video", "recursive_audio",
            # Cấu hình xuất
            "output_folder", "output_prefix", "resolution", "fps",
            "aspect_ratio_mode", "quality", "audio_output_format", "video_audio_mode",
            # Tăng tốc
            "hw_accel", "render_speed", "parallel_segments", "gpu_processing", "decoder_mode",
            # Cache
            "cache_enabled", "cache_max_gb", "cache_folder",
            # Theme
            "theme"
        ]
        defaults = self.mgr.load()
        for k in required_keys:
            self.assertIn(k, defaults, f"Key '{k}' must be present in settings")

    def test_save_and_reload_preferences(self):
        """Verifies saving and reloading preferences across all categories."""
        custom_values = {
            "video_folder": "D:/MyVideos/Source",
            "audio_folder": "D:/MyAudio/BGM",
            "output_folder": "D:/MyVideos/Exported",
            "output_prefix": "CUSTOM_PREFIX",
            "resolution": "1080x1920",
            "fps": 60,
            "aspect_ratio_mode": "crop",
            "quality": "very_high",
            "hw_accel": "nvenc",
            "render_speed": "quality",
            "parallel_segments": 2,
            "gpu_processing": True,
            "decoder_mode": "gpu",
            "cache_enabled": True,
            "cache_max_gb": 20,
            "cache_folder": "D:/Cache/Remix",
            "theme": "light"
        }
        self.mgr.update(custom_values)

        # Reload from scratch
        new_mgr = SettingsManager()
        new_mgr.set_file_path(self.test_settings_file)
        loaded = new_mgr.load()

        for k, v in custom_values.items():
            self.assertEqual(loaded[k], v, f"Setting '{k}' must match saved value '{v}'")

    def test_auto_migration_from_legacy_file(self):
        """Verifies that an existing legacy user_settings.json in local app dir is auto-migrated."""
        isolated_dir = tempfile.mkdtemp(prefix="remix_mig_test_")
        try:
            # Simulate a persistent settings target that does not exist yet
            persistent_target = os.path.join(isolated_dir, "persistent", "user_settings.json")
            legacy_source = os.path.join(isolated_dir, "legacy_app", "user_settings.json")
            os.makedirs(os.path.dirname(legacy_source), exist_ok=True)

            legacy_payload = {
                "video_folder": "C:/OldVersion/Videos",
                "theme": "light",
                "resolution": "3840x2160"
            }
            with open(legacy_source, "w", encoding="utf-8") as f:
                json.dump(legacy_payload, f)

            # Test SettingsManager migration logic when file does not exist
            mgr = SettingsManager()
            from unittest.mock import patch
            with patch("core.settings_manager.get_settings_json_path", return_value=persistent_target), \
                 patch("core.settings_manager.get_legacy_settings_paths", return_value=[legacy_source]):
                mgr.reset_file_path()
                loaded = dict(mgr.load())

            self.assertTrue(os.path.isfile(persistent_target), "Legacy settings must be copied to persistent target")
            self.assertEqual(loaded.get("video_folder"), "C:/OldVersion/Videos")
            self.assertEqual(loaded.get("theme"), "light")
            self.assertEqual(loaded.get("resolution"), "3840x2160")
        finally:
            shutil.rmtree(isolated_dir, ignore_errors=True)

    def test_persistent_paths_resolution(self):
        """Verifies that persistent data and settings paths resolve to valid directories."""
        # Under normal conditions (without test data dir), persistent path uses APPDATA or ~/.config
        with patch.object(app_paths, "_TEST_DATA_DIR", None), \
             patch.dict(os.environ, {"APPDATA": os.path.join(self.temp_dir, "appdata")}):
            p_dir = get_persistent_data_dir()
            self.assertTrue(os.path.isdir(p_dir))
            self.assertTrue(p_dir.startswith(self.temp_dir))
            s_path = get_settings_json_path()
            self.assertTrue(s_path.startswith(p_dir))
            c_dir = get_cache_dir()
            self.assertTrue(c_dir.startswith(p_dir))


if __name__ == "__main__":
    unittest.main()
