"""
Plan Manifest Module (V3).
Defines the immutable PlanManifest contract, serialization, and reproducibility hashing.
"""
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Any, Optional
import hashlib
import json

from core.models import NormalizedCutConfig
from core.media_catalog import CatalogSegment


@dataclass(frozen=True)
class PlanClip:
    """Individual cut clip bound to an output video position."""
    clip_id: str
    segment_id: str
    asset_id: str
    source_path: str
    source_filename: str
    start_frame: int
    end_frame: int
    frame_count: int
    start_sec: float
    end_sec: float
    effective_duration: float
    target_video_index: int
    plan_position: int
    is_trimmed: bool = False
    trim_end_sec: Optional[float] = None

    @classmethod
    def from_catalog_segment(
        cls,
        seg: CatalogSegment,
        target_video_index: int,
        plan_position: int,
        trim_duration: Optional[float] = None
    ) -> "PlanClip":
        eff_dur = seg.duration_sec
        is_trimmed = False
        trim_end = None
        if trim_duration is not None and trim_duration < seg.duration_sec:
            eff_dur = round(trim_duration, 6)
            is_trimmed = True
            trim_end = round(seg.start_sec + trim_duration, 6)

        clip_id = f"clip_v{target_video_index}_p{plan_position}_{seg.segment_id}"
        return cls(
            clip_id=clip_id,
            segment_id=seg.segment_id,
            asset_id=seg.asset_id,
            source_path=seg.source_path,
            source_filename=seg.source_filename,
            start_frame=seg.start_frame,
            end_frame=seg.end_frame,
            frame_count=seg.frame_count,
            start_sec=seg.start_sec,
            end_sec=seg.end_sec,
            effective_duration=eff_dur,
            target_video_index=target_video_index,
            plan_position=plan_position,
            is_trimmed=is_trimmed,
            trim_end_sec=trim_end
        )


@dataclass
class PlanManifest:
    """Complete, reproducible manifest of all video and audio plans in a batch."""
    manifest_id: str
    seed: int
    catalog_snapshot_hash: str
    total_videos: int
    video_batches: List[List[PlanClip]]
    audio_playlists: List[List[Dict[str, Any]]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        seed: int,
        catalog_snapshot_hash: str,
        video_batches: List[List[PlanClip]],
        audio_playlists: Optional[List[List[Dict[str, Any]]]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> "PlanManifest":
        audio_pls = audio_playlists or []
        meta = metadata or {}

        # Compute deterministic manifest ID from clips and seed
        clip_reprs = []
        for b in video_batches:
            for c in b:
                clip_reprs.append(f"{c.clip_id}:{c.effective_duration:.3f}")
        content = f"{seed}::{catalog_snapshot_hash}::" + "|".join(clip_reprs)
        mid = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

        return cls(
            manifest_id=mid,
            seed=seed,
            catalog_snapshot_hash=catalog_snapshot_hash,
            total_videos=len(video_batches),
            video_batches=video_batches,
            audio_playlists=audio_pls,
            metadata=meta
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "seed": self.seed,
            "catalog_snapshot_hash": self.catalog_snapshot_hash,
            "total_videos": self.total_videos,
            "video_batches": [
                [asdict(c) for c in batch]
                for batch in self.video_batches
            ],
            "audio_playlists": self.audio_playlists,
            "metadata": self.metadata
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)
