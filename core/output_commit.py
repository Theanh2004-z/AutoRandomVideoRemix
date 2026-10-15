"""
Output Commit Module (V3).
Implements atomic 2-phase publication for rendered video and audio files:
Phase 1: Render into an isolated staging file.
Phase 2: Perform OutputValidator inspection; on success, atomically promote/replace
         the staging file into the final published output path.
         On failure, discard the corrupt staging file without touching existing outputs.
"""
import os
import shutil
import uuid
import threading
from typing import Optional, Tuple, Set

from core.output_validator import ValidationResult


class OutputCommit:
    """Manages staging, path reservation, and atomic promotion of rendered files."""

    _reserved_paths: Set[str] = set()
    _reserve_lock = threading.Lock()

    @classmethod
    def reserve_path(cls, path: str) -> bool:
        """Atomically reserves an output path in memory if not already taken or existing on disk."""
        abs_p = os.path.abspath(path)
        with cls._reserve_lock:
            if abs_p in cls._reserved_paths or os.path.exists(abs_p):
                return False
            cls._reserved_paths.add(abs_p)
            return True

    @classmethod
    def release_path(cls, path: str):
        """Releases a previously reserved path."""
        abs_p = os.path.abspath(path)
        with cls._reserve_lock:
            cls._reserved_paths.discard(abs_p)

    @classmethod
    def clear_reservations(cls):
        """Clears all reserved paths."""
        with cls._reserve_lock:
            cls._reserved_paths.clear()

    @staticmethod
    def get_staging_path(final_path: str, temp_dir: Optional[str] = None) -> str:
        """
        Generates a temporary staging file path alongside the final destination or in temp_dir.
        """
        dir_name = temp_dir if temp_dir and os.path.isdir(temp_dir) else os.path.dirname(os.path.abspath(final_path))
        os.makedirs(dir_name, exist_ok=True)
        ext = os.path.splitext(final_path)[1]
        staging_filename = f"_staging_{uuid.uuid4().hex[:8]}{ext}"
        return os.path.join(dir_name, staging_filename)

    @classmethod
    def publish_file(
        cls,
        staging_path: str,
        final_destination_path: str,
        validation: ValidationResult,
        allow_overwrite: bool = False
    ) -> Tuple[bool, str]:
        """
        Atomically publishes the staging file to final_destination_path if validation succeeded.
        Guarantees that an existing user output file is NOT overwritten unless allow_overwrite=True (R05).
        Returns: (success, message)
        """
        if not validation.is_valid:
            cls.discard_staging(staging_path)
            cls.release_path(final_destination_path)
            return False, f"Xác minh file thất bại: {validation.error_msg}"

        if not os.path.isfile(staging_path):
            cls.release_path(final_destination_path)
            return False, f"File staging không tồn tại: {staging_path}"

        dest_abs = os.path.abspath(final_destination_path)

        # Check existing destination
        if os.path.exists(dest_abs) and not allow_overwrite:
            cls.discard_staging(staging_path)
            cls.release_path(final_destination_path)
            return False, f"OUTPUT_CONFLICT: File đích đã tồn tại: {dest_abs}"

        try:
            dest_dir = os.path.dirname(dest_abs)
            os.makedirs(dest_dir, exist_ok=True)

            if allow_overwrite:
                try:
                    os.replace(staging_path, dest_abs)
                except OSError:
                    shutil.move(staging_path, dest_abs)
            else:
                # Non-overwriting publication
                if os.name == 'nt':
                    try:
                        os.rename(staging_path, dest_abs)
                    except FileExistsError:
                        cls.discard_staging(staging_path)
                        cls.release_path(final_destination_path)
                        return False, f"OUTPUT_CONFLICT: File đích đã tồn tại: {dest_abs}"
                    except OSError:
                        if os.path.exists(dest_abs):
                            cls.discard_staging(staging_path)
                            cls.release_path(final_destination_path)
                            return False, f"OUTPUT_CONFLICT: File đích đã tồn tại: {dest_abs}"
                        shutil.move(staging_path, dest_abs)
                else:
                    if os.path.exists(dest_abs):
                        cls.discard_staging(staging_path)
                        cls.release_path(final_destination_path)
                        return False, f"OUTPUT_CONFLICT: File đích đã tồn tại: {dest_abs}"
                    try:
                        os.link(staging_path, dest_abs)
                        os.unlink(staging_path)
                    except (OSError, AttributeError):
                        if os.path.exists(dest_abs):
                            cls.discard_staging(staging_path)
                            cls.release_path(final_destination_path)
                            return False, f"OUTPUT_CONFLICT: File đích đã tồn tại: {dest_abs}"
                        shutil.move(staging_path, dest_abs)

            cls.release_path(final_destination_path)
            return True, f"Công bố thành công: {os.path.basename(dest_abs)}"
        except Exception as e:
            cls.discard_staging(staging_path)
            cls.release_path(final_destination_path)
            return False, f"Lỗi di chuyển file sang đích: {e}"

    @staticmethod
    def discard_staging(staging_path: str):
        """Safely deletes temporary staging file."""
        if staging_path and os.path.isfile(staging_path):
            try:
                os.remove(staging_path)
            except Exception:
                pass
