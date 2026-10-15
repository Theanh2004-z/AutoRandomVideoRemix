"""
Output Validator Module (V3).
Performs post-render verification of generated video and audio files:
- Container integrity and stream detection
- Resolution, aspect ratio, and frame rate verification
- Duration match within acceptable tolerance
- Decode smoke-test to catch corrupted headers or stream stalls
"""
import os
import json
import subprocess
from dataclasses import dataclass
from typing import Optional, Tuple

from core.process_runner import ProcessRunner


@dataclass
class ValidationResult:
    """Result of media validation."""
    is_valid: bool
    error_msg: str = ""
    actual_duration: float = 0.0
    actual_width: int = 0
    actual_height: int = 0
    actual_fps: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    file_size_bytes: int = 0


class OutputValidator:
    """Validates rendered output files using ffprobe and decode smoke-tests."""

    @classmethod
    def validate_video(
        cls,
        file_path: str,
        expected_duration: float,
        expected_width: int,
        expected_height: int,
        expected_fps: Optional[float] = None,
        duration_tolerance_sec: float = 1.0,
        require_audio: bool = False,
        deep_validate: bool = False
    ) -> ValidationResult:
        """
        Validates an exported video file against target technical specifications.
        """
        if not os.path.isfile(file_path):
            return ValidationResult(is_valid=False, error_msg=f"File không tồn tại: {file_path}")

        size = os.path.getsize(file_path)
        if size < 1000:
            return ValidationResult(is_valid=False, error_msg=f"File quá nhỏ hoặc rỗng ({size} bytes).")

        # 1. Probe streams and format
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,width,height,r_frame_rate,duration:format=duration",
            "-print_format", "json",
            file_path
        ]
        res = ProcessRunner.run(cmd, timeout_sec=10.0)
        if not res.is_success or not res.stderr and not res.stdout:
            # Note: ffprobe output goes to stdout or stderr depending on build
            pass

        # Try to parse json output
        probe_json = None
        for txt in (res.stdout, res.stderr):
            try:
                if txt and "{" in txt:
                    s_idx = txt.find("{")
                    e_idx = txt.rfind("}")
                    if s_idx != -1 and e_idx != -1:
                        probe_json = json.loads(txt[s_idx:e_idx+1])
                        break
            except Exception:
                pass

        if not probe_json:
            # Direct subprocess fallback for stdout
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                probe_json = json.loads(proc.stdout)
            except Exception as e:
                return ValidationResult(is_valid=False, error_msg=f"Không thể đọc metadata bằng ffprobe: {e}")

        streams = probe_json.get("streams", [])
        fmt = probe_json.get("format", {})

        has_v = False
        has_a = False
        w, h = 0, 0
        fps = 0.0

        for st in streams:
            ctype = st.get("codec_type")
            if ctype == "video":
                has_v = True
                w = int(st.get("width", 0))
                h = int(st.get("height", 0))
                fps_str = st.get("r_frame_rate", "30/1")
                try:
                    num, den = fps_str.split("/")
                    fps = round(float(num) / float(den), 2)
                except Exception:
                    fps = 30.0
            elif ctype == "audio":
                has_a = True

        if not has_v:
            return ValidationResult(is_valid=False, error_msg="File không chứa luồng hình ảnh (video stream).")

        if require_audio and not has_a:
            return ValidationResult(is_valid=False, error_msg="File video thiếu luồng âm thanh (audio stream).")

        actual_dur = float(fmt.get("duration", 0.0))
        if actual_dur <= 0.0:
            return ValidationResult(is_valid=False, error_msg="Thời lượng file không hợp lệ (<= 0s).")

        # 2. Check resolution
        if w != expected_width or h != expected_height:
            return ValidationResult(
                is_valid=False,
                error_msg=f"Sai độ phân giải: thực tế {w}x{h}, yêu cầu {expected_width}x{expected_height}."
            )

        # Check FPS if expected_fps specified (R13)
        if expected_fps is not None and abs(fps - expected_fps) > 0.1:
            return ValidationResult(
                is_valid=False,
                error_msg=f"Sai tốc độ khung hình (FPS): thực tế {fps:.2f}fps, yêu cầu {expected_fps:.2f}fps."
            )

        # 3. Check duration tolerance
        diff = abs(actual_dur - expected_duration)
        if diff > duration_tolerance_sec:
            return ValidationResult(
                is_valid=False,
                error_msg=f"Thời lượng sai lệch quá mức: thực tế {actual_dur:.2f}s, yêu cầu {expected_duration:.2f}s (lệch {diff:.2f}s)."
            )

        # 4. Decode validation (smoke-test + multi-point if deep_validate enabled) (R13)
        check_points = [("0.0", "1.0")]
        if deep_validate and actual_dur > 3.0:
            mid_start = str(round(actual_dur / 2.0, 2))
            tail_start = str(round(max(0.0, actual_dur - 1.5), 2))
            check_points.append((mid_start, "1.0"))
            check_points.append((tail_start, "1.5"))

        for ss_point, t_dur in check_points:
            decode_cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", ss_point,
                "-i", file_path,
                "-t", t_dur,
                "-f", "null", "-"
            ]
            d_res = ProcessRunner.run(decode_cmd, timeout_sec=5.0)
            if d_res.returncode != 0:
                return ValidationResult(
                    is_valid=False,
                    error_msg=f"Lỗi giải mã file tại {ss_point}s (corrupt stream): {d_res.stderr[:200]}"
                )

        return ValidationResult(
            is_valid=True,
            actual_duration=actual_dur,
            actual_width=w,
            actual_height=h,
            actual_fps=fps,
            has_video=has_v,
            has_audio=has_a,
            file_size_bytes=size
        )

    @classmethod
    def validate_audio(
        cls,
        file_path: str,
        expected_duration: float,
        duration_tolerance_sec: float = 1.0
    ) -> ValidationResult:
        """
        Validates an exported audio file against expected duration.
        """
        if not os.path.isfile(file_path):
            return ValidationResult(is_valid=False, error_msg=f"File không tồn tại: {file_path}")

        size = os.path.getsize(file_path)
        if size < 500:
            return ValidationResult(is_valid=False, error_msg=f"File âm thanh quá nhỏ ({size} bytes).")

        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,duration:format=duration",
            "-print_format", "json",
            file_path
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            probe_json = json.loads(proc.stdout)
        except Exception as e:
            return ValidationResult(is_valid=False, error_msg=f"Không thể đọc metadata âm thanh: {e}")

        streams = probe_json.get("streams", [])
        fmt = probe_json.get("format", {})
        has_a = any(st.get("codec_type") == "audio" for st in streams)

        if not has_a:
            return ValidationResult(is_valid=False, error_msg="File không chứa luồng âm thanh hợp lệ.")

        actual_dur = float(fmt.get("duration", 0.0))
        diff = abs(actual_dur - expected_duration)
        if diff > duration_tolerance_sec:
            return ValidationResult(
                is_valid=False,
                error_msg=f"Thời lượng âm thanh sai lệch: thực tế {actual_dur:.2f}s, yêu cầu {expected_duration:.2f}s (lệch {diff:.2f}s)."
            )

        return ValidationResult(
            is_valid=True,
            actual_duration=actual_dur,
            has_video=False,
            has_audio=True,
            file_size_bytes=size
        )
