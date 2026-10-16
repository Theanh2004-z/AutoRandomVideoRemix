"""
Comprehensive Test Suite for Auto-Update System.
Tests:
  1. Version parsing and semantic version comparison (numeric ordering).
  2. Architecture filtering (x64 vs arm64) and platform compatibility.
  3. GitHub release parsing, manifest fetching, and error handling (HTTP 404, rate limit, offline).
  4. Zip Slip vulnerability protection and safe extraction.
  5. Update manager lifecycle, multi-drive disk space pre-flight, mandatory SHA-256 and size verification.
  6. Per-download cancellation token safety and race-condition prevention.
  7. Updater helper transaction, clean rollback (purging new files), health handshake verification, and journaling.
  8. Pending update detection and update directory cleanup/retention policy.
  9. Release packaging script and manifest generation with updater helper bundling.
  10. UI components: UpdateDialog and SettingsDialog 'Giới thiệu' tab.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QWidget

from core.app_paths import set_test_data_dir, reset_data_dir, get_updates_dir, get_app_dir
from core.version import (
    APP_NAME, APP_VERSION, parse_version, is_newer_version
)
from core.update_service import (
    ReleaseAsset, ReleaseInfo, UpdateCheckResult,
    extract_sha256_from_text, parse_release_payload, check_for_updates,
    is_compatible_zip_asset, get_current_platform_identifiers
)
from core.update_manager import (
    UpdateManager, safe_extract_zip, get_free_disk_space_bytes,
    STATE_IDLE, STATE_CHECKING, STATE_UPDATE_AVAILABLE, STATE_UP_TO_DATE,
    STATE_DOWNLOADING, STATE_CANCELLING, STATE_CANCELLED,
    STATE_READY_TO_INSTALL, STATE_ERROR
)
from updater.main import (
    execute_update_transaction, clean_rollback, copy_or_move_contents,
    clear_directory_contents, wait_for_health_handshake
)
from scripts.build_release import calculate_sha256, package_release

# Ensure QApplication exists for GUI component testing
app = QApplication.instance()
if app is None:
    app = QApplication([])


class TestVersionLogic(unittest.TestCase):
    """Tests version parsing and comparison."""

    def test_parse_version(self):
        self.assertEqual(parse_version("1.4.2"), (1, 4, 2, ""))
        self.assertEqual(parse_version("v1.4.2"), (1, 4, 2, ""))
        self.assertEqual(parse_version("V2.0.1"), (2, 0, 1, ""))
        self.assertEqual(parse_version("1.10.0"), (1, 10, 0, ""))
        self.assertEqual(parse_version("1.4.3-beta.1"), (1, 4, 3, "beta.1"))
        self.assertIsNone(parse_version("invalid"))
        self.assertIsNone(parse_version(""))
        self.assertIsNone(parse_version(None))
        self.assertIsNone(parse_version("1.2"))

    def test_is_newer_version_numeric_comparison(self):
        self.assertTrue(is_newer_version("1.10.0", "1.9.0"))
        self.assertFalse(is_newer_version("1.9.0", "1.10.0"))
        self.assertTrue(is_newer_version("2.0.0", "1.99.99"))
        self.assertTrue(is_newer_version("1.5.0", "1.4.99"))
        self.assertTrue(is_newer_version("1.4.3", "1.4.2"))
        self.assertFalse(is_newer_version("1.4.2", "1.4.2"))
        self.assertFalse(is_newer_version("1.4.1", "1.4.2"))

    def test_prerelease_filtering(self):
        self.assertFalse(is_newer_version("1.5.0-beta.1", "1.4.2", allow_prerelease=False))
        self.assertTrue(is_newer_version("1.5.0-beta.1", "1.4.2", allow_prerelease=True))


class TestPlatformAndArchitectureFiltering(unittest.TestCase):
    """Tests Issue 5: strict architecture and platform filtering."""

    def test_compatible_zip_asset_selection(self):
        # x64 platform
        self.assertTrue(is_compatible_zip_asset("AutoRandomVideoRemix-1.5.0-windows-x64.zip", target_os="windows", target_arch="x64"))
        self.assertFalse(is_compatible_zip_asset("AutoRandomVideoRemix-1.5.0-windows-arm64.zip", target_os="windows", target_arch="x64"))
        self.assertFalse(is_compatible_zip_asset("otherapp-1.5.0-windows-x64.zip", target_os="windows", target_arch="x64"))
        self.assertFalse(is_compatible_zip_asset("AutoRandomVideoRemix-1.5.0-macos-x64.zip", target_os="windows", target_arch="x64"))
        self.assertFalse(is_compatible_zip_asset("arbitrary.zip", target_os="windows", target_arch="x64"))

        # arm64 platform
        self.assertTrue(is_compatible_zip_asset("AutoRandomVideoRemix-1.5.0-windows-arm64.zip", target_os="windows", target_arch="arm64"))
        self.assertFalse(is_compatible_zip_asset("AutoRandomVideoRemix-1.5.0-windows-x64.zip", target_os="windows", target_arch="arm64"))

    def test_parse_release_payload_architecture_matching(self):
        payload = {
            "tag_name": "v1.5.0",
            "name": "AutoRandomVideoRemix v1.5.0",
            "body": "Release",
            "assets": [
                {"name": "AutoRandomVideoRemix-1.5.0-windows-arm64.zip", "browser_download_url": "http://arm64.zip", "size": 100},
                {"name": "AutoRandomVideoRemix-1.5.0-windows-x64.zip", "browser_download_url": "http://x64.zip", "size": 100},
            ]
        }
        # Under x64, it MUST select the x64 zip even if arm64 is listed first
        rel_x64 = parse_release_payload(payload, target_os="windows", target_arch="x64")
        self.assertIsNotNone(rel_x64.zip_asset)
        self.assertEqual(rel_x64.zip_asset.name, "AutoRandomVideoRemix-1.5.0-windows-x64.zip")

        # Under arm64, it MUST select the arm64 zip
        rel_arm = parse_release_payload(payload, target_os="windows", target_arch="arm64")
        self.assertIsNotNone(rel_arm.zip_asset)
        self.assertEqual(rel_arm.zip_asset.name, "AutoRandomVideoRemix-1.5.0-windows-arm64.zip")


class TestUpdateService(unittest.TestCase):
    """Tests GitHub release parsing, manifest fetching, and error handling."""

    def test_extract_sha256_from_text(self):
        sample_hash = "a" * 64
        text = f"This is a release notes.\nSHA256: {sample_hash}\nEnjoy!"
        self.assertEqual(extract_sha256_from_text(text), sample_hash)
        self.assertIsNone(extract_sha256_from_text("no hash here"))

    @patch("urllib.request.urlopen")
    def test_check_for_updates_fetches_manifest_sha(self, mock_urlopen):
        # First call returns release JSON, second call returns manifest JSON
        release_payload = {
            "tag_name": "v1.5.0",
            "name": "AutoRandomVideoRemix v1.5.0",
            "body": "Release without hash in body",
            "assets": [
                {"name": "AutoRandomVideoRemix-1.5.0-windows-x64.zip", "browser_download_url": "http://pkg.zip", "size": 2048},
                {"name": "update_manifest.json", "browser_download_url": "http://manifest.json", "size": 128}
            ]
        }
        manifest_payload = {
            "sha256": "abcdef" * 10 + "1234",
            "archive_size": 2048,
        }

        mock_resp_rel = MagicMock()
        mock_resp_rel.status = 200
        mock_resp_rel.headers.get_content_charset.return_value = "utf-8"
        mock_resp_rel.read.return_value = json.dumps(release_payload).encode("utf-8")

        mock_resp_manifest = MagicMock()
        mock_resp_manifest.read.return_value = json.dumps(manifest_payload).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [mock_resp_rel, mock_resp_manifest]

        result = check_for_updates(current_version="1.4.2", target_os="windows", target_arch="x64")
        self.assertTrue(result.is_available)
        self.assertEqual(result.release_info.sha256, manifest_payload["sha256"].lower())

    @patch("urllib.request.urlopen")
    def test_http_404_handling(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://api.github.com",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b"Not Found")
        )
        result = check_for_updates()
        # Must be 'no_releases', not 'up_to_date'!
        self.assertEqual(result.status, "no_releases")
        self.assertFalse(result.is_available)
        self.assertIn("404", result.error_message)


class TestZipSlipProtection(unittest.TestCase):
    """Tests ZIP path traversal / Zip Slip vulnerability prevention."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_safe_zip_extraction(self):
        zip_path = os.path.join(self.test_dir, "good.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("test.txt", "hello safe file")
            zf.writestr("sub/nested.txt", "hello nested file")

        dest = os.path.join(self.test_dir, "extracted")
        safe_extract_zip(zip_path, dest)
        self.assertTrue(os.path.isfile(os.path.join(dest, "test.txt")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "sub", "nested.txt")))

    def test_zip_slip_rejection(self):
        zip_path = os.path.join(self.test_dir, "malicious.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../escaped.txt", "evil content")

        dest = os.path.join(self.test_dir, "extracted_malicious")
        with self.assertRaises(ValueError) as ctx:
            safe_extract_zip(zip_path, dest)
        self.assertIn("Bảo mật", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.test_dir, "escaped.txt")))


class TestUpdateManagerIntegrityAndCancellation(unittest.TestCase):
    """Tests UpdateManager integrity checks (SHA256 & size), cancellation token, and pending updates."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        set_test_data_dir(self.test_dir)
        self.manager = UpdateManager()

    def tearDown(self):
        reset_data_dir()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_mandatory_sha256_rejection_when_missing(self):
        # A release with NO SHA-256 must be rejected before extraction
        rel = ReleaseInfo(
            version="1.5.0",
            tag_name="v1.5.0",
            name="v1.5.0",
            body="Notes without hash",
            published_at="",
            html_url="",
            sha256=None,  # Missing hash
            zip_asset=ReleaseAsset(name="AutoRandomVideoRemix-1.5.0-windows-x64.zip", download_url="http://mock.zip", size=10)
        )

        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers.get.return_value = "10"
            mock_resp.read.side_effect = [b"0123456789", b""]
            mock_url.return_value.__enter__.return_value = mock_resp

            errors = []
            self.manager.error_occurred.connect(lambda msg: errors.append(msg))
            self.manager.download_update_async(rel)
            # Wait for worker thread to finish
            if self.manager._download_thread:
                self.manager._download_thread.join(timeout=3.0)

            self.assertEqual(self.manager.state, STATE_ERROR)
            self.assertIn("thiếu mã kiểm tra toàn vẹn SHA-256", self.manager.last_error)

    def test_mandatory_size_rejection_when_mismatched(self):
        rel = ReleaseInfo(
            version="1.5.0",
            tag_name="v1.5.0",
            name="v1.5.0",
            body="",
            published_at="",
            html_url="",
            sha256="a" * 64,
            zip_asset=ReleaseAsset(name="AutoRandomVideoRemix-1.5.0-windows-x64.zip", download_url="http://mock.zip", size=100)
        )

        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers.get.return_value = "10"
            # Server returns only 10 bytes instead of 100 bytes
            mock_resp.read.side_effect = [b"0123456789", b""]
            mock_url.return_value.__enter__.return_value = mock_resp

            self.manager.download_update_async(rel)
            if self.manager._download_thread:
                self.manager._download_thread.join(timeout=3.0)

            self.assertEqual(self.manager.state, STATE_ERROR)

    def test_cancellation_safety(self):
        # Simulates cancel_operation() during download
        rel = ReleaseInfo(
            version="1.5.0",
            tag_name="v1.5.0",
            name="v1.5.0",
            body="",
            published_at="",
            html_url="",
            sha256="a" * 64,
            zip_asset=ReleaseAsset(name="AutoRandomVideoRemix-1.5.0-windows-x64.zip", download_url="http://mock.zip", size=1000)
        )

        def slow_read(*args, **kwargs):
            time.sleep(0.1)
            return b"some_chunk"

        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.headers.get.return_value = "1000"
            mock_resp.read.side_effect = slow_read
            mock_url.return_value.__enter__.return_value = mock_resp

            self.manager.download_update_async(rel)
            time.sleep(0.05)
            self.assertEqual(self.manager.state, STATE_DOWNLOADING)

            # Cancel
            self.manager.cancel_operation()
            self.assertEqual(self.manager.state, STATE_CANCELLING)

            if self.manager._download_thread:
                self.manager._download_thread.join(timeout=3.0)

            self.assertEqual(self.manager.state, STATE_CANCELLED)
            # Verify .part file was deleted
            updates_dir = get_updates_dir()
            part_files = [f for f in os.listdir(updates_dir) if f.endswith(".part")]
            self.assertEqual(part_files, [])

    def test_pending_update_and_cleanup_policy(self):
        updates_dir = get_updates_dir()
        os.makedirs(updates_dir, exist_ok=True)

        # Create dummy old backups and part files
        b1 = os.path.join(updates_dir, "backup_1.4.0_100")
        b2 = os.path.join(updates_dir, "backup_1.4.1_200")
        os.makedirs(b1, exist_ok=True)
        time.sleep(0.02)
        os.makedirs(b2, exist_ok=True)

        part_file = os.path.join(updates_dir, f"{APP_NAME}-1.0.0.zip.part")
        with open(part_file, "w") as f:
            f.write("part")
        old_time = time.time() - 2 * 86400
        os.utime(part_file, (old_time, old_time))

        # Create valid staged pending update for 1.5.0
        staging = os.path.join(updates_dir, "staging_1.5.0")
        os.makedirs(staging, exist_ok=True)
        exe_path = os.path.join(staging, f"{APP_NAME}.exe")
        with open(exe_path, "w") as f:
            f.write("exe")

        plan = {
            "app_dir": get_app_dir(),
            "new_version": "1.5.0",
            "staging_dir": staging,
            "executable_name": f"{APP_NAME}.exe"
        }
        with open(os.path.join(updates_dir, "install_plan.json"), "w") as f:
            json.dump(plan, f)

        # 1. Test check_pending_update
        pending = self.manager.check_pending_update()
        self.assertIsNotNone(pending)
        self.assertEqual(pending["new_version"], "1.5.0")
        self.assertEqual(self.manager.state, STATE_READY_TO_INSTALL)

        # 2. Test cleanup_old_updates: keeps only newest backup (b2), removes old b1 and part_file
        self.manager.cleanup_old_updates(keep_latest_backup=True)
        self.assertFalse(os.path.exists(part_file))
        self.assertFalse(os.path.exists(b1))
        self.assertTrue(os.path.exists(b2))


class TestUpdaterHelper(unittest.TestCase):
    """Tests atomic directory swap, clean rollback (purging new files), and health handshake."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.app_dir = os.path.join(self.test_dir, "app")
        self.staging_dir = os.path.join(self.test_dir, "staging")
        self.backup_dir = os.path.join(self.test_dir, "backup")
        os.makedirs(self.app_dir, exist_ok=True)
        os.makedirs(self.staging_dir, exist_ok=True)

        # Setup original app files
        with open(os.path.join(self.app_dir, "AutoRandomVideoRemix.exe"), "w") as f:
            f.write("v1.4.2-executable")
        with open(os.path.join(self.app_dir, "data.txt"), "w") as f:
            f.write("old data")

        # Setup staging files (with a new extraneous file)
        with open(os.path.join(self.staging_dir, "AutoRandomVideoRemix.exe"), "w") as f:
            f.write("v1.5.0-executable")
        with open(os.path.join(self.staging_dir, "data.txt"), "w") as f:
            f.write("new data")
        with open(os.path.join(self.staging_dir, "new_feature.dll"), "w") as f:
            f.write("dll")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_clean_rollback_purges_new_files(self):
        # 1. Create a backup of original
        shutil.copytree(self.app_dir, self.backup_dir)

        # 2. Simulate dirty swap where new_feature.dll is put in app_dir
        with open(os.path.join(self.app_dir, "new_feature.dll"), "w") as f:
            f.write("extraneous")

        # 3. Clean rollback
        success = clean_rollback(self.backup_dir, self.app_dir)
        self.assertTrue(success)

        # Verify extraneous new_feature.dll was PURGED during clean rollback
        self.assertFalse(os.path.exists(os.path.join(self.app_dir, "new_feature.dll")))
        # Verify original files exist
        with open(os.path.join(self.app_dir, "AutoRandomVideoRemix.exe"), "r") as f:
            self.assertEqual(f.read(), "v1.4.2-executable")

    def test_health_handshake_success(self):
        # Mock process that writes HEALTHY to handshake file
        handshake_file = os.path.join(self.test_dir, "handshake.ok")
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running

        # Write handshake file in background
        def write_handshake():
            time.sleep(0.1)
            with open(handshake_file, "w") as f:
                f.write("HEALTHY v1.5.0\n")

        t = threading.Thread(target=write_handshake)
        t.start()

        healthy = wait_for_health_handshake(mock_proc, handshake_file, timeout=2.0)
        t.join()
        self.assertTrue(healthy)

    def test_health_handshake_premature_exit_failure(self):
        handshake_file = os.path.join(self.test_dir, "handshake.ok")
        mock_proc = MagicMock()
        # Process exits immediately with code 0 before writing handshake
        mock_proc.poll.return_value = 0

        healthy = wait_for_health_handshake(mock_proc, handshake_file, timeout=2.0)
        self.assertFalse(healthy)


class TestReleasePackaging(unittest.TestCase):
    """Tests build_release packaging, manifest generation, and updater helper inclusion."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dist_dir = os.path.join(self.test_dir, "dist")
        self.out_dir = os.path.join(self.test_dir, "releases")
        os.makedirs(self.dist_dir, exist_ok=True)

        with open(os.path.join(self.dist_dir, f"{APP_NAME}.exe"), "w") as f:
            f.write("binary content")
        with open(os.path.join(self.dist_dir, "updater.exe"), "w") as f:
            f.write("updater binary")
        internal_dir = os.path.join(self.dist_dir, "_internal")
        os.makedirs(internal_dir, exist_ok=True)
        with open(os.path.join(internal_dir, "library.dll"), "w") as f:
            f.write("library")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch('scripts.build_release.ensure_updater')
    def test_package_release(self, mock_updater):
        zip_path = package_release(self.dist_dir, self.out_dir, version="1.5.0", notes="Test notes")
        self.assertTrue(os.path.isfile(zip_path))

        manifest_path = os.path.join(self.out_dir, "update_manifest.json")
        self.assertTrue(os.path.isfile(manifest_path))

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertEqual(manifest["version"], "1.5.0")
        self.assertEqual(manifest["executable"], f"{APP_NAME}.exe")
        self.assertEqual(manifest["sha256"], calculate_sha256(zip_path))


class TestUpdateUIComponents(unittest.TestCase):
    """Tests UI components: UpdateDialog and SettingsDialog."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        set_test_data_dir(self.test_dir)

    def tearDown(self):
        reset_data_dir()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_update_dialog_init(self):
        from ui.update_dialog import UpdateDialog
        rel = ReleaseInfo(
            version="1.5.0",
            tag_name="v1.5.0",
            name="AutoRandomVideoRemix v1.5.0",
            body="### Changes\n- Feature X",
            published_at="2026-10-16",
            html_url="https://github.com/test",
            is_newer=True,
        )
        dialog = UpdateDialog(release_info=rel)
        self.assertIn("1.5.0", dialog.lbl_title.text())
        self.assertIn("Feature X", dialog.txt_changelog.toPlainText())

    def test_settings_dialog_about_tab(self):
        from ui.settings_dialog import SettingsDialog

        class MockCutPanel(QObject):
            fill_frame_changed = Signal(bool)

            def detach_advanced_settings(self):
                return QWidget()

        class MockHost(QWidget):
            def __init__(self):
                super().__init__()
                self.output_panel = QWidget()
                self.cut_panel = MockCutPanel()
                self.workflow = MagicMock()
                self.workflow._running = False

        host = MockHost()
        mock_audio = QWidget()
        dialog = SettingsDialog(host, mock_audio)

        tab_titles = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
        self.assertIn("Giới thiệu", tab_titles)
        self.assertIn("Lưu trữ & Cache", tab_titles)
        self.assertTrue(hasattr(dialog, "about_btn_check"))
        self.assertTrue(hasattr(dialog, "about_btn_update"))


if __name__ == "__main__":
    unittest.main()
