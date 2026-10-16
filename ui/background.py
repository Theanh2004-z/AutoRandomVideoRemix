"""Background preparation; this module never reads or updates widgets."""
from dataclasses import dataclass
from pathlib import Path
import os
import re

from PySide6.QtCore import QThread, Signal
from core.models import compute_media_content_hash, ConfigAdapterV2
from core.config import AudioPlaylistItem
from core.media_catalog import CatalogSegment
from core.pool_manager import SegmentPool
from core.export_planner import ExportPlanner
from core.history_repository import HistoryRepository
from core.timebase import sec_to_frames


class MediaScanWorker(QThread):
    progress = Signal(int, int, str)
    scanned = Signal(list, list)
    failed = Signal(str)

    def __init__(self, folder, recursive, media_type, parent=None):
        super().__init__(parent)
        self.folder, self.recursive, self.media_type = folder, recursive, media_type

    def cancel(self):
        self.requestInterruption()

    def run(self):
        try:
            if self.media_type == 'video':
                from core.video_scanner import scan_directory as scan
            else:
                from core.audio_scanner import scan_audio_directory as scan
            valid, invalid = scan(self.folder, self.recursive,
                progress_callback=lambda c,t,n: self.progress.emit(c,t,'Đọc thông tin: '+n),
                cancel_check=self.isInterruptionRequested)
            # Full hashes are warmed once while scanning, away from the GUI.
            for index, info in enumerate(valid):
                if self.isInterruptionRequested():
                    return
                self.progress.emit(index+1, len(valid), 'Chuẩn bị nội dung: '+info.filename)
                compute_media_content_hash(info.path, cancel_check=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.scanned.emit(valid, invalid)
        except InterruptedError:
            pass
        except Exception as error:
            self.failed.emit(str(error))


@dataclass
class PreparedPlan:
    pool: object
    manifest: object
    playlists: list


class PlanWorker(QThread):
    prepared = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, videos, tracks, cut, audio, output, signature, history_path, parent=None):
        super().__init__(parent)
        self.videos, self.tracks = list(videos), list(tracks)
        self.cut, self.audio, self.output = cut, audio, output
        self.signature, self.history_path = signature, history_path

    def check_cancelled(self):
        if self.isInterruptionRequested():
            raise InterruptedError('Preparation cancelled')

    def run(self):
        try:
            self.check_cancelled()
            pool, segments = None, []
            normalized = ConfigAdapterV2.from_v2_cut_config(self.cut, fps=self.output.fps)
            if self.output.export_target != 'audio_only':
                self.progress.emit('Đang chuẩn bị các đoạn video…')
                pool = SegmentPool()
                pool.build(self.videos, self.cut.segment_duration, self.cut.merge_remainder)
                identities = {}
                for info in self.videos:
                    self.check_cancelled()
                    identities[info.path] = 'asset_'+compute_media_content_hash(
                        info.path, cancel_check=self.isInterruptionRequested)
                for seg in pool.master_pool:
                    self.check_cancelled()
                    start, end = sec_to_frames(seg.start_sec, normalized.fps), sec_to_frames(seg.end_sec, normalized.fps)
                    segments.append(CatalogSegment(seg.segment_id, identities[seg.source_video_path],
                        seg.source_video_path, seg.source_video_name, seg.start_sec, seg.end_sec,
                        seg.duration, start, end, max(1,end-start)))
                if self.cut.use_export_history:
                    self.progress.emit('Đang đối chiếu lịch sử xuất…')
                    segments = HistoryRepository(self.history_path).filter_unexported_segments(segments)
                if not segments:
                    raise ValueError('Không còn đoạn video phù hợp. Kiểm tra kho nguồn và tùy chọn lịch sử trong Cài đặt.')
            self.check_cancelled()
            self.progress.emit('Đang sắp xếp kế hoạch ghép…')
            manifest = ExportPlanner(segments, self.tracks, normalized, self.audio, self.output).create_manifest()
            self.check_cancelled()
            playlists = [[AudioPlaylistItem(**item) for item in playlist] for playlist in manifest.audio_playlists]
            self.prepared.emit(PreparedPlan(pool, manifest, playlists))
        except InterruptedError:
            pass
        except Exception as error:
            self.failed.emit(str(error))


class StorageMaintenanceWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def run(self):
        try:
            from core.storage_cleanup import maintain_storage
            self.result.emit(maintain_storage(self.isInterruptionRequested))
        except Exception as error:
            self.failed.emit(str(error))


class CacheWorker(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, directory, clear=False, parent=None):
        super().__init__(parent)
        self.directory, self.clear_requested = directory, clear

    def run(self):
        try:
            count, size, deleted = 0, 0, 0
            directory = Path(self.directory).resolve()
            if directory.exists():
                for entry in directory.iterdir():
                    if self.isInterruptionRequested():
                        return
                    # Only renderer-owned entries; never recurse or follow links.
                    if entry.is_symlink() or not re.fullmatch(r'[0-9a-f]{24}\.mp4(?:\.tmp_\d+_[0-9a-f]+)?', entry.name):
                        continue
                    if not entry.is_file():
                        continue
                    if self.clear_requested:
                        entry.unlink()
                        deleted += 1
                    else:
                        count += 1
                        size += entry.stat().st_size
            self.result.emit({'file_count':count, 'total_bytes':size, 'deleted':deleted})
        except Exception as error:
            self.failed.emit(str(error))
