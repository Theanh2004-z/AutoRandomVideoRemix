"""Create the per-user Windows installer while retaining the update ZIP."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.version import APP_NAME, APP_VERSION, parse_version
from core.app_identity import APP_USER_MODEL_ID
from scripts.build_icon import build_icon
from scripts.build_updater import ensure_updater


def find_iscc(explicit=None):
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f'Không tìm thấy trình biên dịch: {path}')
        return str(path)
    env = os.environ.get('INNO_SETUP_COMPILER')
    if env:
        return find_iscc(env)
    found = shutil.which('ISCC.exe')
    if found:
        return found
    local = PROJECT_ROOT / 'tools' / 'inno' / 'ISCC.exe'
    if local.is_file():
        return str(local)
    for parent in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles'), os.environ.get('LOCALAPPDATA')):
        if parent:
            for folder in ('Inno Setup 7', 'Inno Setup 6', 'Programs/Inno Setup 7', 'Programs/Inno Setup 6'):
                path = Path(parent) / folder / 'ISCC.exe'
                if path.is_file():
                    return str(path)
    raise FileNotFoundError('Cần Inno Setup 6.3+ hoặc 7 để tạo Setup.exe. Cài từ https://jrsoftware.org/isdl.php hoặc đặt INNO_SETUP_COMPILER tới ISCC.exe.')


def build_installer(dist_dir, output_dir, version=APP_VERSION, compiler=None):
    if parse_version(version) is None:
        raise ValueError('Phiên bản không hợp lệ')
    dist = Path(dist_dir).resolve()
    if not (dist / (APP_NAME + '.exe')).is_file() or not (dist / '_internal').is_dir():
        raise FileNotFoundError('Cần bản ứng dụng đã đóng gói đầy đủ gồm EXE và _internal.')
    iscc = find_iscc(compiler)
    build_icon(PROJECT_ROOT)
    ensure_updater(str(dist))
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Isolate each compilation so a failed build cannot return an old Setup.exe.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='remix-setup-', dir=str(output)) as temporary:
        subprocess.run([
            iscc, '/Qp', '/DAppVersion=' + version, '/DDistDir=' + str(dist),
            '/DBuildOutputDir=' + temporary, '/DAppUserModelID=' + APP_USER_MODEL_ID,
            str(PROJECT_ROOT / 'installer' / 'remix.iss'),
        ], cwd=str(PROJECT_ROOT), check=True)
        name = f'{APP_NAME}-{version}-windows-x64-Setup.exe'
        built = Path(temporary) / name
        if not built.is_file() or not built.stat().st_size:
            raise RuntimeError('Trình biên dịch không tạo được Setup.exe.')
        result = output / name
        os.replace(built, result)
    return str(result)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build REMIX Windows Setup')
    parser.add_argument('--dist-dir', default=str(PROJECT_ROOT / 'dist' / APP_NAME))
    parser.add_argument('--output-dir', default=str(PROJECT_ROOT / 'releases'))
    parser.add_argument('--version', default=APP_VERSION)
    parser.add_argument('--iscc')
    args = parser.parse_args()
    try:
        print(build_installer(args.dist_dir, args.output_dir, args.version, args.iscc))
    except Exception as error:
        parser.exit(1, f'Lỗi tạo bộ cài: {error}\n')
