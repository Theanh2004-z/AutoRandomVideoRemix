"""
random_cutter.py — DEPRECATED.

This module has been superseded by the new pipeline:
  core/segment_slicer.py   — sequential segmentation + remainder merging
  core/pool_manager.py     — pool management, shuffle, sequential pick

The symbols below are re-exported for backward compatibility only.
Do NOT use this module in new code.
"""
from core.pool_manager import InsufficientSegmentsError  # noqa: F401 re-export

# The old InsufficientSourceMaterialError is now InsufficientSegmentsError.
InsufficientSourceMaterialError = InsufficientSegmentsError
