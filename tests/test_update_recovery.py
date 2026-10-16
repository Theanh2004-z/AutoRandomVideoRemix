"""Regression tests for install identity, crash recovery, release and retention."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch, MagicMock

from core import app_paths
from core.update_manager import UpdateManager, STATE_READY_TO_INSTALL
from scripts.build_release import package_release
from scripts.build_updater import ensure_updater
from updater import main as helper


class PowerLoss(BaseException):
    pass


class UpdateRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.previous_data = app_paths._TEST_DATA_DIR
        app_paths.set_test_data_dir(str(self.root / 'userdata'))
        self.app = self.root / 'app'
        self.stage = self.root / 'stage'
        self.app.mkdir(); self.stage.mkdir()
        (self.app / 'AutoRandomVideoRemix.exe').write_bytes(b'old')
        (self.app / 'updater.exe').write_bytes(b'fixture helper')
        (self.app / 'preferences.txt').write_bytes(b'preserve me')
        (self.stage / 'AutoRandomVideoRemix.exe').write_bytes(b'new')
        (self.stage / 'new-only.dll').write_bytes(b'new dependency')
        self.patch_dir = patch('core.update_manager.get_app_dir', return_value=str(self.app))
        self.patch_dir.start()
        self.manager = UpdateManager()
        self.plan = self.manager.prepare_install_plan(str(self.stage), '9.0.0')
        self.plan.update(skip_wait=True, skip_launch=True)
        self.journal = self.plan['journal_file']
        self.addCleanup(self.patch_dir.stop)
        self.addCleanup(app_paths.set_test_data_dir, self.previous_data)
        self.addCleanup(self.temp.cleanup)

    def assert_original_restored(self):
        self.assertEqual((self.app / 'AutoRandomVideoRemix.exe').read_bytes(), b'old')
        self.assertEqual((self.app / 'preferences.txt').read_bytes(), b'preserve me')
        self.assertFalse((self.app / 'new-only.dll').exists())

    def test_resumed_plan_refreshes_pid_identity_and_keeps_same_install(self):
        self.plan['parent_pid'] = 424242
        helper.atomic_write_json(self.plan['plan_file'], self.plan)
        resumed = UpdateManager()
        self.assertIsNotNone(resumed.check_pending_update())
        with patch.object(resumed, 'is_running_from_source', return_value=False), \
             patch.object(resumed, '_launch_helper') as launch, \
             patch('PySide6.QtWidgets.QApplication.instance', return_value=None):
            self.assertTrue(resumed.apply_update_and_restart())
        dispatched = helper.read_journal(launch.call_args.args[0][-1])
        self.assertEqual(dispatched['parent_pid'], os.getpid())
        self.assertEqual(dispatched['parent_identity'], helper.process_identity(os.getpid()))
        self.assertEqual(dispatched['app_dir'], str(self.app))
        self.assertNotEqual(dispatched['backup_dir'], self.plan['backup_dir'])

    def test_other_install_plan_is_not_loaded_or_applied(self):
        self.plan['app_dir'] = str(self.root / 'different-install')
        helper.atomic_write_json(self.plan['plan_file'], self.plan)
        self.assertIsNone(self.manager.check_pending_update())
        self.manager._install_plan = self.plan
        self.manager._staged_dir = str(self.stage)
        with patch.object(self.manager, 'is_running_from_source', return_value=False), \
             patch.object(self.manager, '_launch_helper') as launch:
            self.assertFalse(self.manager.apply_update_and_restart())
            launch.assert_not_called()
        self.assert_original_restored()

    def test_missing_helper_and_copy_failure_never_launch_python_fallback(self):
        self.manager._install_plan = self.plan
        self.manager._staged_dir = str(self.stage)
        for missing in (False, True):
            if missing:
                (self.app / 'updater.exe').unlink()
            with patch.object(self.manager, 'is_running_from_source', return_value=False), \
                 patch('core.update_manager.shutil.copy2', side_effect=PermissionError('locked')), \
                 patch.object(self.manager, '_launch_helper') as launch:
                self.assertFalse(self.manager.apply_update_and_restart())
                launch.assert_not_called()

    def test_journal_preserves_transaction_paths(self):
        helper.write_journal_state(self.journal, 'preparing', self.plan)
        helper.write_journal_state(self.journal, 'swapping')
        saved = helper.read_journal(self.journal)
        self.assertEqual(saved['backup_dir'], self.plan['backup_dir'])
        self.assertEqual(saved['app_dir'], str(self.app))

    def test_successful_directory_swap_retains_original_backup(self):
        self.assertTrue(helper.execute_update_transaction(self.plan))
        self.assertEqual((self.app / 'AutoRandomVideoRemix.exe').read_bytes(), b'new')
        self.assertEqual((Path(self.plan['backup_dir']) / 'AutoRandomVideoRemix.exe').read_bytes(), b'old')
        self.assertFalse((self.app / 'preferences.txt').exists())
        self.assertEqual(helper.read_journal(self.journal)['state'], 'committed')

    def test_interruption_at_each_checkpoint_recovers_original(self):
        # Each checkpoint gets its own transaction and original installation.
        for point in ('prepared', 'swapping', 'old_renamed', 'new_renamed', 'installed'):
            with self.subTest(point=point):
                if os.path.exists(self.journal):
                    os.unlink(self.journal)
                plan = self.manager.prepare_install_plan(str(self.stage), '9.0.0')
                plan.update(skip_wait=True, skip_launch=True)
                write = helper.write_journal_state
                replace = os.replace
                def checkpoint(path, state, details=None):
                    write(path, state, details)
                    if state == point:
                        raise PowerLoss()
                def rename(src, dst):
                    replace(src, dst)
                    if point == 'old_renamed' and str(src) == str(self.app):
                        raise PowerLoss()
                    if point == 'new_renamed' and str(dst) == str(self.app):
                        raise PowerLoss()
                with patch('updater.main.write_journal_state', side_effect=checkpoint), \
                     patch('updater.main.os.replace', side_effect=rename):
                    with self.assertRaises(PowerLoss):
                        helper.execute_update_transaction(plan)
                self.assertTrue(helper.recover_transaction(self.journal))
                self.assert_original_restored()
                self.assertEqual(helper.read_journal(self.journal)['state'], 'rolled_back')
                self.assertTrue(helper.recover_transaction(self.journal))  # idempotent

    def test_copy_failure_leaves_original_usable(self):
        with patch('updater.main.shutil.copytree', side_effect=OSError('disk full')):
            self.assertFalse(helper.execute_update_transaction(self.plan))
        self.assert_original_restored()
        self.assertEqual(helper.read_journal(self.journal)['state'], 'rolled_back')

    def test_registration_failure_happens_before_install_is_changed(self):
        with patch('updater.main.arm_recovery', side_effect=PermissionError('RunOnce denied')):
            self.assertFalse(helper.execute_update_transaction(self.plan))
        self.assert_original_restored()
        self.assertTrue(helper.recover_transaction(self.journal))

    def test_windows_recovery_registration_uses_external_runner(self):
        with patch.object(sys, 'frozen', True, create=True), \
             patch.object(sys, 'executable', str(self.root / 'external-runner.exe')), \
             patch('winreg.CreateKey'), patch('winreg.SetValueEx') as write:
            helper.arm_recovery(self.journal)
        command = write.call_args.args[-1]
        self.assertIn(str(self.root / 'external-runner.exe'), command)
        self.assertIn('--recover', command)
        self.assertIn('--relaunch', command)

    def test_wait_for_current_process_does_not_signal_or_terminate_it(self):
        self.assertFalse(helper.wait_for_pid(os.getpid(), timeout=0, expected_identity=helper.process_identity(os.getpid())))

    def test_handshake_failure_waits_for_child_and_restores_original(self):
        self.plan['skip_launch'] = False
        child = MagicMock()
        child.poll.return_value = None
        with patch('updater.main.subprocess.Popen', return_value=child), \
             patch('updater.main.wait_for_health_handshake', return_value=False):
            self.assertFalse(helper.execute_update_transaction(self.plan))
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=5)
        self.assert_original_restored()

    def test_recovery_failure_never_claims_rollback_or_erases_backup(self):
        replace = os.replace
        def interrupt(src, dst):
            replace(src, dst)
            if str(src) == str(self.app):
                raise PowerLoss()
        with patch('updater.main.os.replace', side_effect=interrupt):
            with self.assertRaises(PowerLoss):
                helper.execute_update_transaction(self.plan)
        with patch('updater.main.os.replace', side_effect=PermissionError('locked')):
            self.assertFalse(helper.recover_transaction(self.journal))
        self.assertEqual((Path(self.plan['backup_dir']) / 'AutoRandomVideoRemix.exe').read_bytes(), b'old')
        self.assertNotIn(helper.read_journal(self.journal)['state'], helper.TERMINAL_STATES)
        self.assertTrue(helper.recover_transaction(self.journal))
        self.assert_original_restored()

    def test_an_interrupted_transaction_is_recovered_before_new_install(self):
        def interrupt(path, state, details=None):
            original_write(path, state, details)
            if state == 'installed':
                raise PowerLoss()
        original_write = helper.write_journal_state
        with patch('updater.main.write_journal_state', side_effect=interrupt):
            with self.assertRaises(PowerLoss):
                helper.execute_update_transaction(self.plan)
        self.assertFalse(helper.execute_update_transaction(self.plan))
        self.assert_original_restored()

    def test_interruption_during_rollback_is_also_recoverable(self):
        original_write = helper.write_journal_state
        replace = os.replace
        for point in ('new_removed', 'old_restored'):
            with self.subTest(point=point):
                if os.path.exists(self.journal):
                    os.unlink(self.journal)
                plan = self.manager.prepare_install_plan(str(self.stage), '9.0.0')
                plan.update(skip_wait=True, skip_launch=True)
                def stop_installed(path, state, details=None):
                    original_write(path, state, details)
                    if state == 'installed':
                        raise PowerLoss()
                with patch('updater.main.write_journal_state', side_effect=stop_installed):
                    with self.assertRaises(PowerLoss):
                        helper.execute_update_transaction(plan)
                def stop_rollback(src, dst):
                    replace(src, dst)
                    if point == 'new_removed' and str(src) == str(self.app):
                        raise PowerLoss()
                    if point == 'old_restored' and str(src) == plan['backup_dir']:
                        raise PowerLoss()
                with patch('updater.main.os.replace', side_effect=stop_rollback):
                    with self.assertRaises(PowerLoss):
                        helper.recover_transaction(self.journal)
                self.assertTrue(helper.recover_transaction(self.journal))
                self.assert_original_restored()

    def test_retention_cleans_only_older_marked_install_backups(self):
        self.assertTrue(helper.execute_update_transaction(self.plan))
        second = self.manager.prepare_install_plan(str(self.stage), '9.0.0')
        second.update(skip_wait=True, skip_launch=True)
        self.assertTrue(helper.execute_update_transaction(second))
        old_time = 1000000000
        os.utime(self.plan['backup_dir'], (old_time, old_time))
        self.manager.cleanup_old_updates()
        self.assertFalse(os.path.exists(self.plan['backup_dir']))
        self.assertTrue(os.path.exists(second['backup_dir']))

    def test_active_transaction_lock_blocks_recovery(self):
        with helper.transaction_lock(self.journal):
            self.assertFalse(helper.recover_transaction(self.journal))
        self.assert_original_restored()

    def test_startup_cleanup_without_network_preserves_pending_zip(self):
        updates = Path(app_paths.get_updates_dir())
        old = updates / 'AutoRandomVideoRemix-1.0.0.zip'
        pending = updates / 'AutoRandomVideoRemix-9.0.0.zip'
        unrelated = updates / 'personal.zip'
        for path in (old, pending, unrelated):
            path.write_bytes(b'fixture')
        self.plan['archive_path'] = str(pending)
        helper.atomic_write_json(self.plan['plan_file'], self.plan)
        self.manager.cleanup_old_updates()
        self.assertFalse(old.exists())
        self.assertTrue(pending.exists())
        self.assertTrue(unrelated.exists())

    def test_cleanup_never_deletes_unfinished_recovery_payload(self):
        updates = Path(app_paths.get_updates_dir())
        old = updates / 'AutoRandomVideoRemix-1.0.0.zip'
        old.write_bytes(b'fixture')
        helper.write_journal_state(self.journal, 'swapping', self.plan)
        self.manager.cleanup_old_updates(keep_latest_backup=False)
        self.assertTrue(old.exists())

    def test_source_checkout_is_never_replaced(self):
        (self.app / '.git').mkdir()
        self.assertFalse(helper.execute_update_transaction(self.plan))
        self.assert_original_restored()


class ReleaseFailureTests(unittest.TestCase):
    def test_failed_helper_build_stops_release_even_if_old_exe_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            dist = Path(tmp) / 'dist'; dist.mkdir()
            (dist / 'AutoRandomVideoRemix.exe').write_bytes(b'fixture')
            (dist / 'updater.exe').write_bytes(b'stale helper')
            output = Path(tmp) / 'release'
            with patch('scripts.build_updater.subprocess.run', side_effect=subprocess.CalledProcessError(1, ['PyInstaller'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    package_release(str(dist), str(output), '9.0.0')
            self.assertFalse(output.exists())

    def test_success_exit_without_helper_output_stops_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('scripts.build_updater.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
                with self.assertRaises(RuntimeError):
                    ensure_updater(tmp)


if __name__ == '__main__':
    unittest.main()
