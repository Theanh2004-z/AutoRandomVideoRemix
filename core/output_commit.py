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
from typing import Optional, Tuple

from core.output_validator import ValidationResult


class OutputCommit:
    """Manages staging and atomic promotion of rendered files."""

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
        validation: ValidationResult
    ) -> Tuple[bool, str]:
        """
        Atomically publishes the staging file to final_destination_path if validation succeeded.
        Returns: (success, message)
        """
        if not validation.is_valid:
            # Discard staging file
            cls.discard_staging(staging_path)
            return False, f"Xác minh file thất bại: {validation.error_msg}"

        if not os.path.isfile(staging_path):
            return False, f"File staging không tồn tại: {staging_path}"

        try:
            dest_dir = os.path.dirname(os.path.abspath(final_destination_path))
            os.makedirs(dest_dir, exist_ok=True)

            # Atomic replace on same filesystem; fallback to shutil.move across drives
            try:
                os.replace(staging_path, final_destination_path)
            except OSError:
                shutil.move(staging_path, final_destination_path)

            return True, f"Công bố thành công: {os.path.basename(final_destination_path)}"
        except Exception as e:
            cls.discard_staging(staging_path)
            return False, f"Lỗi di chuyển file sang đích: {e}"

    @staticmethod
    def discard_staging(staging_path: str):
        """Safely deletes temporary staging file."""
        if staging_path and os.path.isfile(staging_path):
            try:
                os.remove(staging_path)
            except Exception:
                pass
