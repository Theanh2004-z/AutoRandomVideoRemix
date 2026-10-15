"""
Domain Policies & Execution Modes (V3).
Unifies legacy ambiguous boolean combinations into clear, explicit policy enums.
"""
from enum import Enum


class UniquePolicy(str, Enum):
    """
    Segment uniqueness policy across batches and outputs:
    - UNIQUE_BATCH: Each segment appears at most once across all batch outputs (strict no-repeat).
    - UNIQUE_OUTPUT: Each segment appears at most once within a single video, but may be drawn in subsequent videos.
    - CYCLE_OUTPUT: Segments may be reused within the same video (draw with replacement) once pool is exhausted.
    """
    UNIQUE_BATCH = "unique_batch"
    UNIQUE_OUTPUT = "unique_output"
    CYCLE_OUTPUT = "cycle_output"


class AntiConsecutivePolicy(str, Enum):
    """
    Policy for preventing adjacent segments from originating from the same source video:
    - OFF: No restriction on consecutive sources.
    - SOFT: Avoid adjacent same source when alternative sources exist; fallback if only same source remains.
    - STRICT: Raise InsufficientSegmentsError rather than permit adjacent same source.
    """
    OFF = "off"
    SOFT = "soft"
    STRICT = "strict"


class AudioMatchMode(str, Enum):
    """
    Audio playlist matching mode:
    - FIT_TARGET: Video duration determines timeline; last audio track is trimmed to match exactly.
    - KEEP_FULL_TRACKS: Audio duration determines timeline; full songs are kept, video matches audio.
    """
    FIT_TARGET = "fit_target"
    KEEP_FULL_TRACKS = "keep_full_tracks"
