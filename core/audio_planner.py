"""
Audio Planner Module (V3).
Provides deterministic, sample-accurate audio playlist planning with:
- Deck shuffle and anti-adjacent track protection across cycle boundaries
- Sample-accurate trimming in 'fit_target' mode without arbitrary 0.5s lower bound
- Full track preservation in 'keep_full_tracks' mode
- Batch playlist generation for audio-only and multi-video batch pipelines
"""
import copy
import random
import uuid
from typing import List, Dict, Optional, Tuple, Any

from core.errors import InsufficientAudioError
from core.policies import AudioMatchMode
from core.timebase import sec_to_samples, samples_to_sec
from core.media_catalog import MediaAsset
from core.config import AudioInfo, AudioPlaylistItem, AudioConfig


class AudioPlanner:
    """Core V3 Audio Playlist Planner."""

    def __init__(
        self,
        tracks: List[Any],
        config: Optional[AudioConfig] = None,
        seed: Optional[int] = None
    ):
        self.tracks = [t for t in tracks if getattr(t, "is_valid", True) and getattr(t, "duration_sec", getattr(t, "duration", 0.0)) > 0]
        self.config = config or AudioConfig()
        self.rng = random.Random(seed) if seed is not None else random.Random()
        self.seed = seed

    @property
    def total_tracks(self) -> int:
        return len(self.tracks)

    @property
    def total_duration(self) -> float:
        return sum(self._get_duration(t) for t in self.tracks)

    @staticmethod
    def _get_duration(track: Any) -> float:
        return float(getattr(track, "duration_sec", getattr(track, "duration", 0.0)))

    @staticmethod
    def _get_path(track: Any) -> str:
        return str(getattr(track, "file_path", getattr(track, "path", "")))

    @staticmethod
    def _get_name(track: Any) -> str:
        return str(getattr(track, "filename", getattr(track, "source_audio_name", "")))

    @staticmethod
    def _get_sample_rate(track: Any) -> int:
        sr = getattr(track, "sample_rate", 48000)
        return sr if sr > 0 else 48000

    def plan_single_playlist(self, target_duration: Optional[float] = None) -> List[AudioPlaylistItem]:
        """
        Creates an ordered playlist of AudioPlaylistItem objects for a single output.
        """
        if not self.tracks:
            raise InsufficientAudioError("Kho âm thanh trống! Vui lòng quét thư mục chứa file âm thanh hợp lệ.")

        target_dur = target_duration if target_duration is not None else self.config.target_duration
        if target_dur <= 0.0:
            raise InsufficientAudioError("Thời lượng âm thanh mục tiêu phải lớn hơn 0.")

        # Single track validation
        if len(self.tracks) == 1:
            track_dur = self._get_duration(self.tracks[0])
            if not self.config.allow_single_track_loop and target_dur > track_dur:
                raise InsufficientAudioError(
                    f"Kho chỉ có 1 bài hát ({self._get_name(self.tracks[0])} - {track_dur:.1f}s), "
                    f"không đủ cho mục tiêu {target_dur:.1f}s nếu không cho phép lặp bài đơn."
                )

        playlist: List[AudioPlaylistItem] = []
        accumulated_samples = 0
        cycle_idx = 1
        last_track_path: Optional[str] = None
        pos = 1

        # Determine reference sample rate
        ref_sr = self._get_sample_rate(self.tracks[0])
        target_samples = sec_to_samples(target_dur, ref_sr)

        is_keep_full = (self.config.mode in ("keep_all", "keep_full_tracks"))

        while accumulated_samples < target_samples:
            # Create a shuffled deck for this cycle
            deck = list(self.tracks)
            self.rng.shuffle(deck)

            # Apply Anti-adjacent rule across cycles if multiple tracks exist
            if (self.config.no_consecutive_same_track
                    and len(deck) > 1
                    and last_track_path is not None
                    and self._get_path(deck[0]) == last_track_path):
                # Swap index 0 with first non-matching element
                swap_idx = None
                for j in range(1, len(deck)):
                    if self._get_path(deck[j]) != last_track_path:
                        swap_idx = j
                        break
                if swap_idx is not None:
                    deck[0], deck[swap_idx] = deck[swap_idx], deck[0]

            for track in deck:
                if accumulated_samples >= target_samples:
                    break

                track_dur = self._get_duration(track)
                track_samples = sec_to_samples(track_dur, ref_sr)
                needed_samples = target_samples - accumulated_samples
                item_id = uuid.uuid4().hex[:12]

                if track_samples > needed_samples and not is_keep_full:
                    # Chế độ A (fit_target): Trim last track exactly to needed duration
                    eff_sec = samples_to_sec(needed_samples, ref_sr)
                    item = AudioPlaylistItem(
                        item_id=item_id,
                        source_audio_path=self._get_path(track),
                        source_audio_name=self._get_name(track),
                        duration=track_dur,
                        effective_duration=eff_sec,
                        trim_end_sec=eff_sec,
                        cycle_index=cycle_idx,
                        playlist_position=pos,
                        status="Chờ xử lý"
                    )
                    accumulated_samples += needed_samples
                else:
                    # Keep full track
                    item = AudioPlaylistItem(
                        item_id=item_id,
                        source_audio_path=self._get_path(track),
                        source_audio_name=self._get_name(track),
                        duration=track_dur,
                        effective_duration=track_dur,
                        trim_end_sec=None,
                        cycle_index=cycle_idx,
                        playlist_position=pos,
                        status="Chờ xử lý"
                    )
                    accumulated_samples += track_samples

                playlist.append(item)
                last_track_path = self._get_path(track)
                pos += 1

            cycle_idx += 1

        return playlist

    def plan_batch(self, batch_count: int, target_duration: Optional[float] = None) -> List[List[AudioPlaylistItem]]:
        """Plans batch_count independent audio playlists."""
        batches: List[List[AudioPlaylistItem]] = []
        for _ in range(batch_count):
            pl = self.plan_single_playlist(target_duration=target_duration)
            batches.append(pl)
        return batches
