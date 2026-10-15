"""
Media Catalog Module (V3).
Provides deterministic asset identification, stable catalog indexing,
and immutable catalog snapshots independent of filesystem scan order.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import hashlib
import os

from core.timebase import sec_to_frames, frames_to_sec, quantize_time_range_frames
from core.models import AssetFingerprint
from core.config import VideoInfo, AudioInfo


@dataclass(frozen=True)
class CatalogSegment:
    """An immutable segment defined on frame and second boundaries."""
    segment_id: str
    asset_id: str
    source_path: str
    source_filename: str
    start_sec: float
    end_sec: float
    duration_sec: float
    start_frame: int
    end_frame: int
    frame_count: int

    @property
    def effective_duration(self) -> float:
        return self.duration_sec

    @property
    def source_video_name(self) -> str:
        return self.source_filename

    @property
    def source_video_path(self) -> str:
        return self.source_path


@dataclass(frozen=True)
class MediaAsset:
    """An immutable catalogued media asset."""
    asset_id: str
    file_path: str
    filename: str
    media_type: str  # "video" or "audio"
    duration_sec: float
    size_bytes: int
    fps: float = 30.0
    width: int = 1920
    height: int = 1080
    sample_rate: int = 48000
    channels: int = 2
    is_valid: bool = True

    @classmethod
    def from_video_info(cls, v: VideoInfo) -> "MediaAsset":
        abs_p = os.path.abspath(v.path)
        # Deterministic asset id based on path, size, and duration
        aid = hashlib.sha256(f"{abs_p}::{v.size_bytes}::{v.duration:.3f}".encode("utf-8")).hexdigest()[:16]
        return cls(
            asset_id=aid,
            file_path=abs_p,
            filename=os.path.basename(abs_p),
            media_type="video",
            duration_sec=v.duration,
            size_bytes=v.size_bytes,
            fps=v.fps if v.fps > 0 else 30.0,
            width=v.width,
            height=v.height,
            is_valid=v.is_valid
        )

    @classmethod
    def from_audio_info(cls, a: AudioInfo) -> "MediaAsset":
        abs_p = os.path.abspath(a.path)
        aid = hashlib.sha256(f"{abs_p}::{a.size_bytes}::{a.duration:.3f}".encode("utf-8")).hexdigest()[:16]
        return cls(
            asset_id=aid,
            file_path=abs_p,
            filename=os.path.basename(abs_p),
            media_type="audio",
            duration_sec=a.duration,
            size_bytes=a.size_bytes,
            sample_rate=a.sample_rate if a.sample_rate > 0 else 44100,
            channels=a.channels if a.channels > 0 else 2,
            is_valid=a.is_valid
        )


@dataclass(frozen=True)
class CatalogSnapshot:
    """An immutable, deterministically sorted snapshot of media assets."""
    snapshot_hash: str
    assets: Tuple[MediaAsset, ...]

    def get_by_id(self, asset_id: str) -> Optional[MediaAsset]:
        for a in self.assets:
            if a.asset_id == asset_id:
                return a
        return None


class MediaCatalog:
    """Catalog manager maintaining registered assets and generating stable segments."""

    def __init__(self):
        self._assets: Dict[str, MediaAsset] = {}

    def add_video(self, vinfo: VideoInfo) -> MediaAsset:
        asset = MediaAsset.from_video_info(vinfo)
        self._assets[asset.asset_id] = asset
        return asset

    def add_audio(self, ainfo: AudioInfo) -> MediaAsset:
        asset = MediaAsset.from_audio_info(ainfo)
        self._assets[asset.asset_id] = asset
        return asset

    def clear(self):
        self._assets.clear()

    @property
    def total_assets(self) -> int:
        return len(self._assets)

    def create_snapshot(self) -> CatalogSnapshot:
        """Create a deterministically sorted snapshot independent of scan order."""
        sorted_assets = tuple(sorted(self._assets.values(), key=lambda a: (a.media_type, a.filename, a.asset_id)))
        h_content = "|".join(f"{a.asset_id}:{a.duration_sec:.3f}:{a.size_bytes}" for a in sorted_assets)
        snap_hash = hashlib.sha256(h_content.encode("utf-8")).hexdigest()[:16]
        return CatalogSnapshot(snapshot_hash=snap_hash, assets=sorted_assets)

    @staticmethod
    def slice_asset(
        asset: MediaAsset,
        segment_duration: float = 10.0,
        fps: float = 30.0,
        merge_remainder: bool = True
    ) -> List[CatalogSegment]:
        """
        Slices a single video asset into discrete frame-aligned segments.
        Guarantees that remainder < segment_duration is merged if merge_remainder=True.
        """
        if asset.media_type != "video" or not asset.is_valid or asset.duration_sec <= 0:
            return []

        total_frames = sec_to_frames(asset.duration_sec, fps)
        step_frames = max(1, sec_to_frames(segment_duration, fps))

        if total_frames <= step_frames:
            # Whole video is a single segment
            q_start, q_end, frames = quantize_time_range_frames(0.0, asset.duration_sec, fps)
            seg_id = f"{asset.asset_id}_0_{frames}"
            return [
                CatalogSegment(
                    segment_id=seg_id,
                    asset_id=asset.asset_id,
                    source_path=asset.file_path,
                    source_filename=asset.filename,
                    start_sec=q_start,
                    end_sec=q_end,
                    duration_sec=round(q_end - q_start, 6),
                    start_frame=0,
                    end_frame=frames,
                    frame_count=frames
                )
            ]

        segments: List[CatalogSegment] = []
        curr_frame = 0

        while curr_frame + step_frames <= total_frames:
            next_frame = curr_frame + step_frames
            # Check remainder
            remainder = total_frames - next_frame
            if remainder > 0 and remainder < step_frames and merge_remainder:
                # Merge remainder into this last segment
                next_frame = total_frames

            q_start = frames_to_sec(curr_frame, fps)
            q_end = frames_to_sec(next_frame, fps)
            frames = next_frame - curr_frame
            seg_id = f"{asset.asset_id}_{curr_frame}_{next_frame}"

            segments.append(
                CatalogSegment(
                    segment_id=seg_id,
                    asset_id=asset.asset_id,
                    source_path=asset.file_path,
                    source_filename=asset.filename,
                    start_sec=q_start,
                    end_sec=q_end,
                    duration_sec=round(q_end - q_start, 6),
                    start_frame=curr_frame,
                    end_frame=next_frame,
                    frame_count=frames
                )
            )

            curr_frame = next_frame
            if curr_frame >= total_frames:
                break

        return segments

    def slice_all(
        self,
        snapshot: Optional[CatalogSnapshot] = None,
        segment_duration: float = 10.0,
        fps: float = 30.0,
        merge_remainder: bool = True
    ) -> List[CatalogSegment]:
        """Slice all video assets in snapshot deterministically."""
        snap = snapshot or self.create_snapshot()
        all_segs: List[CatalogSegment] = []
        for asset in snap.assets:
            if asset.media_type == "video":
                segs = self.slice_asset(asset, segment_duration=segment_duration, fps=fps, merge_remainder=merge_remainder)
                all_segs.extend(segs)
        return all_segs
