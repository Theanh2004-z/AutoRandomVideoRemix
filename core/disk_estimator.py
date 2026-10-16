"""
Disk Space Estimator Module (V3).
Pre-flight calculation of available storage on target drive vs required space
for video encoding, intermediate segment cutting, and concatenation.
"""
import os
import shutil
from dataclasses import dataclass
from typing import Optional

from core.errors import InsufficientDiskSpaceError


def format_bytes(bytes_val: int) -> str:
    """Format integer bytes into a human-readable string (KB, MB, GB)."""
    if bytes_val < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    val = float(bytes_val)
    unit_idx = 0
    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1
    return f"{val:.2f} {units[unit_idx]}"


@dataclass(frozen=True)
class DiskSpaceCheckResult:
    """Result of pre-flight disk space verification."""
    is_sufficient: bool
    required_bytes: int
    available_bytes: int
    free_after_export_bytes: int
    message: str

    @property
    def formatted_required(self) -> str:
        return format_bytes(self.required_bytes)

    @property
    def formatted_available(self) -> str:
        return format_bytes(self.available_bytes)

    @property
    def formatted_free_after(self) -> str:
        return format_bytes(self.free_after_export_bytes)


class DiskEstimator:
    """Calculates disk space requirements and verifies target disk capacity."""

    # Approximate target bitrates in bps (bits per second)
    BITRATE_TABLE = {
        "4k": 28_000_000,      # ~28 Mbps
        "1440p": 16_000_000,   # ~16 Mbps
        "1080p": 8_000_000,    # ~8 Mbps
        "720p": 4_500_000,     # ~4.5 Mbps
        "sd": 2_000_000,       # ~2 Mbps
        "audio_only": 256_000  # ~256 kbps
    }

    @classmethod
    def determine_video_bitrate(cls, width: int, height: int) -> int:
        """Determines expected video bitrate in bits per second based on resolution."""
        pixels = width * height
        if pixels >= 3840 * 2160 * 0.8:
            return cls.BITRATE_TABLE["4k"]
        elif pixels >= 2560 * 1440 * 0.8:
            return cls.BITRATE_TABLE["1440p"]
        elif pixels >= 1920 * 1080 * 0.8:
            return cls.BITRATE_TABLE["1080p"]
        elif pixels >= 1280 * 720 * 0.8:
            return cls.BITRATE_TABLE["720p"]
        else:
            return cls.BITRATE_TABLE["sd"]

    @classmethod
    def estimate_required_bytes(
        cls,
        total_duration_sec: float,
        width: int = 1920,
        height: int = 1080,
        is_audio_only: bool = False,
        safety_factor: float = 2.0
    ) -> int:
        """
        Calculates estimated bytes required for the entire export.
        Safety factor accounts for:
        1. Temporary individual segment cuts.
        2. Seamless concatenated master output.
        3. Staging and audio muxing workspace.
        """
        if total_duration_sec <= 0:
            return 0

        if is_audio_only:
            audio_bitrate = cls.BITRATE_TABLE["audio_only"]
            raw_bytes = int((audio_bitrate * total_duration_sec) / 8)
        else:
            video_bitrate = cls.determine_video_bitrate(width, height)
            audio_bitrate = 192_000  # 192 kbps AAC
            total_bitrate = video_bitrate + audio_bitrate
            raw_bytes = int((total_bitrate * total_duration_sec) / 8)

        return int(raw_bytes * safety_factor)

    @classmethod
    def _get_available_bytes(cls, path: str) -> int:
        """Helper to safely query available bytes for a filesystem path."""
        abs_target = os.path.abspath(path)
        check_path = abs_target

        while not os.path.exists(check_path):
            parent = os.path.dirname(check_path)
            if parent == check_path:
                break
            check_path = parent

        try:
            usage = shutil.disk_usage(check_path)
            return usage.free
        except Exception:
            return 100 * 1024 * 1024 * 1024  # Fallback to generous 100 GB

    @classmethod
    def are_same_filesystem(cls, path1: str, path2: str) -> bool:
        """Determines if two directory paths share the same storage drive/volume."""
        try:
            p1 = os.path.abspath(path1)
            p2 = os.path.abspath(path2)
            if os.name == "nt":
                d1 = os.path.splitdrive(p1)[0].upper()
                d2 = os.path.splitdrive(p2)[0].upper()
                if d1 and d2:
                    return d1 == d2
            return os.stat(p1).st_dev == os.stat(p2).st_dev
        except Exception:
            return False

    @classmethod
    def check_disk_space(
        cls,
        target_dir: str,
        required_bytes: int,
        min_headroom_bytes: int = 500 * 1024 * 1024  # 500 MB minimum buffer
    ) -> DiskSpaceCheckResult:
        """
        Verifies whether target disk has sufficient storage for required_bytes.
        """
        available = cls._get_available_bytes(target_dir)

        needed_total = required_bytes + min_headroom_bytes
        is_sufficient = (available >= needed_total)
        free_after = max(0, available - required_bytes)

        if is_sufficient:
            msg = (
                f"Đủ dung lượng ổ đĩa: Cần ~{format_bytes(required_bytes)}, "
                f"khả dụng {format_bytes(available)} (còn lại {format_bytes(free_after)} sau khi xuất)."
            )
        else:
            msg = (
                f"Không đủ dung lượng ổ đĩa! Cần tối thiểu {format_bytes(needed_total)} "
                f"(gồm {format_bytes(required_bytes)} xuất + {format_bytes(min_headroom_bytes)} an toàn), "
                f"nhưng ổ đĩa chỉ còn {format_bytes(available)}."
            )

        return DiskSpaceCheckResult(
            is_sufficient=is_sufficient,
            required_bytes=required_bytes,
            available_bytes=available,
            free_after_export_bytes=free_after,
            message=msg
        )

    @classmethod
    def check_dual_storage(
        cls,
        output_dir: str,
        scratch_dir: str,
        single_video_duration_sec: float,
        batch_count: int,
        width: int = 1920,
        height: int = 1080,
        is_audio_only: bool = False,
        min_headroom_bytes: int = 500 * 1024 * 1024
    ) -> DiskSpaceCheckResult:
        """
        Pre-flight check accounting for split storage:
        - Output drive needs space for all finished batch files (batch_count * single_video_duration).
        - Scratch drive only needs space for the intermediate segments and audio of 1 active video batch,
          as temp files are discarded after each video completes.
        """
        if single_video_duration_sec <= 0:
            return DiskSpaceCheckResult(True, 0, 100 * 1024**3, 100 * 1024**3, "Thời lượng bằng 0.")

        if is_audio_only:
            audio_bitrate = cls.BITRATE_TABLE["audio_only"]
            single_raw = int((audio_bitrate * single_video_duration_sec) / 8)
        else:
            v_bps = cls.determine_video_bitrate(width, height)
            a_bps = 192_000
            single_raw = int(((v_bps + a_bps) * single_video_duration_sec) / 8)

        # 1. Scratch workspace requirement (for 1 video at a time)
        # Safety factor 1.5x accounts for intermediate sliced segment clips + audio track
        scratch_req = int(single_raw * 1.5)

        # 2. Final output requirement (for all batches)
        # Safety factor 1.1x accounts for mux overhead and final staging file
        total_batches = max(1, batch_count)
        output_req = int(single_raw * total_batches * 1.1)

        same_fs = cls.are_same_filesystem(output_dir, scratch_dir)

        out_avail = cls._get_available_bytes(output_dir)
        scratch_avail = cls._get_available_bytes(scratch_dir)

        if same_fs:
            total_req = output_req + scratch_req
            needed_total = total_req + min_headroom_bytes
            is_sufficient = (out_avail >= needed_total)
            free_after = max(0, out_avail - total_req)

            if is_sufficient:
                msg = (
                    f"Đủ dung lượng ổ đĩa: Cần ~{format_bytes(total_req)} "
                    f"(Xuất: ~{format_bytes(output_req)}, Xử lý tạm: ~{format_bytes(scratch_req)}), "
                    f"khả dụng {format_bytes(out_avail)} (còn lại {format_bytes(free_after)})."
                )
            else:
                msg = (
                    f"Không đủ dung lượng ổ đĩa! Cần tối thiểu {format_bytes(needed_total)} "
                    f"(gồm ~{format_bytes(output_req)} kết quả + ~{format_bytes(scratch_req)} tạm + "
                    f"{format_bytes(min_headroom_bytes)} an toàn), nhưng ổ đĩa chỉ còn {format_bytes(out_avail)}."
                )

            return DiskSpaceCheckResult(
                is_sufficient=is_sufficient,
                required_bytes=total_req,
                available_bytes=out_avail,
                free_after_export_bytes=free_after,
                message=msg
            )
        else:
            scratch_sufficient = (scratch_avail >= scratch_req + min_headroom_bytes)
            output_sufficient = (out_avail >= output_req + min_headroom_bytes)
            is_sufficient = scratch_sufficient and output_sufficient

            free_after_out = max(0, out_avail - output_req)

            out_drive = os.path.splitdrive(os.path.abspath(output_dir))[0] or output_dir
            scratch_drive = os.path.splitdrive(os.path.abspath(scratch_dir))[0] or scratch_dir

            if is_sufficient:
                msg = (
                    f"Đủ dung lượng: Ổ lưu kết quả ({out_drive}) cần ~{format_bytes(output_req)} "
                    f"(khả dụng {format_bytes(out_avail)}), Ổ xử lý tạm ({scratch_drive}) cần ~{format_bytes(scratch_req)} "
                    f"(khả dụng {format_bytes(scratch_avail)})."
                )
            else:
                failures = []
                if not output_sufficient:
                    failures.append(
                        f"Ổ lưu kết quả ({out_drive}) cần ~{format_bytes(output_req + min_headroom_bytes)} "
                        f"nhưng chỉ còn {format_bytes(out_avail)}"
                    )
                if not scratch_sufficient:
                    failures.append(
                        f"Ổ xử lý tạm ({scratch_drive}) cần ~{format_bytes(scratch_req + min_headroom_bytes)} "
                        f"nhưng chỉ còn {format_bytes(scratch_avail)}"
                    )
                msg = "Không đủ dung lượng ổ đĩa! " + "; ".join(failures) + "."

            return DiskSpaceCheckResult(
                is_sufficient=is_sufficient,
                required_bytes=output_req + scratch_req,
                available_bytes=min(out_avail, scratch_avail),
                free_after_export_bytes=free_after_out,
                message=msg
            )

    @classmethod
    def verify_disk_space(
        cls,
        target_dir: str,
        required_bytes: int,
        min_headroom_bytes: int = 500 * 1024 * 1024
    ) -> DiskSpaceCheckResult:
        """
        Verifies disk space and raises InsufficientDiskSpaceError if insufficient.
        """
        res = cls.check_disk_space(target_dir, required_bytes, min_headroom_bytes)
        if not res.is_sufficient:
            raise InsufficientDiskSpaceError(res.message)
        return res
