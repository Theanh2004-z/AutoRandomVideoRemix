"""
Timebase Arithmetic & Quantization Module (V3).
Provides exact integer frame and audio sample conversions to prevent
floating-point drift across multi-segment video/audio sequences.
"""
from typing import Tuple


def sec_to_frames(seconds: float, fps: float) -> int:
    """Convert duration or timestamp in seconds to exact integer frame count."""
    if seconds <= 0 or fps <= 0:
        return 0
    return int(round(seconds * fps))


def frames_to_sec(frames: int, fps: float) -> float:
    """Convert integer frame count back to float seconds."""
    if frames <= 0 or fps <= 0:
        return 0.0
    return round(frames / fps, 6)


def sec_to_samples(seconds: float, sample_rate: int) -> int:
    """Convert audio duration in seconds to exact integer sample count."""
    if seconds <= 0 or sample_rate <= 0:
        return 0
    return int(round(seconds * sample_rate))


def samples_to_sec(samples: int, sample_rate: int) -> float:
    """Convert integer audio sample count to float seconds."""
    if samples <= 0 or sample_rate <= 0:
        return 0.0
    return round(samples / sample_rate, 6)


def quantize_duration_to_frames(seconds: float, fps: float) -> float:
    """
    Quantize floating seconds to the nearest discrete video frame duration.
    Guarantees that resulting frame count >= 1 if original seconds > 0.
    """
    if seconds <= 0:
        return 0.0
    frames = max(1, sec_to_frames(seconds, fps))
    return frames_to_sec(frames, fps)


def quantize_time_range_frames(start_sec: float, end_sec: float, fps: float) -> Tuple[float, float, int]:
    """
    Quantize start and end times to exact frame boundaries.
    Returns: (quantized_start, quantized_end, frame_count)
    """
    start_frame = sec_to_frames(start_sec, fps)
    end_frame = max(start_frame + 1, sec_to_frames(end_sec, fps))
    q_start = frames_to_sec(start_frame, fps)
    q_end = frames_to_sec(end_frame, fps)
    return q_start, q_end, (end_frame - start_frame)
