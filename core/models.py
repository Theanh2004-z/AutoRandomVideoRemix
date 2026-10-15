"""
Domain Models and Configuration Adapters (V3).
Defines normalized immutable configuration structures and provides
backward compatibility adapters for V2.5 configuration objects.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple
import hashlib
import os

from core.policies import UniquePolicy, AntiConsecutivePolicy, AudioMatchMode
from core.errors import ConfigValidationError


_CONTENT_HASH_CACHE: Dict[Tuple[str, float, int], str] = {}


def compute_media_content_hash(
    file_path: str,
    file_size: Optional[int] = None,
    allow_cache: bool = True
) -> str:
    """
    Computes an authoritative content-based SHA-256 fingerprint by streaming
    the file bytes (V04). Caches results by (abs_path, mtime, size) to avoid re-reading.
    Independent of file path or name.
    """
    if not os.path.isfile(file_path):
        return hashlib.sha256(f"missing::{file_path}".encode("utf-8")).hexdigest()[:16]

    abs_p = os.path.abspath(file_path)
    try:
        stat = os.stat(abs_p)
        size = stat.st_size if file_size is None else file_size
        mtime = stat.st_mtime
    except Exception:
        size = file_size or 0
        mtime = 0.0

    cache_key = (abs_p, mtime, size)
    if allow_cache and cache_key in _CONTENT_HASH_CACHE:
        return _CONTENT_HASH_CACHE[cache_key]

    h = hashlib.sha256()
    try:
        with open(abs_p, "rb") as f:
            while chunk := f.read(256 * 1024):
                h.update(chunk)
        digest = h.hexdigest()[:16]
    except Exception:
        digest = hashlib.sha256(f"{abs_p}::{size}::{mtime}".encode("utf-8")).hexdigest()[:16]

    if allow_cache:
        _CONTENT_HASH_CACHE[cache_key] = digest
    return digest


@dataclass(frozen=True)
class AssetFingerprint:
    """Immutable identity fingerprint of a media file."""
    asset_id: str
    file_path: str
    file_size: int
    duration_sec: float

    @classmethod
    def from_file(cls, path: str, duration: float = 0.0) -> "AssetFingerprint":
        """Generate fingerprint from content bytes and file size."""
        abs_path = os.path.abspath(path)
        size = os.path.getsize(abs_path) if os.path.isfile(abs_path) else 0
        h = compute_media_content_hash(abs_path, size)
        return cls(asset_id=h, file_path=abs_path, file_size=size, duration_sec=duration)


@dataclass(frozen=True)
class NormalizedCutConfig:
    """Normalized configuration for video segmentation and batch planning."""
    segment_duration: float = 10.0
    target_total_duration: float = 1800.0
    batch_count: int = 1
    merge_remainder: bool = True
    unique_policy: UniquePolicy = UniquePolicy.UNIQUE_BATCH
    anti_consecutive_policy: AntiConsecutivePolicy = AntiConsecutivePolicy.SOFT
    trim_last_segment: bool = True
    use_export_history: bool = False
    fps: float = 30.0

    def validate(self) -> None:
        """Validate config parameters and raise ConfigValidationError if contradictory."""
        if self.segment_duration <= 0:
            raise ConfigValidationError("segment_duration must be strictly positive (> 0s)")
        if self.target_total_duration <= 0:
            raise ConfigValidationError("target_total_duration must be strictly positive (> 0s)")
        if self.batch_count < 1:
            raise ConfigValidationError("batch_count must be at least 1")
        if self.fps <= 0:
            raise ConfigValidationError("fps must be strictly positive (> 0)")


class ConfigAdapterV2:
    """Translates between legacy V2.5 CutConfig and V3 NormalizedCutConfig."""

    @staticmethod
    def from_v2_cut_config(legacy_cfg: Any, fps: float = 30.0) -> NormalizedCutConfig:
        """
        Convert legacy CutConfig with ambiguous boolean flags into NormalizedCutConfig.
        Mapping logic:
          - allow_reuse == True -> CYCLE_OUTPUT
          - allow_reuse == False and no_repeat_across_batch == True -> UNIQUE_BATCH
          - allow_reuse == False and no_repeat_across_batch == False -> UNIQUE_OUTPUT
        """
        unique_policy_raw = getattr(legacy_cfg, "unique_policy", None)
        if unique_policy_raw:
            if isinstance(unique_policy_raw, UniquePolicy):
                unique_policy = unique_policy_raw
            else:
                u_str = str(unique_policy_raw).lower()
                if "cycle" in u_str:
                    unique_policy = UniquePolicy.CYCLE_OUTPUT
                elif "batch" in u_str:
                    unique_policy = UniquePolicy.UNIQUE_BATCH
                elif "output" in u_str:
                    unique_policy = UniquePolicy.UNIQUE_OUTPUT
                else:
                    unique_policy = UniquePolicy.UNIQUE_BATCH
        else:
            allow_reuse = getattr(legacy_cfg, "allow_reuse", False)
            no_repeat_batch = getattr(legacy_cfg, "no_repeat_across_batch", True)
            if allow_reuse:
                unique_policy = UniquePolicy.CYCLE_OUTPUT
            elif no_repeat_batch:
                unique_policy = UniquePolicy.UNIQUE_BATCH
            else:
                unique_policy = UniquePolicy.UNIQUE_OUTPUT

        anti_consec_raw = getattr(legacy_cfg, "anti_consecutive_policy", None)
        if anti_consec_raw:
            if isinstance(anti_consec_raw, AntiConsecutivePolicy):
                anti_consecutive = anti_consec_raw
            else:
                a_str = str(anti_consec_raw).lower()
                if "strict" in a_str:
                    anti_consecutive = AntiConsecutivePolicy.STRICT
                elif "off" in a_str:
                    anti_consecutive = AntiConsecutivePolicy.OFF
                else:
                    anti_consecutive = AntiConsecutivePolicy.SOFT
        else:
            no_consecutive = getattr(legacy_cfg, "no_consecutive_same_source", True)
            anti_consecutive = AntiConsecutivePolicy.SOFT if no_consecutive else AntiConsecutivePolicy.OFF

        cfg = NormalizedCutConfig(
            segment_duration=float(getattr(legacy_cfg, "segment_duration", 10.0)),
            target_total_duration=float(getattr(legacy_cfg, "target_total_duration", 1800.0)),
            batch_count=int(getattr(legacy_cfg, "batch_count", 1)),
            merge_remainder=bool(getattr(legacy_cfg, "merge_remainder", True)),
            unique_policy=unique_policy,
            anti_consecutive_policy=anti_consecutive,
            trim_last_segment=bool(getattr(legacy_cfg, "trim_last_segment", True)),
            use_export_history=bool(getattr(legacy_cfg, "use_export_history", False)),
            fps=fps
        )
        cfg.validate()
        return cfg
