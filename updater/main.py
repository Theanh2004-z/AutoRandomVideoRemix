"""Independent updater with directory swaps and recoverable transactions."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

logger = logging.getLogger(__name__)
TERMINAL_STATES = {"committed", "rolled_back", "aborted", "failed"}
BACKUP_MARKER = ".remix-update-backup.json"


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".remix-journal-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_journal(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise ValueError("Invalid update journal")
    return data


def write_journal_state(journal_path, state, details=None):
    if journal_path:
        data = read_journal(journal_path) or {}
        data.update(details or {})
        data.update(state=state, timestamp=time.time())
        atomic_write_json(journal_path, data)


def _kernel():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return kernel


def process_identity(pid):
    """Windows process creation time prevents mistaking a recycled PID."""
    if sys.platform != "win32" or not pid:
        return None
    import ctypes
    from ctypes import wintypes
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
        return None
    finally:
        kernel.CloseHandle(handle)


def wait_for_pid(pid, timeout=30.0, expected_identity=None):
    if int(pid or 0) <= 0:
        return True
    if sys.platform == "win32":
        import ctypes
        kernel = _kernel()
        handle = kernel.OpenProcess(0x100000 | 0x1000, False, int(pid))
        if not handle:
            return ctypes.get_last_error() == 87
        try:
            if expected_identity and process_identity(pid) != expected_identity:
                return True
            return kernel.WaitForSingleObject(handle, int(timeout * 1000)) == 0
        finally:
            kernel.CloseHandle(handle)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(int(pid), 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        time.sleep(0.1)
    return False


def _normal(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))


def _reject_link(path):
    if os.path.lexists(path):
        info = os.lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Update paths cannot be links or junctions")


def _validate_paths(data):
    app = os.path.abspath(data["app_dir"])
    parent = os.path.dirname(app)
    if parent == app or os.path.isdir(os.path.join(app, ".git")):
        raise ValueError("Refusing to replace a disk root or source checkout")
    paths = []
    for key in ("app_dir", "backup_dir", "candidate_dir", "failed_dir"):
        path = data.get(key)
        if path:
            _reject_link(path)
            if _normal(os.path.dirname(path)) != _normal(parent):
                raise ValueError("Transaction paths must stay beside the installed app")
            paths.append(_normal(path))
    if len(paths) != len(set(paths)):
        raise ValueError("Transaction directories must be distinct")
    exe = data.get("executable_name", "AutoRandomVideoRemix.exe")
    if exe != os.path.basename(exe) or exe in ("", ".", ".."):
        raise ValueError("Invalid executable name")


def _remove_tree(path, parent):
    _reject_link(path)
    if _normal(os.path.dirname(path)) != _normal(parent) or _normal(path) == _normal(parent):
        raise ValueError("Cleanup target escaped the transaction parent")
    if os.path.exists(path):
        shutil.rmtree(path)


def clear_directory_contents(target_dir):
    # Compatibility utility; installation swaps never erase the live directory.
    target = Path(target_dir).resolve()
    if target == target.parent or (target / ".git").exists():
        raise ValueError("Unsafe cleanup target")
    _reject_link(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.is_dir() and not child.is_symlink():
            _remove_tree(str(child), str(target))
        else:
            child.unlink()


def copy_or_move_contents(source_dir, target_dir, mode="move"):
    os.makedirs(target_dir, exist_ok=True)
    for entry in os.listdir(source_dir):
        src, dst = os.path.join(source_dir, entry), os.path.join(target_dir, entry)
        if os.path.isdir(dst):
            _remove_tree(dst, target_dir)
        elif os.path.exists(dst):
            os.unlink(dst)
        if mode == "move":
            shutil.move(src, dst)
        elif os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def clean_rollback(backup_dir, app_dir):
    """Restore through a sibling without exposing a half-copied install."""
    token = uuid.uuid4().hex
    parent = os.path.dirname(app_dir)
    candidate = os.path.join(parent, ".remix-restore-" + token)
    failed = os.path.join(parent, ".remix-failed-" + token)
    try:
        _validate_paths(dict(app_dir=app_dir, backup_dir=backup_dir, candidate_dir=candidate, failed_dir=failed))
        if not os.path.isdir(backup_dir):
            return False
        shutil.copytree(backup_dir, candidate)
        if os.path.exists(app_dir):
            os.replace(app_dir, failed)
        try:
            os.replace(candidate, app_dir)
        except Exception:
            if os.path.exists(failed) and not os.path.exists(app_dir):
                os.replace(failed, app_dir)
            raise
        _remove_tree(failed, parent)
        return True
    except Exception:
        logger.exception("Rollback failed; original backup retained")
        return False


@contextmanager
def transaction_lock(journal_path):
    path = journal_path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _recovery_value(journal_path):
    return "RemixRecovery_" + hashlib.sha256(_normal(journal_path).encode()).hexdigest()[:16]


def arm_recovery(journal_path):
    """Resume with the external helper at next sign-in after a power loss."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import winreg
        command = subprocess.list2cmdline([sys.executable, "--recover", "--journal", os.path.abspath(journal_path), "--relaunch"])
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce") as key:
            winreg.SetValueEx(key, _recovery_value(journal_path), 0, winreg.REG_SZ, command)


def disarm_recovery(journal_path):
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\RunOnce", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _recovery_value(journal_path))
        except FileNotFoundError:
            pass
        except OSError:
            # Recovery/commit is already durable. A restricted registry must
            # not turn a successful restoration into a failed transaction.
            logger.warning('Could not remove completed RunOnce recovery entry', exc_info=True)


def _recover_unlocked(journal_path):
    data = read_journal(journal_path)
    if not data or data.get("state") in TERMINAL_STATES:
        disarm_recovery(journal_path)
        return True
    _validate_paths(data)
    app, backup = data["app_dir"], data["backup_dir"]
    exe = data.get("executable_name", "AutoRandomVideoRemix.exe")
    failed = data["failed_dir"]
    write_journal_state(journal_path, "rolling_back")
    if os.path.isdir(backup):
        if not os.path.isfile(os.path.join(backup, exe)):
            raise RuntimeError("Backup incomplete; refusing to replace the app")
        if os.path.exists(app):
            if os.path.exists(failed):
                raise RuntimeError("Previous failed installation needs inspection")
            os.replace(app, failed)
        os.replace(backup, app)
    elif not os.path.isfile(os.path.join(app, exe)):
        raise RuntimeError("Original installation and complete backup are unavailable")
    elif data.get('state') not in ('preparing', 'prepared'):
        marker = read_journal(os.path.join(app, BACKUP_MARKER)) or {}
        if marker.get('transaction_id') != data.get('transaction_id'):
            raise RuntimeError('Original backup missing; recovery needs inspection')
    write_journal_state(journal_path, "rolled_back")
    disarm_recovery(journal_path)
    for name in ("candidate_dir", "failed_dir"):
        try:
            _remove_tree(data[name], os.path.dirname(app))
        except OSError:
            logger.warning("Deferred removal of %s", data[name])
    return True


def recover_transaction(journal_path):
    try:
        with transaction_lock(journal_path):
            return _recover_unlocked(journal_path)
    except Exception:
        logger.exception("Recovery unfinished; backup and journal retained")
        return False


def wait_for_health_handshake(proc, handshake_file, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with open(handshake_file, encoding="utf-8") as stream:
                if stream.read().startswith("HEALTHY"):
                    return True
        except OSError:
            pass
        time.sleep(0.1)
    return False


def _stop_child(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _execute_unlocked(plan, journal_path):
    previous = read_journal(journal_path)
    if previous and previous.get("state") not in TERMINAL_STATES:
        _recover_unlocked(journal_path)
        return False  # Never overwrite the only backup of an interrupted update.
    app = os.path.abspath(plan["app_dir"])
    token = uuid.uuid4().hex
    parent = os.path.dirname(app)
    candidate = os.path.join(parent, "." + os.path.basename(app) + ".new_" + token)
    failed = os.path.join(parent, "." + os.path.basename(app) + ".failed_" + token)
    data = dict(plan, app_dir=app, candidate_dir=candidate, failed_dir=failed,
                transaction_id=token, helper_pid=os.getpid(), helper_identity=process_identity(os.getpid()), journal_file=journal_path)
    _validate_paths(data)
    stage = os.path.abspath(plan["staging_dir"])
    exe = plan.get("executable_name", "AutoRandomVideoRemix.exe")
    if not os.path.isfile(os.path.join(stage, exe)) and os.path.isdir(stage):
        entries = os.listdir(stage)
        if len(entries) == 1 and os.path.isdir(os.path.join(stage, entries[0])):
            stage = os.path.join(stage, entries[0])
    if not os.path.isfile(os.path.join(stage, exe)) or not os.path.isfile(os.path.join(app, exe)):
        return False
    if _normal(stage) == _normal(app) or _normal(stage).startswith(_normal(app) + os.sep):
        raise ValueError("Staging cannot be inside the installation")
    if os.path.exists(plan["backup_dir"]):
        raise ValueError("Backup target already exists; refusing to overwrite")
    if not plan.get("skip_wait") and not wait_for_pid(plan.get("parent_pid", 0), 30, plan.get("parent_identity")):
        return False
    size = 0
    for root, dirs, files in os.walk(stage):
        for entry in dirs + files:
            path = os.path.join(root, entry)
            _reject_link(path)
            if os.path.isfile(path):
                size += os.path.getsize(path)
    if shutil.disk_usage(parent).free < size + 50 * 1024 * 1024:
        raise OSError("Not enough space beside the installed application")
    atomic_write_json(journal_path, dict(data, state="preparing", timestamp=time.time()))
    arm_recovery(journal_path)
    proc = None
    try:
        shutil.copytree(stage, candidate)
        write_journal_state(journal_path, "prepared")
        atomic_write_json(os.path.join(app, BACKUP_MARKER), {
            "app_dir": app, "transaction_id": token, "journal_file": journal_path,
        })
        write_journal_state(journal_path, "swapping")
        os.replace(app, plan["backup_dir"])
        os.replace(candidate, app)
        write_journal_state(journal_path, "installed")
        if plan.get("skip_launch"):
            write_journal_state(journal_path, "committed")
            disarm_recovery(journal_path)
            return True
        handshake = os.path.join(os.path.dirname(journal_path), "handshake_" + token + ".ok")
        write_journal_state(journal_path, "verifying", {"handshake_file": handshake})
        proc = subprocess.Popen([os.path.join(app, exe), "--update-handshake", handshake] + plan.get("restart_args", []), cwd=app)
        write_journal_state(journal_path, "verifying", {"new_pid": proc.pid})
        if not wait_for_health_handshake(proc, handshake, plan.get("handshake_timeout", 30)):
            raise RuntimeError("New application failed its startup handshake")
        write_journal_state(journal_path, "committed")
        disarm_recovery(journal_path)
        return True
    except Exception:
        logger.exception("Update failed; restoring previous installation")
        try:
            _stop_child(proc)
            restored = _recover_unlocked(journal_path)
            if restored and not plan.get('skip_launch'):
                subprocess.Popen([os.path.join(app, exe)] + plan.get('restart_args', []), cwd=app)
        except Exception:
            logger.exception("Recovery remains pending in %s", journal_path)
        return False


def execute_update_transaction(plan):
    journal = plan.get("journal_file") or os.path.join(os.path.dirname(plan["backup_dir"]), "transaction_journal.json")
    try:
        with transaction_lock(journal):
            return _execute_unlocked(plan, journal)
    except Exception:
        logger.exception("Could not start update transaction")
        return False


def main():
    log_dir = os.environ.get("LOCALAPPDATA", tempfile.gettempdir())
    logging.basicConfig(filename=os.path.join(log_dir, "AutoRandomVideoRemix_updater.log"),
                        format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
    parser = argparse.ArgumentParser(description="AutoRandomVideoRemix Updater")
    parser.add_argument("--plan")
    parser.add_argument("--recover", action="store_true")
    parser.add_argument("--journal")
    parser.add_argument("--relaunch", action="store_true")
    parser.add_argument("--wait-pid", type=int, default=0)
    parser.add_argument("--wait-identity")
    args = parser.parse_args()
    try:
        if args.recover:
            if not args.journal:
                parser.error("--recover requires --journal")
            if not wait_for_pid(args.wait_pid, 30, args.wait_identity):
                return 1
            ok = recover_transaction(args.journal)
            data = read_journal(args.journal)
            if not ok and data and data.get('state') not in TERMINAL_STATES:
                arm_recovery(args.journal)
            if ok and args.relaunch and data:
                subprocess.Popen([os.path.join(data["app_dir"], data.get("executable_name", "AutoRandomVideoRemix.exe"))], cwd=data["app_dir"])
            return 0 if ok else 1
        if not args.plan:
            parser.error("--plan or --recover is required")
        with open(args.plan, encoding="utf-8") as stream:
            return 0 if execute_update_transaction(json.load(stream)) else 1
    except Exception:
        logger.exception("Updater could not complete")
        return 1


if __name__ == "__main__":
    sys.exit(main())
