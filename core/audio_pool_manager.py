"""
Audio Pool Manager — manages intact audio tracks, cycle-based shuffling,
anti-adjacent duplicate protection, and duration fitting.
"""
import copy
import random
import uuid
from typing import List, Optional
from core.config import AudioInfo, AudioPlaylistItem, AudioConfig


from core.errors import InsufficientAudioError


class AudioPool:
    """Manages the pool of intact source audio files."""

    def __init__(self, audios: Optional[List[AudioInfo]] = None):
        self._tracks: List[AudioInfo] = list(audios) if audios else []

    def set_tracks(self, audios: List[AudioInfo]):
        self._tracks = [a for a in audios if a.is_valid and a.duration > 0.0]

    @property
    def tracks(self) -> List[AudioInfo]:
        return list(self._tracks)

    @property
    def total_tracks(self) -> int:
        return len(self._tracks)

    @property
    def total_duration(self) -> float:
        return sum(t.duration for t in self._tracks)

    def shuffle_deck(self) -> List[AudioInfo]:
        """Return a newly shuffled copy of the full tracks list."""
        deck = list(self._tracks)
        random.shuffle(deck)
        return deck


class AudioPicker:
    """
    Builds a sequential playlist from the AudioPool to meet target duration.
    Implements cycle shuffling and enforces:
      Track_cycle_k[0] != Track_cycle_k-1[-1]
    """

    def __init__(self, pool: AudioPool, config: AudioConfig):
        self.pool = pool
        self.config = config

    def pick_playlist(self, target_duration: Optional[float] = None) -> List[AudioPlaylistItem]:
        """
        Creates an ordered playlist of AudioPlaylistItem objects.
        """
        if self.pool.total_tracks == 0:
            raise InsufficientAudioError("Kho âm thanh trống! Vui lòng quét thư mục chứa file âm thanh.")

        if target_duration is None:
            target_duration = self.config.target_duration

        if target_duration <= 0.0:
            raise InsufficientAudioError("Thời lượng âm thanh mục tiêu phải lớn hơn 0.")

        # Special case: only 1 track
        if self.pool.total_tracks == 1:
            if not self.config.allow_single_track_loop and target_duration > self.pool.total_duration:
                raise InsufficientAudioError(
                    f"Kho chỉ có 1 file âm thanh ({self.pool.tracks[0].filename} - {self.pool.total_duration:.1f}s), "
                    f"không đủ cho mục tiêu {target_duration:.1f}s nếu không cho phép lặp bài đơn."
                )

        playlist: List[AudioPlaylistItem] = []
        accumulated = 0.0
        cycle_idx = 1
        last_track_path: Optional[str] = None
        pos = 1

        while (target_duration - accumulated) > 0.05 and cycle_idx < 1000:
            # Generate a shuffled deck for this cycle
            deck = self.pool.shuffle_deck()

            # Apply Anti-adjacent rule across cycles if multiple tracks exist
            if (self.config.no_consecutive_same_track
                    and len(deck) > 1
                    and last_track_path is not None
                    and deck[0].path == last_track_path):
                # Swap index 0 with first non-matching element
                swap_idx = None
                for j in range(1, len(deck)):
                    if deck[j].path != last_track_path:
                        swap_idx = j
                        break
                if swap_idx is not None:
                    deck[0], deck[swap_idx] = deck[swap_idx], deck[0]

            # Draw tracks from this cycle
            for track in deck:
                if accumulated >= target_duration or (target_duration - accumulated) < 0.1:
                    break

                needed = target_duration - accumulated
                item_id = uuid.uuid4().hex[:12]

                # Check if this track exceeds target duration
                if track.duration > needed and self.config.mode == "fit_target":
                    # Chế độ A: Cắt bài cuối để khớp thời lượng (tối thiểu 0.5s)
                    eff_dur = max(0.5, round(needed, 3))
                    item = AudioPlaylistItem(
                        item_id=item_id,
                        source_audio_path=track.path,
                        source_audio_name=track.filename,
                        duration=track.duration,
                        effective_duration=eff_dur,
                        trim_end_sec=eff_dur,
                        cycle_index=cycle_idx,
                        playlist_position=pos,
                        status="Chờ xử lý"
                    )
                else:
                    # Chế độ B (hoặc chưa vượt thời lượng): Giữ nguyên bài
                    item = AudioPlaylistItem(
                        item_id=item_id,
                        source_audio_path=track.path,
                        source_audio_name=track.filename,
                        duration=track.duration,
                        effective_duration=track.duration,
                        trim_end_sec=None,
                        cycle_index=cycle_idx,
                        playlist_position=pos,
                        status="Chờ xử lý"
                    )

                playlist.append(item)
                accumulated += item.effective_duration
                last_track_path = track.path
                pos += 1

            if not self.config.auto_reshuffle_cycle and accumulated < target_duration:
                # User does not allow multiple cycles
                break

            cycle_idx += 1

        return playlist
