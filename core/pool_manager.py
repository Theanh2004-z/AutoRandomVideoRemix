"""
Segment Pool Manager — shuffle, sequential pick, and batch planning.

Full pipeline
-------------
source videos
  → SegmentPool.build()   (calls slice_all_videos internally)
  → SegmentPool.shuffle()
  → BatchPlanner.plan_all()
  → List[List[SegmentInfo]]   (handed to VideoProcessorWorker)

Drawing rules (configured via CutConfig)
-----------------------------------------
• Sequential: segments are taken in shuffled order, not randomly re-drawn.
• no_consecutive_same_source: skip the next candidate when it shares a
  source with the just-picked segment; fall back to any available segment
  only if no alternative exists.
• trim_last_segment: if the last picked segment would overshoot the target
  duration, set trim_end_sec so rendering stops exactly on target.
• allow_reuse: when the pool is exhausted, reshuffle and continue (draw
  with replacement).  Default is False (each segment used at most once).
• no_repeat_across_batch: segments used in one batch output are excluded
  from subsequent batch outputs.
"""
import copy
import random
from typing import Dict, List, Optional, Set

from core.config import CutConfig, SegmentInfo, VideoInfo
from core.segment_slicer import slice_all_videos


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

from core.errors import InsufficientSegmentsError



# ------------------------------------------------------------------
# Pool
# ------------------------------------------------------------------

class SegmentPool:
    """
    Holds all segments produced from source videos.

    The *master* pool preserves source order (A1 A2 … B1 B2 …).
    The *draw* pool starts as a copy of master and is shuffled before picking.
    """

    def __init__(self) -> None:
        self._master: List[SegmentInfo] = []
        self._draw: List[SegmentInfo] = []

    # ---------------------------------------------------------------- build

    def build(
        self,
        videos: List[VideoInfo],
        segment_duration: float,
        merge_remainder: bool = True,
    ) -> None:
        """Build (or rebuild) the pool from source videos."""
        self._master = slice_all_videos(
            videos=videos,
            segment_duration=segment_duration,
            merge_remainder=merge_remainder,
        )
        self._draw = [copy.copy(s) for s in self._master]
        for s in self._draw:
            s.is_used = False

    # ---------------------------------------------------------------- queries

    @property
    def total_segments(self) -> int:
        return len(self._master)

    @property
    def total_duration(self) -> float:
        return sum(s.duration for s in self._master)

    @property
    def master_pool(self) -> List[SegmentInfo]:
        """Ordered, unshuffled snapshot of the pool."""
        return list(self._master)

    @property
    def draw_pool(self) -> List[SegmentInfo]:
        """Current (possibly shuffled) draw pool snapshot."""
        return list(self._draw)

    # ---------------------------------------------------------------- shuffle / reset

    def shuffle(self) -> None:
        """Shuffle the draw pool in-place and reset all is_used flags."""
        random.shuffle(self._draw)
        for s in self._draw:
            s.is_used = False

    def reset_used(self) -> None:
        """Reset is_used on every draw-pool segment (keeps current order)."""
        for s in self._draw:
            s.is_used = False

    # ---------------------------------------------------------------- stats

    def segments_by_source(self) -> Dict[str, List[SegmentInfo]]:
        """Master pool grouped by source video name."""
        result: Dict[str, List[SegmentInfo]] = {}
        for s in self._master:
            result.setdefault(s.source_video_name, []).append(s)
        return result

    def stats_summary(self) -> str:
        by_src = self.segments_by_source()
        lines = [
            f"Kho đoạn: {self.total_segments} đoạn | Tổng: {self.total_duration:.1f}s | "
            f"Từ {len(by_src)} video nguồn"
        ]
        for name, segs in by_src.items():
            dur = sum(s.duration for s in segs)
            lines.append(f"  {name}: {len(segs)} đoạn ({dur:.1f}s)")
        return "\n".join(lines)


# ------------------------------------------------------------------
# RandomPicker — single output video
# ------------------------------------------------------------------

class RandomPicker:
    """
    Draws segments sequentially from the shuffled draw pool to build the
    plan for one output video.
    """

    def __init__(self, pool: SegmentPool, config: CutConfig) -> None:
        self.pool = pool
        self.config = config

    # ---------------------------------------------------------------- public

    def pick_plan(
        self,
        target_duration: float,
        target_video_index: int,
        exclude_ids: Optional[Set[str]] = None,
    ) -> List[SegmentInfo]:
        """
        Build a plan for one output video.

        Args:
            target_duration:    Required total duration in seconds.
            target_video_index: 1-based index of the output video (for labelling).
            exclude_ids:        Segment IDs already used in previous batch outputs.

        Returns:
            Ordered list of SegmentInfo (plan_position and target_video_index set).

        Raises:
            InsufficientSegmentsError
        """
        if exclude_ids is None:
            exclude_ids = set()

        # Working list: ordered copies of draw pool, minus excluded segments.
        working: List[SegmentInfo] = [
            copy.copy(s) for s in self.pool._draw
            if s.segment_id not in exclude_ids
        ]

        if not working:
            if self.config.allow_reuse and self.pool._draw:
                working = [copy.copy(s) for s in self.pool._draw]
                random.shuffle(working)
            else:
                hint = ""
                if self.config.use_export_history:
                    hint = " (Lưu ý: Đang bật 'Loại trừ lịch sử đã xuất', bạn có thể tắt tùy chọn này hoặc xóa lịch sử)."
                raise InsufficientSegmentsError(
                    f"Kho đoạn trống hoặc tất cả đoạn đã bị loại trừ "
                    f"(video #{target_video_index})!{hint}"
                )

        used_ids: Set[str] = set()
        plan: List[SegmentInfo] = []
        accumulated = 0.0
        last_source: Optional[str] = None
        position = 1
        max_steps = max(5000, int(target_duration * 10))
        step = 0

        while (target_duration - accumulated) > 0.05 and step < max_steps:
            step += 1
            chosen = self._pick_next(working, used_ids, last_source)

            if chosen is None:
                if self.config.allow_reuse:
                    # Replenish full draw pool if working was exhausted or partial
                    if len(working) < len(self.pool._draw):
                        working = [copy.copy(s) for s in self.pool._draw]
                    random.shuffle(working)
                    used_ids.clear()
                    chosen = self._pick_next(working, used_ids, last_source)
                    if chosen is None:
                        raise InsufficientSegmentsError(
                            f"Kho đoạn rỗng sau khi hoàn lại (video #{target_video_index})!"
                        )
                else:
                    shortage = target_duration - accumulated
                    raise InsufficientSegmentsError(
                        f"Không đủ đoạn để hoàn thành video #{target_video_index}!\n"
                        f"• Đã ghép: {len(plan)} đoạn ({accumulated:.1f}s / {target_duration:.1f}s)\n"
                        f"• Cần thêm: {shortage:.1f}s\n"
                        f"• Giải pháp: bật 'Bốc có hoàn lại', thêm video nguồn,"
                        f" hoặc giảm thời lượng đầu ra."
                    )

            used_ids.add(chosen.segment_id)

            # Propagate is_used to the master draw pool (for UI display)
            for s in self.pool._draw:
                if s.segment_id == chosen.segment_id:
                    s.is_used = True
                    break

            remaining_needed = target_duration - accumulated
            if remaining_needed <= 0.05:
                break

            plan_seg = chosen.clone_for_plan(position, target_video_index)

            # Precision trim: the last segment may overshoot target
            if (chosen.duration > remaining_needed
                    and self.config.trim_last_segment):
                plan_seg.trim_end_sec = round(chosen.start_sec + remaining_needed, 6)

            eff_dur = plan_seg.effective_duration
            if eff_dur <= 0.05:
                break

            plan.append(plan_seg)
            accumulated += eff_dur
            last_source = chosen.source_video_path
            position += 1

        return plan

    # ---------------------------------------------------------------- internal

    def _pick_next(
        self,
        working: List[SegmentInfo],
        used_ids: Set[str],
        last_source: Optional[str],
    ) -> Optional[SegmentInfo]:
        """
        Return the first available candidate from *working* (in order).
        Prefers non-consecutive source when the option is enabled.
        """
        candidates = [s for s in working if s.segment_id not in used_ids]
        if not candidates:
            return None

        if self.config.no_consecutive_same_source and last_source is not None:
            non_same = [s for s in candidates if s.source_video_path != last_source]
            if non_same:
                return non_same[0]
            # All remaining candidates share the same source — fall through

        return candidates[0]


# ------------------------------------------------------------------
# BatchPlanner — multiple output videos
# ------------------------------------------------------------------

class BatchPlanner:
    """
    Plans config.batch_count output videos from a single SegmentPool.

    When no_repeat_across_batch is True, each segment can appear in at most
    one of the batch outputs.
    """

    def __init__(self, pool: SegmentPool, config: CutConfig) -> None:
        self.pool = pool
        self.config = config

    def plan_all(self, target_duration: float) -> List[List[SegmentInfo]]:
        """
        Create one plan per batch output video.

        Raises InsufficientSegmentsError if any output cannot be fulfilled.
        """
        picker = RandomPicker(self.pool, self.config)
        batch_plans: List[List[SegmentInfo]] = []
        global_used: Set[str] = set()

        history_excluded_ids: Set[str] = set()
        if self.config.use_export_history:
            try:
                from core.history_manager import HistoryManager, _make_segment_key
                hist_keys = HistoryManager().load_history_keys()
                if hist_keys:
                    for seg in self.pool._draw:
                        k = _make_segment_key(seg.source_video_name, seg.start_sec, seg.end_sec)
                        if k in hist_keys:
                            history_excluded_ids.add(seg.segment_id)
            except Exception:
                pass

        for v_idx in range(1, self.config.batch_count + 1):
            exclude = (global_used if self.config.no_repeat_across_batch else set()) | history_excluded_ids
            # If allow_reuse is enabled and all segments are excluded, clear global_used to allow reuse
            available = [s for s in self.pool._draw if s.segment_id not in exclude]
            if not available and self.config.allow_reuse:
                global_used.clear()
                exclude = history_excluded_ids

            plan = picker.pick_plan(
                target_duration=target_duration,
                target_video_index=v_idx,
                exclude_ids=exclude,
            )
            batch_plans.append(plan)

            if self.config.no_repeat_across_batch:
                for seg in plan:
                    global_used.add(seg.segment_id)

        return batch_plans
