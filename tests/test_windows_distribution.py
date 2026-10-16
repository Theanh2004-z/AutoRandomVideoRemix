"""Distribution contracts: icon resources, compiler failure and shell identity."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

from core.app_identity import APP_USER_MODEL_ID, APP_MUTEX_NAME, icon_path
from scripts.build_installer import build_installer, find_iscc
from scripts.verify_distribution import filter_system_icu, verify_distribution
from updater.main import APP_MUTEX_NAME as UPDATER_MUTEX

ROOT = Path(__file__).resolve().parents[1]


class WindowsDistributionTests(unittest.TestCase):
    def test_foreign_windows_icu_is_removed_without_removing_versioned_icu(self):
        foreign = ('icuuc.dll', 'foreign-tools/icuuc.dll', 'BINARY')
        nested = ('PySide6/ICUIN.DLL', 'foreign-tools/icuin.dll', 'BINARY')
        versioned = ('icuuc78.dll', 'package/icuuc78.dll', 'BINARY')
        qt = ('PySide6/Qt6Core.dll', 'package/Qt6Core.dll', 'BINARY')
        self.assertEqual(filter_system_icu([foreign, nested, versioned, qt]), [versioned, qt])

    def test_startup_gate_rejects_conflicting_dll_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'AutoRandomVideoRemix.exe').write_bytes(b'fixture')
            internal = root / '_internal'; internal.mkdir()
            (internal / 'icuuc.dll').write_bytes(b'foreign DLL')
            with patch('scripts.verify_distribution.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'ICU'):
                    verify_distribution(root)
                launch.assert_not_called()

    def test_source_and_frozen_icon_locations(self):
        self.assertEqual(icon_path(), ROOT / 'assets' / 'remix.ico')
        with patch.object(sys, '_MEIPASS', '/frozen-resources', create=True):
            self.assertEqual(icon_path(), Path('/frozen-resources/assets/remix.ico'))

    def test_icon_has_small_and_high_dpi_frames(self):
        from PySide6.QtGui import QIcon
        icon = QIcon(str(icon_path()))
        self.assertFalse(icon.isNull())
        sizes = {size.width() for size in icon.availableSizes()}
        self.assertTrue({16, 32, 48, 256}.issubset(sizes))

    def test_installer_and_updater_share_identity(self):
        self.assertEqual(APP_USER_MODEL_ID, APP_MUTEX_NAME)
        self.assertEqual(APP_MUTEX_NAME, UPDATER_MUTEX)
        script = (ROOT / 'installer' / 'remix.iss').read_text(encoding='utf-8')
        self.assertIn(APP_USER_MODEL_ID, script)
        self.assertIn('AppUserModelID: "{#AppUserModelID}"', script)

    def test_uninstaller_is_outside_the_directory_replaced_by_updates(self):
        script = (ROOT / 'installer' / 'remix.iss').read_text(encoding='utf-8')
        self.assertIn('UninstallFilesDir={app}\\Uninstall', script)
        self.assertIn('DestDir: "{app}\\App"', script)

    def test_missing_compiler_reports_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                find_iscc(Path(directory) / 'missing.exe')

    def test_incomplete_distribution_is_not_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                build_installer(directory, Path(directory) / 'release')

    def test_failed_compile_does_not_return_previous_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dist = root / 'dist'; dist.mkdir()
            (dist / 'AutoRandomVideoRemix.exe').write_bytes(b'fixture')
            (dist / '_internal').mkdir()
            output = root / 'release'; output.mkdir()
            previous = output / 'AutoRandomVideoRemix-1.4.2-windows-x64-Setup.exe'
            previous.write_bytes(b'previous release')
            with patch('scripts.build_installer.find_iscc', return_value='ISCC.exe'), \
                 patch('scripts.build_installer.build_icon'), patch('scripts.build_installer.ensure_updater'), \
                 patch('scripts.build_installer.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['ISCC'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_installer(dist, output, '1.4.2')
            self.assertEqual(previous.read_bytes(), b'previous release')
