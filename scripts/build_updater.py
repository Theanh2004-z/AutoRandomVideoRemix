"""Build the standalone helper in a temporary workspace; fail closed on errors."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_updater(dist_dir):
    dist = Path(dist_dir).resolve()
    source = PROJECT_ROOT / 'updater' / 'main.py'
    exe = dist / 'updater.exe'
    stamp = dist / 'updater.build.json'
    digest = _sha(source)
    try:
        saved = json.loads(stamp.read_text(encoding='utf-8'))
        if saved.get('source_sha256') == digest and saved.get('binary_sha256') == _sha(exe):
            return str(exe)
    except (OSError, ValueError, TypeError):
        pass
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='remix-updater-build-') as temporary:
        workspace = Path(temporary)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([
            sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onefile', '--windowed', '--name', 'updater',
            '--distpath', str(workspace / 'dist'), '--workpath', str(workspace / 'build'),
            '--specpath', temporary, str(source),
        ], cwd=temporary, env=env, check=True)
        built = workspace / 'dist' / 'updater.exe'
        if not built.is_file() or built.stat().st_size == 0:
            raise RuntimeError('Không tạo được updater.exe; dừng đóng gói bản phát hành.')
        temporary_exe = dist / 'updater.exe.tmp'
        try:
            shutil.copy2(built, temporary_exe)
            os.replace(temporary_exe, exe)
        finally:
            if temporary_exe.exists():
                temporary_exe.unlink()
    stamp.write_text(json.dumps({'source_sha256': digest, 'binary_sha256': _sha(exe)}, indent=2), encoding='utf-8')
    return str(exe)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: build_updater.py DIST_DIRECTORY')
    ensure_updater(sys.argv[1])
