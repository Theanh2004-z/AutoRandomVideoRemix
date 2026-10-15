"""
Segment Slicer — Sequential video segmentation engine.

Divides each source video into consecutive, non-overlapping segments of a
fixed duration.  The final remainder (shorter than the target length) is
optionally merged with the preceding segment so no footage is lost.

Segmentation rules
------------------
1. Segments are exactly `segment_duration` seconds long, starting at t=0.
2. If merge_remainder is True and the final piece is shorter than
   `segment_duration`, it is folded into the preceding segment.
3. If the video is shorter than `segment_duration`, a single segment
   covering the entire video is produced (no merging needed).
4. Every source frame belongs to exactly one segment — nothing is skipped.

Examples (segment_duration = 10 s)
------------------------------------
  35 s → [0-10, 10-20, 20-35]          last merged: 15 s
  48 s → [0-10, 10-20, 20-30, 30-48]   last merged: 18 s
  23 s → [0-10, 10-23]                  last merged: 13 s
  60 s → [0-10, 10-20, … , 50-60]       6 × 10 s — no merge needed
   8 s → [0-8]                           shorter than seg_dur, single seg
  15 s → [0-15]                          10 + 5 remainder → merge → 15 s
"""
import uuid
from typing import List

from core.config import VideoInfo, SegmentInfo


class SegmentSlicer:
    """Slices a single VideoInfo into consecutive SegmentInfo objects."""

    def __init__(self, segment_duration: float = 10.0, merge_remainder: bool = True):
        if segment_duration <= 0:
            raise ValueError(f"segment_duration must be > 0, got {segment_duration!r}")
        self.segment_duration = segment_duration
        self.merge_remainder = merge_remainder

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def slice(self, video: VideoInfo) -> List[SegmentInfo]:
        """
        Slice one video into consecutive segments.

        Returns an ordered list of SegmentInfo.
        Returns an empty list if the video is invalid or has zero duration.
        """
        if not video.is_valid or video.duration <= 0.0:
            return []

        total = video.duration
        seg_dur = self.segment_duration
        segments: List[SegmentInfo] = []
        current = 0.0
        seg_idx = 1

        while current < total:
            remaining = total - current

            # Fold remainder into the preceding segment when it is shorter
            # than a full segment and there is already at least one segment.
            if self.merge_remainder and remaining < seg_dur and segments:
                last = segments[-1]
                last.end_sec = round(total, 6)
                last.duration = round(total - last.start_sec, 6)
                break

            end = min(current + seg_dur, total)
            seg = SegmentInfo(
                segment_id=uuid.uuid4().hex,
                source_video_path=video.path,
                source_video_name=video.filename,
                start_sec=round(current, 6),
                end_sec=round(end, 6),
                duration=round(end - current, 6),
                segment_index=seg_idx,
            )
            segments.append(seg)
            current = end
            seg_idx += 1

        return segments

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def describe(self, video: VideoInfo) -> str:
        """Return a human-readable description of the segments that would be produced."""
        segs = self.slice(video)
        lines = [f"Video: {video.filename} ({video.duration:.2f}s) → {len(segs)} đoạn:"]
        for s in segs:
            lines.append(f"  [{s.segment_index}] {s.start_sec:.2f}s – {s.end_sec:.2f}s ({s.duration:.2f}s)")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Convenience helpers
# ------------------------------------------------------------------

def slice_all_videos(
    videos: List[VideoInfo],
    segment_duration: float = 10.0,
    merge_remainder: bool = True,
) -> List[SegmentInfo]:
    """
    Slice every video in *videos* and return the combined flat pool in
    source order (A1, A2, …, B1, B2, …).
    """
    slicer = SegmentSlicer(
        segment_duration=segment_duration,
        merge_remainder=merge_remainder,
    )
    pool: List[SegmentInfo] = []
    for video in videos:
        pool.extend(slicer.slice(video))
    return pool
