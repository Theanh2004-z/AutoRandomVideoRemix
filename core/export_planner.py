"""
Export Planner Module (V3).
Coordinates video and audio planners into a unified, reproducible PlanManifest contract.
Supports:
- Dual timing contracts:
    - fit_target: Video timeline dictates duration; audio trims to fit.
    - keep_full_tracks: Full audio tracks dictate duration; video expands to match.
- Standalone audio batch planning.
- Side-effect-free validate_plan() for Preview and Pre-flight checks.
"""
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import asdict

from core.errors import InsufficientSegmentsError, InsufficientAudioError, ConfigValidationError
from core.policies import UniquePolicy, AntiConsecutivePolicy, AudioMatchMode
from core.models import NormalizedCutConfig, ConfigAdapterV2
from core.config import OutputConfig, AudioConfig, AudioPlaylistItem
from core.media_catalog import MediaCatalog, CatalogSnapshot, CatalogSegment
from core.plan_manifest import PlanClip, PlanManifest
from core.video_planner import VideoPlanner
from core.audio_planner import AudioPlanner


class ExportPlanner:
    """Master coordinator creating reproducible PlanManifests for any export mode."""

    def __init__(
        self,
        video_segments: List[CatalogSegment],
        audio_tracks: List[Any],
        cut_config: NormalizedCutConfig,
        audio_config: AudioConfig,
        output_config: OutputConfig,
        catalog_snapshot_hash: str = "snapshot_0",
        seed: Optional[int] = None
    ):
        self.video_segments = video_segments
        self.audio_tracks = audio_tracks
        self.cut_config = cut_config
        self.audio_config = audio_config
        self.output_config = output_config
        self.catalog_snapshot_hash = catalog_snapshot_hash
        self.seed = seed

    def validate_plan(self) -> Tuple[bool, str]:
        """
        Pure preview preflight validation.
        Validates whether enough footage and audio exist without mutating state or consuming history.
        Returns: (is_valid, message)
        """
        target = self.output_config.export_target
        try:
            if target != "audio_only":
                if not self.video_segments:
                    return False, "Kho video trống hoặc chưa có đoạn cắt nào hợp lệ."
                # Run a dry-run video plan
                vp = VideoPlanner(self.video_segments, self.cut_config, seed=self.seed)
                vp.plan_batch()

            if target in ("video_with_random_audio", "audio_only"):
                if not self.audio_tracks:
                    return False, "Kho âm thanh trống hoặc chưa có file âm thanh hợp lệ."
                ap = AudioPlanner(self.audio_tracks, self.audio_config, seed=self.seed)
                dur = self.cut_config.target_total_duration
                ap.plan_batch(batch_count=self.cut_config.batch_count, target_duration=dur)

            return True, "Kế hoạch hợp lệ và sẵn sàng xuất."
        except (InsufficientSegmentsError, InsufficientAudioError, ConfigValidationError) as e:
            return False, str(e)
        except Exception as e:
            return False, f"Lỗi xác thực kế hoạch: {e}"

    def create_manifest(self) -> PlanManifest:
        """
        Builds the complete PlanManifest containing all video batches and audio playlists.
        """
        target = self.output_config.export_target
        total_batches = self.cut_config.batch_count
        video_plans: List[List[PlanClip]] = []
        audio_playlists: List[List[Dict[str, Any]]] = []

        is_keep_full = (self.audio_config.mode in ("keep_all", "keep_full_tracks"))

        if target == "audio_only":
            # Standalone audio export for total_batches outputs
            ap = AudioPlanner(self.audio_tracks, self.audio_config, seed=self.seed)
            playlists = ap.plan_batch(
                batch_count=total_batches,
                target_duration=self.cut_config.target_total_duration
            )
            audio_playlists = [
                [asdict(item) for item in pl]
                for pl in playlists
            ]
            video_plans = []

        elif target == "video_only":
            # Video only
            vp = VideoPlanner(self.video_segments, self.cut_config, seed=self.seed)
            video_plans = vp.plan_batch()
            audio_playlists = []

        else:
            # Combined video_with_random_audio
            if is_keep_full:
                # 1. Nhạc quyết định thời lượng: plan audio first
                ap = AudioPlanner(self.audio_tracks, self.audio_config, seed=self.seed)
                raw_pls = ap.plan_batch(
                    batch_count=total_batches,
                    target_duration=self.cut_config.target_total_duration
                )
                audio_playlists = [
                    [asdict(item) for item in pl]
                    for pl in raw_pls
                ]

                # 2. Plan video matching exact combined audio duration for each batch
                all_v_plans = []
                for b_idx in range(total_batches):
                    audio_dur = sum(item.effective_duration for item in raw_pls[b_idx])
                    # Create matching config for this video's exact duration
                    v_cfg = NormalizedCutConfig(
                        segment_duration=self.cut_config.segment_duration,
                        target_total_duration=audio_dur,
                        batch_count=1,
                        merge_remainder=self.cut_config.merge_remainder,
                        unique_policy=self.cut_config.unique_policy,
                        anti_consecutive_policy=self.cut_config.anti_consecutive_policy,
                        trim_last_segment=self.cut_config.trim_last_segment,
                        fps=self.cut_config.fps
                    )
                    v_seed = (self.seed + b_idx) if self.seed is not None else None
                    vp = VideoPlanner(self.video_segments, v_cfg, seed=v_seed)
                    plan = vp.plan_batch()[0]
                    # Update video index
                    updated_plan = [
                        PlanClip(
                            clip_id=f"clip_v{b_idx+1}_p{c.plan_position}_{c.segment_id}",
                            segment_id=c.segment_id,
                            asset_id=c.asset_id,
                            source_path=c.source_path,
                            source_filename=c.source_filename,
                            start_frame=c.start_frame,
                            end_frame=c.end_frame,
                            frame_count=c.frame_count,
                            start_sec=c.start_sec,
                            end_sec=c.end_sec,
                            effective_duration=c.effective_duration,
                            target_video_index=b_idx + 1,
                            plan_position=c.plan_position,
                            is_trimmed=c.is_trimmed,
                            trim_end_sec=c.trim_end_sec
                        )
                        for c in plan
                    ]
                    all_v_plans.append(updated_plan)
                video_plans = all_v_plans

            else:
                # fit_target: Hình quyết định timeline
                vp = VideoPlanner(self.video_segments, self.cut_config, seed=self.seed)
                video_plans = vp.plan_batch()

                # For each video, plan audio to match its exact effective duration
                ap = AudioPlanner(self.audio_tracks, self.audio_config, seed=self.seed)
                raw_pls = []
                for b_idx, v_plan in enumerate(video_plans):
                    v_dur = sum(c.effective_duration for c in v_plan)
                    pl = ap.plan_single_playlist(target_duration=v_dur)
                    raw_pls.append(pl)

                audio_playlists = [
                    [asdict(item) for item in pl]
                    for pl in raw_pls
                ]

        metadata = {
            "export_target": target,
            "resolution": self.output_config.resolution,
            "fps": self.output_config.fps,
            "hw_accel": self.output_config.hw_accel,
            "audio_mode": self.audio_config.mode
        }

        return PlanManifest.create(
            seed=self.seed if self.seed is not None else 0,
            catalog_snapshot_hash=self.catalog_snapshot_hash,
            video_batches=video_plans,
            audio_playlists=audio_playlists,
            metadata=metadata
        )
