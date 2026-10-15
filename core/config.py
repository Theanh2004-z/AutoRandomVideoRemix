"""
Configuration and Data Models for AutoRandomVideoRemix.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Optional


# ------------------------------------------------------------------
# Helper
# ------------------------------------------------------------------

def _fmt_time(sec: float) -> str:
    """Format float seconds as MM:SS.f or HH:MM:SS.f."""
    m, s_int = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    frac = int((sec - int(sec)) * 10)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s_int:02d}.{frac}"
    return f"{m:02d}:{s_int:02d}.{frac}"


# ------------------------------------------------------------------
# VideoInfo — source video metadata
# ------------------------------------------------------------------

@dataclass
class VideoInfo:
    """Stores metadata for a source video file."""
    path: str
    filename: str
    duration: float   # seconds
    width: int
    height: int
    fps: float
    codec: str
    has_audio: bool
    size_bytes: int
    is_valid: bool = True
    error_msg: str = ""
    pixel_format: str = ""
    color_transfer: str = ""
    sample_aspect_ratio: str = "1:1"
    time_base: str = ""

    @property
    def formatted_duration(self) -> str:
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


# ------------------------------------------------------------------
# SegmentInfo — one segment in the pool / output plan
# ------------------------------------------------------------------

@dataclass
class SegmentInfo:
    """
    Represents a single segment produced by sequential slicing of a source
    video.  Used in both the segment pool and the output video plan.
    """
    segment_id: str            # Unique identifier (UUID hex)
    source_video_path: str     # Absolute path to the source file
    source_video_name: str     # Filename of the source video (display)
    start_sec: float           # Start time within the source video (s)
    end_sec: float             # End time within the source video (s)
    duration: float            # Duration of this segment (s)
    segment_index: int         # 1-based index within its source video

    # Pool state
    is_used: bool = False      # Has been drawn into an output plan
    status: str = "Chờ xử lý"  # Chờ xử lý | Đang cắt | Hoàn tất | Lỗi

    # Plan state (set by RandomPicker when building an output plan)
    plan_position: int = 0          # 1-based position in the output video
    target_video_index: int = 1     # Which output video (batch)
    trim_end_sec: Optional[float] = None  # If set, rendering ends here

    # ---------------------------------------------------------------- properties

    @property
    def effective_duration(self) -> float:
        """Actual render duration, respecting trim_end_sec."""
        if self.trim_end_sec is not None:
            return round(self.trim_end_sec - self.start_sec, 6)
        return self.duration

    @property
    def formatted_start(self) -> str:
        return _fmt_time(self.start_sec)

    @property
    def formatted_end(self) -> str:
        end = self.trim_end_sec if self.trim_end_sec is not None else self.end_sec
        return _fmt_time(end)

    # ---------------------------------------------------------------- helpers

    def clone_for_plan(
        self,
        plan_position: int,
        target_video_index: int,
    ) -> "SegmentInfo":
        """Return a shallow copy configured for an output plan slot."""
        c = copy.copy(self)
        c.plan_position = plan_position
        c.target_video_index = target_video_index
        c.trim_end_sec = None
        c.status = "Chờ xử lý"
        return c


# Backward-compatibility alias — existing imports of ClipSegment still work.
ClipSegment = SegmentInfo


# ------------------------------------------------------------------
# CutConfig — segmentation and randomisation settings
# ------------------------------------------------------------------

@dataclass
class CutConfig:
    """Segmentation and randomisation configuration."""

    # Segmentation
    segment_duration: float = 10.0          # Length of each segment (s)
    target_total_duration: float = 1800.0   # Target output duration (s); default 30 min
    batch_count: int = 1                    # Number of output videos to produce

    # Segmentation option
    merge_remainder: bool = True            # Merge tail < segment_duration into prev segment

    # Randomisation options
    allow_reuse: bool = False               # Re-use segments when pool is exhausted
    no_repeat_across_batch: bool = True     # Each segment appears in ≤ 1 batch output
    no_consecutive_same_source: bool = True  # Avoid two consecutive segments from the same source
    trim_last_segment: bool = True          # Trim last segment to hit exact target duration
    use_export_history: bool = False        # Exclude segments previously saved in export_history.json
    unique_policy: Optional[str] = None     # unique_batch | unique_output | cycle_output
    anti_consecutive_policy: Optional[str] = None   # soft | strict | off


# ------------------------------------------------------------------
# AudioInfo — source audio metadata (Phần 2)
# ------------------------------------------------------------------

@dataclass
class AudioInfo:
    """Stores metadata for an intact source audio file."""
    path: str
    filename: str
    duration: float   # seconds
    sample_rate: int  # Hz (e.g. 44100, 48000)
    channels: int     # 1 = mono, 2 = stereo
    codec: str
    size_bytes: int
    is_valid: bool = True
    error_msg: str = ""

    @property
    def formatted_duration(self) -> str:
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


# ------------------------------------------------------------------
# AudioPlaylistItem — item in audio playlist (Phần 2)
# ------------------------------------------------------------------

@dataclass
class AudioPlaylistItem:
    """Represents one audio track in the randomized sequential playlist."""
    item_id: str
    source_audio_path: str
    source_audio_name: str
    duration: float                     # Full original duration (s)
    effective_duration: float           # Actual playtime (s), trimmed if last track in Mode A
    trim_end_sec: Optional[float] = None
    cycle_index: int = 1                # Which deck shuffle cycle this belongs to
    playlist_position: int = 1          # 1-based index in the final audio playlist
    status: str = "Chờ xử lý"           # Chờ xử lý | Đang ghép | Hoàn tất | Lỗi

    @property
    def formatted_original(self) -> str:
        return _fmt_time(self.duration)

    @property
    def formatted_effective(self) -> str:
        return _fmt_time(self.effective_duration)

    @property
    def is_trimmed(self) -> bool:
        return self.trim_end_sec is not None and self.trim_end_sec < self.duration


# ------------------------------------------------------------------
# AudioConfig — audio processing configuration (Phần 2)
# ------------------------------------------------------------------

@dataclass
class AudioConfig:
    """Configuration for independent random audio shuffle and concat."""
    folder_path: str = ""
    target_duration: float = 1800.0
    mode: str = "fit_target"               # 'fit_target' (Chế độ A: cắt bài cuối) | 'keep_all' (Chế độ B: giữ nguyên bài)
    no_consecutive_same_track: bool = True  # Avoid adjacent identical tracks across cycles
    auto_reshuffle_cycle: bool = True      # Start new cycle when deck is exhausted
    allow_single_track_loop: bool = True   # Allow repeating when only 1 track exists in folder
    audio_output_format: str = "mp3"       # 'mp3' | 'm4a' | 'wav'


# ------------------------------------------------------------------
# OutputConfig — encoding and export settings
# ------------------------------------------------------------------

@dataclass
class OutputConfig:
    """Video output and encoding settings."""
    export_target: str = "video_with_random_audio"  # 'video_with_random_audio' | 'video_only' | 'audio_only'
    resolution: str = "1920x1080"   # WxH string
    fps: int = 30
    aspect_ratio_mode: str = "fit"  # 'fit' | 'crop'
    hw_accel: str = "auto"          # 'auto' | 'nvenc' | 'amf' | 'qsv' | 'cpu'
    quality: str = "high"           # 'standard' | 'high' | 'very_high'
    audio_mode: str = "random_audio_pool"  # 'none' | 'keep' | 'bgm' | 'random_audio_pool'
    bgm_path: str = ""
    output_dir: str = ""
    output_prefix: str = ""  # Empty string defaults to daily date MMDD (e.g. 1014.mp4, 1014(0).mp4)
    audio_output_format: str = "mp3"  # 'mp3' | 'wav' | 'm4a'
    render_speed: str = "balanced"  # fast | balanced | quality
    parallel_segments: int = 0       # 0=auto, 1 or 2
    gpu_processing: bool = True      # hardware decode/scale when compatible
    decoder_mode: str = "auto"       # auto | gpu | cpu

    @property
    def width_height(self) -> tuple[int, int]:
        try:
            parts = (self.resolution or "1920x1080").split("x")
            return int(parts[0]), int(parts[1])
        except Exception:
            return 1920, 1080

