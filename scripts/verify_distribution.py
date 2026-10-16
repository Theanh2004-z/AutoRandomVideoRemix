"""Reject conflicting Windows DLLs and prove the frozen GUI starts before release."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


# Qt uses the Windows ICU API (Windows 10 1703+). A same-named DLL picked
# from another tool on PATH can instead expose version-suffixed ICU symbols.
# Do not ship those OS components; keep versioned third-party ICU DLLs intact.
WINDOWS_ICU_DLLS = frozenset({'icu.dll', 'icuuc.dll', 'icuin.dll'})


def filter_system_icu(binaries):
    return [entry for entry in binaries
            if Path(entry[0]).name.lower() not in WINDOWS_ICU_DLLS]


def verify_distribution(dist_dir, timeout=45):
    dist = Path(dist_dir).resolve()
    exe = dist / 'AutoRandomVideoRemix.exe'
    internal = dist / '_internal'
    if not exe.is_file() or not internal.is_dir():
        raise FileNotFoundError('Bản đóng gói thiếu EXE hoặc thư mục _internal.')
    conflicts = [p for p in internal.rglob('*')
                 if p.is_file() and p.name.lower() in WINDOWS_ICU_DLLS]
    if conflicts:
        raise RuntimeError('Bản đóng gói chứa DLL ICU có thể xung đột với Windows: '
                           + ', '.join(str(p.relative_to(dist)) for p in conflicts))
    with tempfile.TemporaryDirectory(prefix='remix-startup-') as temporary:
        root = Path(temporary)
        appdata = root / 'appdata'
        settings = appdata / 'AutoRandomVideoRemix' / 'user_settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            'auto_save': False, 'video_folder': '', 'audio_folder': '',
            'output_folder': str(root / 'output'), 'check_updates_on_startup': False,
            'auto_cleanup': False,
        }), encoding='utf-8')
        handshake = root / 'healthy.ok'
        env = dict(os.environ, APPDATA=str(appdata),
                   LOCALAPPDATA=str(root / 'local'), QT_QPA_PLATFORM='offscreen')
        # Do not let plugins from the build machine satisfy missing bundle files.
        for key in ('QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'PYTHONPATH', 'PYTHONHOME'):
            env.pop(key, None)
        windows = Path(os.environ.get('SystemRoot', 'C:/Windows'))
        env['PATH'] = os.pathsep.join(map(str, (windows / 'System32', windows)))
        with (root / 'startup.log').open('w+', encoding='utf-8', errors='replace') as log:
            process = subprocess.Popen(
                [str(exe), '--update-handshake', str(handshake)],
                cwd=root, env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            try:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and process.poll() is None:
                    if handshake.exists():
                        time.sleep(.5)
                        if process.poll() is None and handshake.read_text().startswith('HEALTHY v'):
                            return
                        break
                    time.sleep(.1)
                log.flush()
                log.seek(0)
                details = log.read()[-6000:]
                raise RuntimeError('EXE không vượt qua kiểm tra khởi động '
                                   f'(exit={process.poll()}).\n{details}')
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Verify a frozen REMIX application')
    parser.add_argument('dist_dir')
    args = parser.parse_args()
    try:
        verify_distribution(args.dist_dir)
        print('EXE startup verified with isolated settings and system-only PATH.')
    except Exception as error:
        parser.exit(1, f'Lỗi bản đóng gói: {error}\n')
