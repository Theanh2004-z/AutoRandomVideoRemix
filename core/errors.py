"""
Application Error Types for AutoRandomVideoRemix (V3).
"""


class RemixError(Exception):
    """Base exception for all remix operations."""
    pass


class ConfigValidationError(RemixError):
    """Raised when configuration parameters are invalid or contradictory."""
    pass


class InsufficientSegmentsError(RemixError):
    """Raised when the segment pool cannot supply enough footage for target duration."""
    pass


class InsufficientAudioError(RemixError):
    """Raised when audio pool cannot supply enough audio for target duration."""
    pass


class SourceCorruptError(RemixError):
    """Raised when a media source cannot be read or probed."""
    pass


class RenderError(RemixError):
    """Raised when FFmpeg rendering fails or output file is corrupt."""
    pass


class HistoryConflictError(RemixError):
    """Raised when attempting to export segments that are excluded by export history."""
    pass
