"""
Audio Scanner — scans and extracts metadata from audio files using ffprobe.
Supports MP3, WAV, M4A, AAC, FLAC, OGG, WMA.
"""
import os
import json
import subprocess
from typing import List, Tuple, Callable, Optional
from core.config import AudioInfo

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma"}


def probe_audio_file(file_path: str) -> AudioInfo:
    """
    Extracts duration, sample rate, channels, codec using ffprobe.
    """
    filename = os.path.basename(file_path)
    try:
        size_bytes = os.path.getsize(file_path)
    except Exception:
        size_bytes = 0

    if not os.path.isfile(file_path):
        return AudioInfo(
            path=file_path, filename=filename, duration=0.0,
            sample_rate=0, channels=0, codec="", size_bytes=0,
            is_valid=False, error_msg="File không tồn tại"
        )

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        file_path
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            timeout=10
        )
        if proc.returncode != 0:
            return AudioInfo(
                path=file_path, filename=filename, duration=0.0,
                sample_rate=0, channels=0, codec="", size_bytes=size_bytes,
                is_valid=False, error_msg="Lỗi đọc ffprobe"
            )

        data = json.loads(proc.stdout.decode('utf-8', errors='ignore'))
        streams = data.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not audio_stream:
            return AudioInfo(
                path=file_path, filename=filename, duration=0.0,
                sample_rate=0, channels=0, codec="", size_bytes=size_bytes,
                is_valid=False, error_msg="Không tìm thấy audio stream"
            )

        # Duration resolution
        format_info = data.get("format", {})
        duration = float(format_info.get("duration", 0.0) or audio_stream.get("duration", 0.0) or 0.0)

        sample_rate = int(audio_stream.get("sample_rate", 44100) or 44100)
        channels = int(audio_stream.get("channels", 2) or 2)
        codec = str(audio_stream.get("codec_name", "unknown"))

        if duration <= 0.1:
            return AudioInfo(
                path=file_path, filename=filename, duration=duration,
                sample_rate=sample_rate, channels=channels, codec=codec, size_bytes=size_bytes,
                is_valid=False, error_msg="Thời lượng âm thanh quá ngắn hoặc không xác định"
            )

        return AudioInfo(
            path=file_path,
            filename=filename,
            duration=round(duration, 3),
            sample_rate=sample_rate,
            channels=channels,
            codec=codec,
            size_bytes=size_bytes,
            is_valid=True
        )

    except Exception as e:
        return AudioInfo(
            path=file_path, filename=filename, duration=0.0,
            sample_rate=0, channels=0, codec="", size_bytes=size_bytes,
            is_valid=False, error_msg=str(e)
        )


def scan_audio_directory(
    directory: str,
    recursive: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None
) -> Tuple[List[AudioInfo], List[AudioInfo]]:
    """
    Scans a directory for audio files and extracts metadata.
    Returns: (valid_audios, invalid_audios)
    """
    if not os.path.isdir(directory):
        return [], []

    matched_files: List[str] = []
    if recursive:
        for root, _, files in os.walk(directory):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    matched_files.append(os.path.join(root, f))
    else:
        for f in os.listdir(directory):
            full_path = os.path.join(directory, f)
            if os.path.isfile(full_path):
                ext = os.path.splitext(f)[1].lower()
                if ext in AUDIO_EXTENSIONS:
                    matched_files.append(full_path)

    total_files = len(matched_files)
    valid_audios: List[AudioInfo] = []
    invalid_audios: List[AudioInfo] = []

    for idx, path in enumerate(matched_files):
        if cancel_check and cancel_check():
            break

        if progress_callback:
            progress_callback(idx + 1, total_files, os.path.basename(path))

        info = probe_audio_file(path)
        if info.is_valid:
            valid_audios.append(info)
        else:
            invalid_audios.append(info)

    return valid_audios, invalid_audios
