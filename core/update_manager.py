"""
Update Manager Module.
Coordinates the update lifecycle:
  1. Check for new releases in background.
  2. Download distribution package with progress, integrity checksum (SHA-256), and cancellation.
  3. Pre-flight multi-drive disk space and path validation.
  4. Safe ZIP extraction with Zip-Slip protection into staging directory.
  5. Preparation of atomic update transaction plan with journal state tracking.
  6. Dispatch of isolated updater helper process and application restart.
  7. Resumption of pending updates and cleanup policy for old update artifacts.
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from core.app_paths import get_app_dir, get_updates_dir
from core.settings_manager import settings
from core.update_service import ReleaseInfo, UpdateCheckResult, check_for_updates
from core.version import APP_NAME, APP_VERSION, is_newer_version
from updater.main import atomic_write_json, read_journal, process_identity, wait_for_pid, TERMINAL_STATES, BACKUP_MARKER

logger = logging.getLogger(__name__)

# State constants
STATE_IDLE = "idle"
STATE_CHECKING = "checking"
STATE_UPDATE_AVAILABLE = "update_available"
STATE_UP_TO_DATE = "up_to_date"
STATE_DOWNLOADING = "downloading"
STATE_CANCELLING = "cancelling"
STATE_VERIFYING = "verifying"
STATE_EXTRACTING = "extracting"
STATE_READY_TO_INSTALL = "ready_to_install"
STATE_INSTALLING = "installing"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"


def get_free_disk_space_bytes(path: str) -> int:
    """Returns available free disk space in bytes for the specified path."""
    try:
        total, used, free = shutil.disk_usage(path)
        return free
    except Exception:
        return 10 * 1024 * 1024 * 1024


def get_directory_size_bytes(directory: str) -> int:
    """Calculates total size of all files in a directory."""
    total = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_directory_size_bytes(entry.path)
    except Exception:
        pass
    return total


def safe_extract_zip(zip_path: str, target_dir: str, progress_callback: Optional[Callable[[int, int], None]] = None) -> None:
    """
    Safely extracts all members of a ZIP file into target_dir.
    Protects against Zip Slip (path traversal) vulnerabilities.
    """
    os.makedirs(target_dir, exist_ok=True)
    target_dir_abs = os.path.abspath(target_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()
        total = len(members)

        for idx, member in enumerate(members):
            norm_name = os.path.normpath(member.filename)
            if norm_name.startswith("..") or os.path.isabs(norm_name) or ".." in norm_name.split(os.sep):
                raise ValueError(f"Bảo mật: Tệp nén chứa đường dẫn nguy hiểm ({member.filename}). Quá trình giải nén bị hủy.")

            dest_path = os.path.abspath(os.path.join(target_dir, norm_name))
            if not (dest_path == target_dir_abs or dest_path.startswith(target_dir_abs + os.sep)):
                raise ValueError(f"Bảo mật: Tệp nén cố tình ghi ngoài thư mục đích ({member.filename}).")

            zf.extract(member, target_dir)
            if progress_callback:
                progress_callback(idx + 1, total)


class UpdateManager(QObject):
    """
    Central update controller exposing Qt signals for UI binding and workflow management.
    """
    state_changed = Signal(str, str)  # (state, message)
    check_finished = Signal(bool, object)  # (is_available, ReleaseInfo or error_str)
    download_progress = Signal(int, int, float)  # (downloaded_bytes, total_bytes, percent: 0-100)
    download_finished = Signal(str)  # (zip_file_path)
    extract_progress = Signal(int, int)  # (current, total)
    extract_finished = Signal(str)  # (staging_dir)
    ready_to_install = Signal(dict)  # (install_plan)
    error_occurred = Signal(str)  # (error_message)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._state = STATE_IDLE
        self._current_release: Optional[ReleaseInfo] = None
        self._is_export_busy_callback: Optional[Callable[[], bool]] = None
        self._cancel_event = threading.Event()
        self._download_thread: Optional[threading.Thread] = None
        self._check_thread: Optional[threading.Thread] = None
        self._downloaded_zip_path: Optional[str] = None
        self._staged_dir: Optional[str] = None
        self._install_plan: Optional[Dict[str, Any]] = None
        self._last_error: str = ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def current_release(self) -> Optional[ReleaseInfo]:
        return self._current_release

    def is_running_from_source(self) -> bool:
        """Returns True if the application is running from source files rather than a PyInstaller package."""
        return not getattr(sys, "frozen", False)

    def set_export_busy_callback(self, callback: Callable[[], bool]):
        """Injects a callback to check if video rendering is currently active."""
        self._is_export_busy_callback = callback

    def is_export_busy(self) -> bool:
        """Checks if video export is currently running."""
        if self._is_export_busy_callback:
            try:
                return bool(self._is_export_busy_callback())
            except Exception as e:
                logger.error("Error executing export_busy_callback: %s", e)
        return False

    def _set_state(self, new_state: str, message: str = ""):
        self._state = new_state
        if new_state == STATE_ERROR:
            self._last_error = message
        logger.info("UpdateManager state -> %s (%s)", new_state, message)
        self.state_changed.emit(new_state, message)

    def check_updates_async(self, allow_prerelease: bool = False):
        """Checks for updates in a background thread."""
        if self._state in (STATE_CHECKING, STATE_DOWNLOADING, STATE_CANCELLING, STATE_EXTRACTING, STATE_INSTALLING):
            logger.warning("Update check requested while another operation is in progress: %s", self._state)
            return

        self._set_state(STATE_CHECKING, "Đang kiểm tra bản cập nhật...")

        def _worker():
            try:
                result = check_for_updates(allow_prerelease=allow_prerelease)
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                settings.update({"last_update_check": now_str}, auto_save=True)

                if result.is_available and result.release_info:
                    self._current_release = result.release_info
                    self._set_state(STATE_UPDATE_AVAILABLE, f"Đã có phiên bản mới v{result.release_info.version}")
                    self.check_finished.emit(True, result.release_info)
                elif result.status == "up_to_date":
                    self._current_release = result.release_info
                    self._set_state(STATE_UP_TO_DATE, "Bạn đang sử dụng phiên bản mới nhất.")
                    self.check_finished.emit(False, result.release_info)
                else:
                    err = result.error_message or "Không thể kiểm tra cập nhật."
                    self._set_state(STATE_ERROR, err)
                    self.error_occurred.emit(err)
                    self.check_finished.emit(False, err)
            except Exception as e:
                logger.exception("Exception in update check worker: %s", e)
                err = f"Lỗi kiểm tra cập nhật: {str(e)}"
                self._set_state(STATE_ERROR, err)
                self.error_occurred.emit(err)
                self.check_finished.emit(False, err)
            finally:
                self._check_thread = None

        thread = threading.Thread(target=_worker, name="UpdateCheckWorker", daemon=True)
        self._check_thread = thread
        thread.start()

    def cancel_operation(self):
        """Cancels an in-progress download safely and cleans up."""
        if self._state == STATE_DOWNLOADING and self._download_thread and self._download_thread.is_alive():
            logger.info("Signaling download worker to cancel...")
            self._cancel_event.set()
            self._set_state(STATE_CANCELLING, "Đang hủy và dọn dẹp tệp tải...")

    def check_pending_update(self) -> Optional[Dict[str, Any]]:
        """
        Inspects updates_dir for a previously downloaded and staged update plan.
        If valid and newer than current version, restores readiness state.
        """
        updates_dir = get_updates_dir()
        plan_path = os.path.join(updates_dir, "install_plan.json")
        if not os.path.isfile(plan_path):
            return None

        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)

            # AppData is shared by portable installs; never apply another copy's plan.
            if not self._plan_targets_current_install(plan):
                return None

            new_version = plan.get("new_version", "")
            if not is_newer_version(new_version, APP_VERSION):
                # Staged version is not newer than current app version
                logger.info("Found obsolete pending install plan for v%s (current is v%s). Removing.", new_version, APP_VERSION)
                os.remove(plan_path)
                return None

            staging_dir = plan.get("staging_dir", "")
            exe_name = plan.get("executable_name", f"{APP_NAME}.exe")
            target_exe = os.path.join(staging_dir, exe_name)

            # Check nested staging
            if not os.path.isfile(target_exe) and os.path.isdir(staging_dir):
                entries = os.listdir(staging_dir)
                if len(entries) == 1 and os.path.isdir(os.path.join(staging_dir, entries[0])):
                    target_exe = os.path.join(staging_dir, entries[0], exe_name)

            if not os.path.isfile(target_exe):
                logger.warning("Pending install plan points to missing staged executable: %s", target_exe)
                os.remove(plan_path)
                return None

            self._install_plan = plan
            self._staged_dir = staging_dir
            self._set_state(STATE_READY_TO_INSTALL, f"Bản cập nhật v{new_version} đã sẵn sàng cài đặt.")
            return plan

        except Exception as e:
            logger.error("Failed to read pending install plan: %s", e)
            return None

    @staticmethod
    def _plan_targets_current_install(plan):
        expected = os.path.normcase(os.path.realpath(get_app_dir()))
        actual = plan.get("app_dir")
        return bool(actual and os.path.normcase(os.path.realpath(actual)) == expected)

    def cleanup_old_updates(self, keep_latest_backup: bool = True):
        """Keep pending payloads and active recovery; remove only owned artifacts."""
        root = os.path.realpath(get_updates_dir())
        if self._download_thread and self._download_thread.is_alive():
            return
        try:
            journal = read_journal(os.path.join(root, "transaction_journal.json"))
            if journal and journal.get("state") not in TERMINAL_STATES:
                return  # Even a crashed transaction still owns its recovery files.
            plan = read_journal(os.path.join(root, "install_plan.json")) or {}
            if self._plan_targets_current_install(plan) and not is_newer_version(plan.get("new_version", ""), APP_VERSION):
                plan = {}
            protected = {os.path.normcase(os.path.realpath(path)) for path in (
                plan.get("staging_dir"), plan.get("archive_path"), self._staged_dir,
                self._downloaded_zip_path if self._state == STATE_READY_TO_INSTALL else None,
            ) if path}
            if plan.get("new_version"):
                protected.add(os.path.normcase(os.path.join(root, f"{APP_NAME}-{plan['new_version']}.zip")))
            owned_zip = re.compile(re.escape(APP_NAME) + r"-\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?\.zip(?:\.part)?$")
            legacy_backup = re.compile(r"backup_\d+\.\d+\.\d+_\d+$")
            backups = []
            def remove_owned(path):
                # Junctions and symlinks must never turn cleanup into an external delete.
                info = os.lstat(path)
                if os.path.islink(path) or getattr(info, 'st_file_attributes', 0) & 0x400:
                    return
                if os.path.dirname(os.path.realpath(path)) != os.path.realpath(os.path.dirname(path)):
                    return
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            for entry in os.listdir(root):
                path = os.path.join(root, entry)
                if os.path.normcase(os.path.realpath(path)) in protected:
                    continue
                if owned_zip.fullmatch(entry):
                    if entry.endswith('.part') and time.time() - os.path.getmtime(path) < 86400:
                        continue
                    remove_owned(path)
                elif entry.startswith('staging_') and is_newer_version(entry[8:], '0.0.0'):
                    remove_owned(path)
                elif legacy_backup.fullmatch(entry) and os.path.isdir(path):
                    backups.append(path)
                elif entry.startswith('handshake_') and entry.endswith('.ok'):
                    remove_owned(path)
            # Directory swaps keep backups on the install volume, not the download drive.
            app = os.path.realpath(get_app_dir())
            parent = os.path.dirname(app)
            prefix = '.' + os.path.basename(app) + '.backup_'
            for entry in os.listdir(parent):
                if not entry.startswith(prefix) or not re.fullmatch(r'[0-9a-f]{32}', entry[len(prefix):]):
                    continue
                path = os.path.join(parent, entry)
                marker = read_journal(os.path.join(path, BACKUP_MARKER))
                if marker and os.path.normcase(os.path.realpath(marker.get('app_dir', ''))) == os.path.normcase(app):
                    backups.append(path)
            backups.sort(key=os.path.getmtime, reverse=True)
            for path in backups[1:] if keep_latest_backup else backups:
                remove_owned(path)
        except Exception:
            logger.exception("Could not finish update retention cleanup")

    def download_update_async(self, release_info: Optional[ReleaseInfo] = None):
        """Downloads the release distribution zip file asynchronously."""
        target_release = release_info or self._current_release
        if not target_release:
            err = "Không có thông tin bản phát hành để tải về."
            self._set_state(STATE_ERROR, err)
            self.error_occurred.emit(err)
            return

        if not target_release.zip_asset or not target_release.zip_asset.download_url:
            err = f"Không tìm thấy gói cài đặt tương thích cho hệ điều hành và kiến trúc của máy trong bản phát hành v{target_release.version}."
            self._set_state(STATE_ERROR, err)
            self.error_occurred.emit(err)
            return

        # Prevent concurrent downloads
        if self._download_thread and self._download_thread.is_alive():
            logger.warning("Download requested while download worker is still running.")
            return

        if self._state in (STATE_DOWNLOADING, STATE_CANCELLING, STATE_EXTRACTING, STATE_INSTALLING):
            logger.warning("Download requested while operation is active: %s", self._state)
            return

        updates_dir = get_updates_dir()
        app_dir = get_app_dir()
        os.makedirs(updates_dir, exist_ok=True)

        # Pre-flight multi-drive disk space check:
        # 1. Updates drive: ZIP download + extracted staging + backup of current app + margin
        zip_size = target_release.zip_asset.size or (60 * 1024 * 1024)
        current_app_size = get_directory_size_bytes(app_dir)
        required_updates_space = int(zip_size + (zip_size * 2.5) + current_app_size + (100 * 1024 * 1024))
        free_updates_space = get_free_disk_space_bytes(updates_dir)

        if free_updates_space < required_updates_space:
            err = (
                f"Dung lượng trống trên ổ lưu bản cập nhật ({free_updates_space // (1024 * 1024)} MB) "
                f"không đủ. Cần tối thiểu {required_updates_space // (1024 * 1024)} MB."
            )
            self._set_state(STATE_ERROR, err)
            self.error_occurred.emit(err)
            return

        # 2. App directory drive: space for swapped files + margin
        required_app_space = int((zip_size * 2.5) + (50 * 1024 * 1024))
        free_app_space = get_free_disk_space_bytes(app_dir)
        if free_app_space < required_app_space:
            err = (
                f"Dung lượng trống trên ổ cài đặt ứng dụng ({free_app_space // (1024 * 1024)} MB) "
                f"không đủ để thay thế tệp. Cần tối thiểu {required_app_space // (1024 * 1024)} MB."
            )
            self._set_state(STATE_ERROR, err)
            self.error_occurred.emit(err)
            return

        self._set_state(STATE_DOWNLOADING, "Đang tải bản cập nhật...")
        self._cancel_event.clear()

        zip_asset = target_release.zip_asset
        dest_filename = f"{APP_NAME}-{target_release.version}.zip"
        dest_zip = os.path.join(updates_dir, dest_filename)
        dest_part = dest_zip + ".part"

        def _worker(cancel_event: threading.Event, release: ReleaseInfo):
            part_cleaned = False
            try:
                headers = {"User-Agent": f"{APP_NAME}-Updater/{APP_VERSION}"}
                req = urllib.request.Request(zip_asset.download_url, headers=headers)

                hasher = hashlib.sha256()
                downloaded = 0
                chunk_size = 64 * 1024  # 64 KB

                with urllib.request.urlopen(req, timeout=30.0) as resp:
                    total_bytes = int(resp.headers.get("Content-Length", 0)) or zip_asset.size

                    with open(dest_part, "wb") as f_out:
                        while True:
                            if cancel_event.is_set():
                                logger.info("Download cancel signaled.")
                                break
                            chunk = resp.read(chunk_size)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            hasher.update(chunk)
                            downloaded += len(chunk)
                            percent = (downloaded / total_bytes * 100.0) if total_bytes > 0 else 0.0
                            self.download_progress.emit(downloaded, total_bytes, percent)

                if cancel_event.is_set():
                    return

                # Check actual downloaded size vs expected size
                actual_size = os.path.getsize(dest_part)
                expected_size = release.zip_asset.size
                if expected_size and expected_size > 0 and actual_size != expected_size:
                    if os.path.exists(dest_part):
                        os.remove(dest_part)
                        part_cleaned = True
                    err = (
                        f"Dung lượng tệp tải về ({actual_size} bytes) không khớp với thông tin phát hành "
                        f"({expected_size} bytes). Tệp có thể tải chưa xong hoặc bị gián đoạn."
                    )
                    self._set_state(STATE_ERROR, err)
                    self.error_occurred.emit(err)
                    return

                # Download finished, rename .part to .zip atomically
                if os.path.exists(dest_zip):
                    os.remove(dest_zip)
                os.replace(dest_part, dest_zip)
                self._downloaded_zip_path = dest_zip

                # Mandatory SHA256 integrity check
                calculated_hash = hasher.hexdigest().lower()
                expected_hash = (release.sha256 or "").strip().lower()

                if not expected_hash:
                    # Release must have SHA-256 for security
                    if os.path.exists(dest_zip):
                        os.remove(dest_zip)
                    err = "Bản phát hành thiếu mã kiểm tra toàn vẹn SHA-256. Để đảm bảo an toàn, ứng dụng từ chối cài đặt bản cập nhật không có mã xác thực."
                    self._set_state(STATE_ERROR, err)
                    self.error_occurred.emit(err)
                    return

                self._set_state(STATE_VERIFYING, "Đang kiểm tra tính toàn vẹn (SHA-256)...")
                if calculated_hash != expected_hash:
                    if os.path.exists(dest_zip):
                        os.remove(dest_zip)
                    err = (
                        f"Mã băm SHA-256 không khớp! Tệp có thể đã bị lỗi hoặc can thiệp.\n"
                        f"Kỳ vọng: {expected_hash}\nThực tế: {calculated_hash}"
                    )
                    self._set_state(STATE_ERROR, err)
                    self.error_occurred.emit(err)
                    return

                logger.info("Download completed and verified: %s", dest_zip)
                self.download_finished.emit(dest_zip)

                # Proceed to extract into staging
                self._extract_and_stage(dest_zip, release.version)

            except Exception as e:
                logger.exception("Error during update download: %s", e)
                if not part_cleaned and os.path.exists(dest_part):
                    try:
                        os.remove(dest_part)
                    except Exception:
                        pass
                err = f"Lỗi khi tải bản cập nhật: {str(e)}"
                self._set_state(STATE_ERROR, err)
                self.error_occurred.emit(err)

            finally:
                if cancel_event.is_set():
                    if os.path.exists(dest_part):
                        try:
                            os.remove(dest_part)
                        except Exception:
                            pass
                    self._set_state(STATE_CANCELLED, "Đã hủy tải bản cập nhật.")
                self._download_thread = None

        thread = threading.Thread(
            target=_worker,
            args=(self._cancel_event, target_release),
            name="UpdateDownloadWorker",
            daemon=True
        )
        self._download_thread = thread
        thread.start()

    def _extract_and_stage(self, zip_path: str, version: str):
        """Extracts the downloaded ZIP into a staging folder with Zip-Slip protection."""
        self._set_state(STATE_EXTRACTING, "Đang giải nén gói cập nhật...")
        updates_dir = get_updates_dir()
        staging_dir = os.path.join(updates_dir, f"staging_{version}")

        # Clean prior staging folder if present
        if os.path.exists(staging_dir):
            try:
                shutil.rmtree(staging_dir)
            except Exception as e:
                logger.warning("Could not clear prior staging dir %s: %s", staging_dir, e)

        def _prog(curr, total):
            self.extract_progress.emit(curr, total)

        try:
            safe_extract_zip(zip_path, staging_dir, progress_callback=_prog)
            self._staged_dir = staging_dir
            logger.info("Extraction successful into staging: %s", staging_dir)
            self.extract_finished.emit(staging_dir)

            # Build install plan
            plan = self.prepare_install_plan(staging_dir, version)
            self._install_plan = plan
            self._set_state(STATE_READY_TO_INSTALL, f"Bản cập nhật v{version} đã sẵn sàng cài đặt.")
            self.ready_to_install.emit(plan)

        except Exception as e:
            logger.exception("Error extracting update package: %s", e)
            err = f"Lỗi giải nén bản cập nhật: {str(e)}"
            self._set_state(STATE_ERROR, err)
            self.error_occurred.emit(err)

    @staticmethod
    def _restart_arguments():
        args = []
        original = iter(sys.argv[1:])
        for item in original:
            if item == '--update-handshake':
                next(original, None)
            else:
                args.append(item)
        return args

    def prepare_install_plan(self, staging_dir: str, new_version: str) -> Dict[str, Any]:
        app_dir = os.path.realpath(get_app_dir())
        updates_dir = get_updates_dir()
        token = uuid.uuid4().hex
        plan = {
            'schema_version': 2,
            'transaction_id': token,
            'parent_pid': os.getpid(),
            'parent_identity': process_identity(os.getpid()),
            'app_dir': app_dir,
            'staging_dir': os.path.realpath(staging_dir),
            'backup_dir': os.path.join(os.path.dirname(app_dir), '.' + os.path.basename(app_dir) + '.backup_' + token),
            'executable_name': APP_NAME + ('.exe' if sys.platform == 'win32' else ''),
            'current_version': APP_VERSION,
            'new_version': new_version,
            'restart_args': self._restart_arguments(),
            'archive_path': self._downloaded_zip_path,
            'journal_file': os.path.join(updates_dir, 'transaction_journal.json'),
            'handshake_timeout': 30.0,
            'created_at': datetime.now().isoformat(),
            'is_frozen': getattr(sys, 'frozen', False),
            'plan_file': os.path.join(updates_dir, 'install_plan.json'),
        }
        atomic_write_json(plan['plan_file'], plan)
        return plan

    def _isolated_runner(self):
        app_dir = get_app_dir()
        source = next((path for path in (
            os.path.join(app_dir, 'updater.exe'),
            os.path.join(app_dir, '_internal', 'updater.exe'),
        ) if os.path.isfile(path)), None)
        if not source:
            raise RuntimeError('Bản ứng dụng thiếu updater.exe. Hãy tải lại gói phát hành đầy đủ.')
        runner_dir = os.path.join(get_updates_dir(), 'updater_runner')
        os.makedirs(runner_dir, exist_ok=True)
        runner = os.path.join(runner_dir, 'updater.exe')
        temporary = runner + '.' + uuid.uuid4().hex + '.tmp'
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, runner)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return runner

    @staticmethod
    def _launch_helper(cmd):
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if sys.platform == 'win32' else 0
        return subprocess.Popen(cmd, cwd=os.path.dirname(cmd[0]), creationflags=flags, close_fds=True)

    def recover_interrupted_update(self):
        """Bootstrap recovery before constructing windows; the helper waits for us."""
        if self.is_running_from_source() or '--update-handshake' in sys.argv:
            return False
        journal_path = os.path.join(get_updates_dir(), 'transaction_journal.json')
        journal = read_journal(journal_path)
        if not journal or journal.get('state') in TERMINAL_STATES or not self._plan_targets_current_install(journal):
            return False
        # Another helper may still be completing the current transaction.
        pid = journal.get('helper_pid', 0)
        if pid and not wait_for_pid(pid, 0, journal.get('helper_identity')):
            return True
        runner = self._isolated_runner()
        cmd = [runner, '--recover', '--journal', journal_path, '--relaunch', '--wait-pid', str(os.getpid())]
        identity = process_identity(os.getpid())
        if identity:
            cmd += ['--wait-identity', identity]
        self._launch_helper(cmd)
        return True

    def apply_update_and_restart(self) -> bool:
        try:
            if self.is_running_from_source():
                raise RuntimeError('Tự động cài đặt chỉ áp dụng cho bản EXE đã đóng gói; mã nguồn được giữ nguyên.')
            if self.is_export_busy():
                raise RuntimeError('Vui lòng chờ lượt xuất hoàn tất trước khi cập nhật.')
            if not self._install_plan or not self._staged_dir:
                raise RuntimeError('Chưa có bản cập nhật đã chuẩn bị.')
            if not self._plan_targets_current_install(self._install_plan):
                raise RuntimeError('Gói đang chờ thuộc một bản cài khác. Hãy kiểm tra và tải lại bản cập nhật.')
            journal = read_journal(os.path.join(get_updates_dir(), 'transaction_journal.json'))
            if journal and journal.get('state') not in TERMINAL_STATES:
                raise RuntimeError('Cần hoàn tất phục hồi lần cập nhật trước trước khi cài tiếp.')
            runner = self._isolated_runner()
            # A persisted plan is data, never a persisted authority to wait on an old PID.
            plan = self.prepare_install_plan(self._staged_dir, self._install_plan['new_version'])
            self._install_plan = plan
            self._set_state(STATE_INSTALLING, 'Đang đóng ứng dụng để cập nhật...')
            self._launch_helper([runner, '--plan', plan['plan_file']])
            from PySide6.QtCore import QTimer
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app:
                QTimer.singleShot(0, app.closeAllWindows)
            return True
        except Exception as error:
            message = f'Không thể bắt đầu cập nhật: {error}'
            logger.exception(message)
            self._set_state(STATE_ERROR, message)
            self.error_occurred.emit(message)
            return False


update_manager = UpdateManager()
