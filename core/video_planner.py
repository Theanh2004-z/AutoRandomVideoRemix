"""
Video Planner Engine (V3).
Provides deterministic, frame-accurate batch planning with explicit policies:
- Uniqueness: UNIQUE_BATCH, UNIQUE_OUTPUT, CYCLE_OUTPUT
- Anti-consecutive: OFF, SOFT, STRICT
- Frame timeline accumulation with zero float drift
"""
import copy
import random
from typing import List, Dict, Optional, Set, Tuple

from core.errors import InsufficientSegmentsError, ConfigValidationError
from core.policies import UniquePolicy, AntiConsecutivePolicy
from core.models import NormalizedCutConfig, ConfigAdapterV2
from core.timebase import sec_to_frames, frames_to_sec
from core.media_catalog import CatalogSegment
from core.plan_manifest import PlanClip, PlanManifest


class VideoPlanner:
    """Core V3 Batch Video Planner."""

    def __init__(
        self,
        segments: List[CatalogSegment],
        config: NormalizedCutConfig,
        seed: Optional[int] = None
    ):
        self.segments = list(segments)
        self.config = config
        self.config.validate()
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.seed = seed

    def plan_batch(self) -> List[List[PlanClip]]:
        """
        Plans all videos in the batch according to the configured policies.
        Returns: List of video plans, where each plan is a List[PlanClip].
        """
        target_frames = sec_to_frames(self.config.target_total_duration, self.config.fps)
        total_batches = self.config.batch_count

        # Global set of segment_ids used across the batch (for UNIQUE_BATCH)
        batch_used_ids: Set[str] = set()
        all_plans: List[List[PlanClip]] = []

        for video_idx in range(1, total_batches + 1):
            plan = self._plan_single_video(
                video_index=video_idx,
                target_frames=target_frames,
                batch_used_ids=batch_used_ids
            )
            all_plans.append(plan)

        return all_plans

    def _plan_single_video(
        self,
        video_index: int,
        target_frames: int,
        batch_used_ids: Set[str]
    ) -> List[PlanClip]:
        """Plans a single video output to reach target_frames."""
        # 1. Determine initial eligible pool for this video
        if self.config.unique_policy == UniquePolicy.UNIQUE_BATCH:
            available = [s for s in self.segments if s.segment_id not in batch_used_ids]
        else:
            # UNIQUE_OUTPUT or CYCLE_OUTPUT starts with all segments
            available = list(self.segments)

        if not available:
            raise InsufficientSegmentsError(
                f"Không có đoạn video nào hợp lệ để tạo video #{video_index}."
            )

        # Shuffle available pool with deterministic RNG
        self.rng.shuffle(available)

        plan_clips: List[PlanClip] = []
        accumulated_frames = 0
        last_asset_id: Optional[str] = None
        plan_position = 1

        # Track segments used in THIS video (for UNIQUE_OUTPUT)
        video_used_ids: Set[str] = set()

        while accumulated_frames < target_frames:
            # Filter available candidates
            candidates = [
                s for s in available
                if (self.config.unique_policy != UniquePolicy.UNIQUE_OUTPUT or s.segment_id not in video_used_ids)
            ]

            if not candidates:
                # Pool exhausted in this video
                if self.config.unique_policy == UniquePolicy.CYCLE_OUTPUT:
                    # Refill draw pool
                    available = list(self.segments)
                    self.rng.shuffle(available)
                    candidates = list(available)
                else:
                    # Insufficient footage
                    gathered_sec = frames_to_sec(accumulated_frames, self.config.fps)
                    target_sec = self.config.target_total_duration
                    needed_sec = round(target_sec - gathered_sec, 2)
                    raise InsufficientSegmentsError(
                        f"Không đủ đoạn để hoàn thành video #{video_index}!\n"
                        f"• Đã ghép: {len(plan_clips)} đoạn ({gathered_sec:.1f}s / {target_sec:.1f}s)\n"
                        f"• Cần thêm: {needed_sec:.1f}s\n"
                        f"• Giải pháp: Bật 'Bốc có hoàn lại' (cycle_output), thêm video nguồn, hoặc giảm thời lượng đầu ra."
                    )

            # Select candidate respecting AntiConsecutivePolicy
            chosen_seg = self._select_candidate(candidates, last_asset_id)
            if chosen_seg is None:
                # Strict policy violation
                raise InsufficientSegmentsError(
                    f"Chính sách Strict Anti-Consecutive vi phạm: không tìm thấy đoạn từ nguồn khác video vừa chọn."
                )

            # Check if this segment will overshoot target_frames
            seg_frames = chosen_seg.frame_count
            new_total = accumulated_frames + seg_frames

            if new_total >= target_frames:
                # Last segment reach or overshoot
                if self.config.trim_last_segment and new_total > target_frames:
                    needed_frames = target_frames - accumulated_frames
                    trim_duration = frames_to_sec(needed_frames, self.config.fps)
                    clip = PlanClip.from_catalog_segment(
                        seg=chosen_seg,
                        target_video_index=video_index,
                        plan_position=plan_position,
                        trim_duration=trim_duration
                    )
                    accumulated_frames += needed_frames
                else:
                    clip = PlanClip.from_catalog_segment(
                        seg=chosen_seg,
                        target_video_index=video_index,
                        plan_position=plan_position
                    )
                    accumulated_frames += seg_frames

                plan_clips.append(clip)
                batch_used_ids.add(chosen_seg.segment_id)
                video_used_ids.add(chosen_seg.segment_id)
                break
            else:
                clip = PlanClip.from_catalog_segment(
                    seg=chosen_seg,
                    target_video_index=video_index,
                    plan_position=plan_position
                )
                accumulated_frames += seg_frames
                plan_clips.append(clip)
                batch_used_ids.add(chosen_seg.segment_id)
                video_used_ids.add(chosen_seg.segment_id)
                last_asset_id = chosen_seg.asset_id
                plan_position += 1

                # Remove from available to avoid duplicate draw before refill
                available.remove(chosen_seg)

        return plan_clips

    def _select_candidate(
        self,
        candidates: List[CatalogSegment],
        last_asset_id: Optional[str]
    ) -> Optional[CatalogSegment]:
        """Selects a candidate respecting AntiConsecutivePolicy."""
        if not candidates:
            return None

        if self.config.anti_consecutive_policy == AntiConsecutivePolicy.OFF or last_asset_id is None:
            return candidates[0]

        # Try to find a candidate from a different asset
        different_source_candidates = [s for s in candidates if s.asset_id != last_asset_id]
        if different_source_candidates:
            return different_source_candidates[0]

        # Only same source available
        if self.config.anti_consecutive_policy == AntiConsecutivePolicy.STRICT:
            return None  # Rejection
        else:
            # SOFT policy falls back to available candidate
            return candidates[0]
