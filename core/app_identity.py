"""Windows shell identity shared by the application, shortcut and installer."""
import atexit
import os
from pathlib import Path
import sys

APP_USER_MODEL_ID = 'Theanh2004z.AutoRandomVideoRemix'
APP_MUTEX_NAME = APP_USER_MODEL_ID
_mutex_handle = None


def icon_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    return root / 'assets' / 'remix.ico'


def configure_windows_identity():
    global _mutex_handle
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
    shell.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    shell.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    if _mutex_handle is None:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        _mutex_handle = kernel.CreateMutexW(None, False, APP_MUTEX_NAME)
        if _mutex_handle:
            atexit.register(kernel.CloseHandle, _mutex_handle)
