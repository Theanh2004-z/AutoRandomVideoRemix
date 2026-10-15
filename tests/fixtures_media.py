"""
Media Fixtures Helper — generates small, deterministic synthetic video and audio files
using FFmpeg lavfi filters for fully self-contained, reproducible unit and integration tests.
No external media folders (e.g. E:/Quy Coc Tu) required.
"""
import os
import subprocess
import tempfile
from typing import Optional


def get_ffmpeg_cmd() -> str:
    """Returns path or command name for ffmpeg."""
    # Ensure local bin is checked
    local_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "ffmpeg.exe")
    if os.path.isfile(local_bin):
        return local_bin
    return "ffmpeg"


def create_synthetic_video(
    output_path: str,
    duration: float = 2.0,
    width: int = 640,
    height: int = 360,
    fps: int = 30,
    color: str = "blue",
    with_audio: bool = False,
) -> str:
    """
    Creates a tiny synthetic MP4 video in milliseconds using lavfi testsrc or color.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()

    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
    ]
    if with_audio:
        cmd.extend([
            "-f", "lavfi",
            "-i", f"sine=frequency=440:duration={duration}:sample_rate=48000",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2",
        ])
    cmd.extend([
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        output_path
    ])

    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        timeout=15
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to generate synthetic video: {output_path}")
    return output_path


def create_synthetic_audio(
    output_path: str,
    duration: float = 3.0,
    freq: int = 440,
    sample_rate: int = 44100,
    codec: str = "libmp3lame",
) -> str:
    """
    Creates a tiny synthetic audio file (MP3, WAV, etc.) in milliseconds using lavfi sine.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()

    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={duration}:sample_rate={sample_rate}",
    ]
    if output_path.lower().endswith(".mp3"):
        cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k"])
    elif output_path.lower().endswith(".wav"):
        cmd.extend(["-c:a", "pcm_s16le"])
    else:
        cmd.extend(["-c:a", "aac"])

    cmd.extend([
        "-ar", str(sample_rate),
        "-ac", "2",
        "-t", str(duration),
        output_path
    ])

    proc = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        timeout=15
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to generate synthetic audio: {output_path}")
    return output_path
