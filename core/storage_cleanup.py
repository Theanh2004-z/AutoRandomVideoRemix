"""Owned scratch workspaces, crash recovery and bounded storage maintenance.

Only marked direct children are removed. An OS lock protects each live workspace
across app instances; links/junctions and unmarked legacy folders are left alone.
"""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid

GIB = 1024 ** 3
MARKER = '.remix-workspace.json'
LOCK = '.remix-workspace.lock'
OWNER = 'AutoRandomVideoRemix/workspace-v1'
NAME = re.compile(r'_temp_(?:remix|audio)_[0-9a-f]{32}')
STAGING = re.compile(r'_staging_[0-9a-f]{8}\.mp4')


def process_alive(pid):
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Access denied/unknown: preserve.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_link(path):
    """Includes Windows junctions/reparse points, not just symbolic links."""
    try:
        value = os.lstat(path)
        return stat.S_ISLNK(value.st_mode) or bool(getattr(value, 'st_file_attributes', 0) & 0x400)
    except OSError:
        return True


def _lock(handle):
    handle.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _contained_tree(path, root):
    """Verify absolute containment before recursive deletion on Windows."""
    path, root = Path(path).absolute(), Path(root).resolve()
    if is_link(path) or path.resolve().parent != root or not NAME.fullmatch(path.name):
        return False
    for parent, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            child = Path(parent)/name
            if is_link(child) or root not in child.resolve().parents:
                return False
    return True


class Workspace:
    def __init__(self, root, kind):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root/f'_temp_{kind}_{uuid.uuid4().hex}'
        self.path.mkdir()
        self.lease = open(self.path/LOCK, 'w+b')
        self.lease.write(b'1'); self.lease.flush()
        _lock(self.lease)
        self.info = {'owner': OWNER, 'created': time.time()}
        self._write_marker()

    def _write_marker(self):
        (self.path/MARKER).write_text(json.dumps(self.info), encoding='utf-8')

    def track_staging(self, path):
        path = Path(path).absolute()
        if path.exists() or is_link(path.parent) or not STAGING.fullmatch(path.name):
            raise ValueError('Invalid or existing output staging path')
        self.info.update(staging=str(path), output_root=str(path.parent.resolve()))
        self._write_marker()

    def close(self):
        if self.lease:
            self.lease.close()
            self.lease = None

    def cleanup(self):
        self.close()
        if self.path.exists() and _contained_tree(self.path, self.root):
            _remove_staging(self.info)
            shutil.rmtree(self.path)


def _remove_staging(info):
    raw = info.get('staging')
    if not raw:
        return
    path = Path(raw).absolute()
    if (STAGING.fullmatch(path.name) and not is_link(path)
            and str(path.parent.resolve()) == info.get('output_root')
            and path.resolve().parent == path.parent.resolve() and path.is_file()):
        path.unlink()


def clean_orphans(root, older_than_hours=24, cancel_check=lambda: False):
    root = Path(root).resolve()
    deleted = 0
    if not root.is_dir():
        return deleted
    for path in root.iterdir():
        if cancel_check():
            break
        lease = None
        try:
            if not NAME.fullmatch(path.name) or not _contained_tree(path, root):
                continue
            if is_link(path/MARKER) or is_link(path/LOCK):
                continue
            info = json.loads((path/MARKER).read_text(encoding='utf-8'))
            if info.get('owner') != OWNER or time.time() - float(info['created']) < older_than_hours*3600:
                continue
            lease = open(path/LOCK, 'r+b')
            _lock(lease)  # A live job in any app instance keeps this lock held.
            if cancel_check():
                break
            _remove_staging(info)
            lease.close(); lease = None
            if _contained_tree(path, root):
                shutil.rmtree(path)
                deleted += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue
        finally:
            if lease:
                lease.close()
    return deleted


def maintain_storage(cancel_check=lambda: False):
    """Run in a worker, never on the GUI thread; never inspect source folders."""
    from core.settings_manager import settings
    from core.app_paths import get_scratch_dir, get_default_scratch_dir, get_cache_dir
    from core.render_cache import RenderCache
    if not settings.get('auto_cleanup', True):
        return {'workspaces': 0}
    roots = {str(Path(p).resolve()) for p in (get_scratch_dir(), get_default_scratch_dir())}
    count = sum(clean_orphans(p, settings.get('temp_retention_hours', 24), cancel_check) for p in roots)
    if not cancel_check():
        cache = RenderCache(settings.get('cache_folder', '') or get_cache_dir(),
            int(settings.get('cache_max_gb', 5))*GIB,
            min_free_bytes=int(settings.get('storage_min_free_gb', 2))*GIB)
        cache.flush()
    return {'workspaces': count}


class SpaceGuard:
    """Stop the owned engine before scratch/output storage is exhausted."""
    def __init__(self, paths, engine):
        from core.settings_manager import settings
        self.minimum = int(settings.get('storage_min_free_gb', 2))*GIB
        self.paths, self.engine = list(dict.fromkeys(map(str, paths))), engine
        self.stop_event = threading.Event()
        self.thread = None

    def check(self):
        for path in self.paths:
            try:
                if shutil.disk_usage(path).free >= self.minimum:
                    continue
                cache = getattr(self.engine, 'render_cache', None)
                if cache:
                    try:
                        cache.flush()
                    except OSError:
                        pass
                if shutil.disk_usage(path).free >= self.minimum:
                    continue
                message = 'Ổ lưu trữ sắp đầy. Đã dừng xử lý; hãy giải phóng dung lượng trước khi xuất lại.'
            except OSError:
                message = 'Không truy cập được ổ lưu trữ. Đã dừng xử lý để bảo vệ kết quả.'
            self.engine.on_log('ERROR', message)
            self.engine.cancel()
            return False
        return True

    def start(self):
        if not self.check():
            return False
        def monitor():
            while not self.stop_event.wait(5):
                try:
                    if not self.check():
                        return
                except OSError:
                    continue
        self.thread = threading.Thread(target=monitor, daemon=True, name='remix-storage')
        self.thread.start()
        return True

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)


def stop_owned_process(engine):
    """Never delete a workspace while its failed subprocess can still write."""
    process = engine._current_subprocess
    if process is None or not hasattr(process, 'poll'):
        return True
    try:
        if process.poll() is None:
            engine.cancel()
            process.wait(timeout=5)
        return process.poll() is not None
    except Exception:
        engine.on_log('WARNING', 'Chưa xác nhận tác vụ đã dừng; giữ lại tệp tạm để tránh xóa dữ liệu đang dùng.')
        return False
