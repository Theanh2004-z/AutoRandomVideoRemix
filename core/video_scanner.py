"""
Video Scanner module to discover and inspect source video files.
"""
import os
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Callable, Optional, Tuple
from core.config import VideoInfo

SUPPORTED_EXTENSIONS = {
    ".mp4", ".mkv", ".mov", ".avi", ".webm",
    ".flv", ".ts", ".m4v", ".wmv", ".mpeg", ".mpg"
}

# Optimal number of parallel ffprobe workers (I/O-bound, safe to overprovision)
_PROBE_WORKERS = min(8, (os.cpu_count() or 2) * 2)


def parse_fps(fps_str: str) -> float:
    """Safely converts fractional fps like '30000/1001' or '30/1' to float."""
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/", 1)
            den_val = float(den)
            if den_val == 0:
                return 30.0
            return float(num) / den_val
        return float(fps_str)
    except Exception:
        return 30.0


def probe_video_file(file_path: str) -> VideoInfo:
    """
    Uses ffprobe to inspect a single video file.
    Returns a VideoInfo object with metadata and validity status.
    """
    filename = os.path.basename(file_path)
    try:
        size_bytes = os.path.getsize(file_path)
    except Exception as e:
        return VideoInfo(
            path=file_path,
            filename=filename,
            duration=0.0,
            width=0,
            height=0,
            fps=0.0,
            codec="unknown",
            has_audio=False,
            size_bytes=0,
            is_valid=False,
            error_msg=f"Không đọc được kích thước file: {e}"
        )

    # Single ffprobe call: fetch both video stream (v:0) and audio stream (a:0)
    # plus format-level metadata — eliminates the second audio-only subprocess call.
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,codec_name,duration,codec_type"
        ":format=duration",
        "-of", "json",
        file_path
    ]

    try:
        # Single subprocess call covering video, audio, and format metadata
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        )

        if proc.returncode != 0:
            return VideoInfo(
                path=file_path,
                filename=filename,
                duration=0.0,
                width=0,
                height=0,
                fps=0.0,
                codec="unknown",
                has_audio=False,
                size_bytes=size_bytes,
                is_valid=False,
                error_msg=f"Lỗi ffprobe: {proc.stderr.strip()[:200]}"
            )

        data = json.loads(proc.stdout)
        all_streams = data.get("streams", [])
        fmt = data.get("format", {})

        # Separate streams by codec_type
        video_streams = [s for s in all_streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in all_streams if s.get("codec_type") == "audio"]

        if not video_streams:
            return VideoInfo(
                path=file_path,
                filename=filename,
                duration=0.0,
                width=0,
                height=0,
                fps=0.0,
                codec="unknown",
                has_audio=False,
                size_bytes=size_bytes,
                is_valid=False,
                error_msg="Không tìm thấy luồng video (stream v:0)"
            )

        v_stream = video_streams[0]
        width = int(v_stream.get("width", 0))
        height = int(v_stream.get("height", 0))
        codec = v_stream.get("codec_name", "unknown")

        # Duration resolution: prefer format duration, fallback to stream duration
        duration_str = fmt.get("duration") or v_stream.get("duration")
        if duration_str is None or duration_str == "N/A":
            duration = 0.0
        else:
            try:
                duration = float(duration_str)
            except ValueError:
                duration = 0.0

        # FPS calculation
        fps_str = v_stream.get("avg_frame_rate", "") or v_stream.get("r_frame_rate", "30/1")
        fps = parse_fps(fps_str)
        if fps <= 0 or fps > 240:
            fps = 30.0

        if duration <= 0.1 or width <= 0 or height <= 0:
            return VideoInfo(
                path=file_path,
                filename=filename,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                codec=codec,
                has_audio=False,
                size_bytes=size_bytes,
                is_valid=False,
                error_msg="Thời lượng hoặc kích thước không hợp lệ (<= 0)"
            )

        # Audio presence derived from the combined probe result — no second call needed
        has_audio = len(audio_streams) > 0

        return VideoInfo(
            path=file_path,
            filename=filename,
            duration=duration,
            width=width,
            height=height,
            fps=round(fps, 2),
            codec=codec,
            has_audio=has_audio,
            size_bytes=size_bytes,
            is_valid=True
        )

    except subprocess.TimeoutExpired:
        return VideoInfo(
            path=file_path,
            filename=filename,
            duration=0.0,
            width=0,
            height=0,
            fps=0.0,
            codec="unknown",
            has_audio=False,
            size_bytes=size_bytes,
            is_valid=False,
            error_msg="Hết thời gian quét (ffprobe timeout)"
        )
    except Exception as e:
        return VideoInfo(
            path=file_path,
            filename=filename,
            duration=0.0,
            width=0,
            height=0,
            fps=0.0,
            codec="unknown",
            has_audio=False,
            size_bytes=size_bytes,
            is_valid=False,
            error_msg=f"Lỗi ngoại lệ: {str(e)}"
        )


def scan_directory(
    directory: str,
    recursive: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> Tuple[List[VideoInfo], List[VideoInfo]]:
    """
    Scans a folder for all valid and invalid video files.
    Returns:
        (valid_videos, invalid_videos)
    """
    if not os.path.isdir(directory):
        return [], []

    candidate_files: List[str] = []
    if recursive:
        for root, _, files in os.walk(directory):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    candidate_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(directory):
            full_path = os.path.join(directory, f)
            if os.path.isfile(full_path):
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    candidate_files.append(full_path)

    total_files = len(candidate_files)
    if total_files == 0:
        return [], []

    valid_list: List[VideoInfo] = []
    invalid_list: List[VideoInfo] = []

    # Thread-safe counter for progress reporting
    completed_lock = threading.Lock()
    completed_count = [0]  # mutable int via list

    def _probe_with_progress(fpath: str) -> VideoInfo:
        """Wraps probe_video_file and fires the progress callback thread-safely."""
        result = probe_video_file(fpath)
        with completed_lock:
            completed_count[0] += 1
            if progress_callback:
                progress_callback(completed_count[0], total_files, os.path.basename(fpath))
        return result

    # Run ffprobe calls in parallel using a bounded thread pool.
    # ffprobe is I/O-bound (disk + subprocess), so threading gives a major speedup.
    with ThreadPoolExecutor(max_workers=_PROBE_WORKERS) as executor:
        # Submit all jobs; keep a dict future -> path for ordered cancellation check
        future_map = {
            executor.submit(_probe_with_progress, fpath): fpath
            for fpath in candidate_files
        }

        for future in as_completed(future_map):
            # Honour cancel request: stop collecting results early.
            # Already-running ffprobe subprocesses will finish naturally (they are
            # short-lived), but no new results will be added after the flag is set.
            if cancel_check and cancel_check():
                # Cancel pending futures that have not started yet
                for f in future_map:
                    f.cancel()
                break

            try:
                info = future.result()
                if info.is_valid:
                    valid_list.append(info)
                else:
                    invalid_list.append(info)
            except Exception:
                # Should not normally happen; probe_video_file handles its own exceptions
                pass

    return valid_list, invalid_list
