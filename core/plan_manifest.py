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


@dataclass
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
    status: str = "Chờ xử lý"

    @property
    def source_video_name(self) -> str:
        return self.source_filename

    @property
    def source_video_path(self) -> str:
        return self.source_path

    @property
    def duration(self) -> float:
        return round(self.end_sec - self.start_sec, 6)

    @property
    def formatted_start(self) -> str:
        m, s = divmod(int(self.start_sec), 60)
        h, m = divmod(m, 60)
        frac = int(round((self.start_sec - int(self.start_sec)) * 100))
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}.{frac:02d}"
        return f"{m:02d}:{s:02d}.{frac:02d}"

    @property
    def formatted_end(self) -> str:
        end = self.trim_end_sec if self.trim_end_sec is not None else self.end_sec
        m, s = divmod(int(end), 60)
        h, m = divmod(m, 60)
        frac = int(round((end - int(end)) * 100))
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}.{frac:02d}"
        return f"{m:02d}:{s:02d}.{frac:02d}"

    @property
    def formatted_effective(self) -> str:
        m, s = divmod(int(self.effective_duration), 60)
        h, m = divmod(m, 60)
        frac = int(round((self.effective_duration - int(self.effective_duration)) * 100))
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}.{frac:02d}"
        return f"{m:02d}:{s:02d}.{frac:02d}"

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
        start_frame = seg.start_frame
        end_frame = seg.end_frame
        frame_count = seg.frame_count

        if trim_duration is not None and trim_duration < seg.duration_sec:
            eff_dur = round(trim_duration, 6)
            is_trimmed = True
            trim_end = round(seg.start_sec + trim_duration, 6)
            fps = seg.frame_count / seg.duration_sec if seg.duration_sec > 0 else 30.0
            trim_frames = max(1, round(trim_duration * fps))
            end_frame = start_frame + trim_frames
            frame_count = trim_frames

        clip_id = f"clip_v{target_video_index}_p{plan_position}_{seg.segment_id}"
        return cls(
            clip_id=clip_id,
            segment_id=seg.segment_id,
            asset_id=seg.asset_id,
            source_path=seg.source_path,
            source_filename=seg.source_filename,
            start_frame=start_frame,
            end_frame=end_frame,
            frame_count=frame_count,
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

        # Compute deterministic manifest ID preserving batch/playlist boundaries and full source identity (V05)
        video_reprs = []
        for b_idx, batch in enumerate(video_batches):
            b_clips = []
            for c in batch:
                asset_key = c.asset_id or c.source_path or c.source_filename
                b_clips.append(f"{asset_key}:{c.clip_id}:{c.effective_duration:.3f}:{c.start_frame}:{c.end_frame}")
            video_reprs.append(f"BATCH_{b_idx}:[{'|'.join(b_clips)}]")

        audio_reprs = []
        for pl_idx, pl in enumerate(audio_pls):
            pl_items = []
            for item in pl:
                src = item.get("asset_id") or item.get("source_audio_path") or item.get("source_audio_name") or ""
                dur = float(item.get("effective_duration", 0.0))
                trim = item.get("trim_end_sec")
                pos = item.get("playlist_position", 0)
                pl_items.append(f"{src}:{dur:.3f}:{trim}:{pos}")
            audio_reprs.append(f"PLAYLIST_{pl_idx}:[{'|'.join(pl_items)}]")

        meta_json = json.dumps(meta, sort_keys=True, ensure_ascii=False)
        content = (
            f"SCHEMA_V3::{seed}::{catalog_snapshot_hash}::"
            f"V:[{';'.join(video_reprs)}]::"
            f"A:[{';'.join(audio_reprs)}]::"
            f"M:[{meta_json}]"
        )
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
