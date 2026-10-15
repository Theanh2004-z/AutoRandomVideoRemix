"""
Main Window for AutoRandomVideoRemix — refactored for sequential-slice pipeline.

New flow:
  [Quét video] → source_videos
       ↓
  [Tạo kho đoạn] → SegmentPool (slice_all_videos per source)
       ↓
  [Xáo trộn kho] → shuffled draw pool
       ↓
  [Xem trước kế hoạch / Bắt đầu] → BatchPlanner → batch_plans
       ↓
  VideoProcessorWorker → rendered MP4 files
"""
import os
import hashlib
import subprocess
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QScrollArea, QLabel, QMessageBox,
    QApplication
)
try:
    from qfluentwidgets import (
        setTheme, Theme, PushButton, InfoBar,
        InfoBarPosition, isDarkTheme
    )
except ImportError:
    Theme = None
    def setTheme(*args, **kwargs): pass
    def isDarkTheme(): return False
    class InfoBarPosition:
        TOP = "TOP"
        BOTTOM = "BOTTOM"
    class InfoBar:
        @classmethod
        def error(cls, *args, **kwargs): pass
        @classmethod
        def success(cls, *args, **kwargs): pass
        @classmethod
        def warning(cls, *args, **kwargs): pass
        @classmethod
        def info(cls, *args, **kwargs): pass
    class PushButton:
        pass

import uuid
from core.config import VideoInfo, SegmentInfo, CutConfig, OutputConfig, AudioInfo, AudioConfig, AudioPlaylistItem
from core.pool_manager import SegmentPool, BatchPlanner, InsufficientSegmentsError
from core.audio_pool_manager import AudioPool, InsufficientAudioError
from core.audio_scanner import scan_audio_directory
from core.video_processor import VideoProcessorWorker
from core.export_planner import ExportPlanner
from core.media_catalog import CatalogSegment
from core.models import ConfigAdapterV2, compute_media_content_hash
from core.history_repository import HistoryRepository
from core.history_manager import HistoryManager, _make_segment_key
from core.plan_manifest import PlanClip, PlanManifest
from core.timebase import sec_to_frames
from core.job_controller import JobController
from core.output_commit import OutputCommit
from core.output_validator import OutputValidator
from ui.components.source_panel import SourcePanel
from ui.components.audio_panel import AudioPanel
from ui.components.cut_config_panel import CutConfigPanel
from ui.components.output_config_panel import OutputConfigPanel
from ui.components.progress_panel import ProgressPanel
from ui.components.cut_table_view import CutTableView
from ui.styles import GLOBAL_STYLE_SHEET
from ui.workflow import GuidedWorkflow
from core.settings_manager import settings
from core.render_cache import RenderCache
from core.app_paths import get_cache_dir


class MainWindow(QMainWindow):
    """Main Application Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoRandomVideoRemix — Tạo nội dung từ kho của bạn")
        self.resize(1320, 900)
        self.setMinimumSize(1100, 740)
        self.setStyleSheet(GLOBAL_STYLE_SHEET)

        # ── Application state ──────────────────────────────────────────
        self.source_videos: List[VideoInfo] = []
        self.pool: Optional[SegmentPool] = None
        self.current_batch_plans: List[List[SegmentInfo]] = []
        self.audio_pool: Optional[AudioPool] = None
        self.current_audio_playlists: List[List[AudioPlaylistItem]] = []
        self.worker: Optional[VideoProcessorWorker] = None
        self._closing_requested = False
        self._pending_start_export: bool = False
        self._plan_is_stale: bool = True
        self._plan_revision: int = 0

        # ── Theme restoration ──────────────────────────────────────────
        saved_theme = settings.get("theme", "dark")
        if Theme is not None:
            if saved_theme == "light":
                setTheme(Theme.LIGHT)
            else:
                setTheme(Theme.DARK)

        self._init_ui()
        self._connect_signals()
        self.workflow.bind()

        # Schedule automatic restoration of source directories on startup
        QTimer.singleShot(250, self._auto_restore_sessions)

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _init_ui(self):
        self.workflow = GuidedWorkflow(self)
        self.setCentralWidget(self.workflow)
        if Theme is not None:
            self.workflow.apply_theme(isDarkTheme())

    def _toggle_theme(self):
        new_is_dark = not isDarkTheme()
        if Theme is not None:
            setTheme(Theme.DARK if new_is_dark else Theme.LIGHT)
        settings.set("theme", "dark" if new_is_dark else "light")
        self.workflow.apply_theme(new_is_dark)

    def _auto_restore_sessions(self):
        """Restores video and audio sources if previously configured folders exist."""
        vid_dir = settings.get("video_folder", "").strip()
        if vid_dir and os.path.isdir(vid_dir) and not self.source_videos:
            self.progress_panel.log("INFO", f"📂 Tự động khôi phục video từ: {vid_dir}")
            self.source_panel._start_scan()

        aud_dir = settings.get("audio_folder", "").strip()
        if aud_dir and os.path.isdir(aud_dir) and (not self.audio_pool or self.audio_pool.total_tracks == 0):
            self.progress_panel.log("INFO", f"🎵 Tự động khôi phục âm thanh từ: {aud_dir}")
            self.audio_panel._start_scan()


    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self):
        self.source_panel.videos_scanned.connect(self._on_source_scanned)
        self.audio_panel.audios_scanned.connect(self._on_audios_scanned)
        self.audio_panel.config_changed.connect(self._on_audio_config_changed)
        self.cut_panel.config_changed.connect(self._on_cut_config_changed)

        self.progress_panel.build_pool_requested.connect(self._on_build_pool)
        self.progress_panel.shuffle_pool_requested.connect(self._on_shuffle_pool)
        self.progress_panel.audio_playlist_requested.connect(self._on_generate_audio_playlist)
        self.progress_panel.preview_requested.connect(self._on_preview_plan)
        self.progress_panel.start_requested.connect(self._on_start_rendering)
        self.progress_panel.pause_requested.connect(self._on_pause_rendering)
        self.progress_panel.resume_requested.connect(self._on_resume_rendering)
        self.progress_panel.cancel_requested.connect(self._on_cancel_rendering)
        self._on_audio_config_changed()

    # ------------------------------------------------------------------
    # Pipeline handlers
    # ------------------------------------------------------------------

    def _on_source_scanned(self, valid_videos: List[VideoInfo]):
        self.source_videos = valid_videos
        self.pool = None
        self.current_batch_plans = []
        self._plan_is_stale = True
        self._plan_revision += 1
        self.table_view.set_plans([])
        self.progress_panel.log("INFO", f"Đã nạp {len(valid_videos)} video nguồn hợp lệ.")
        self.cut_panel.lbl_pool_info.setText("Kho đoạn: Chưa tạo")
        self.cut_panel.lbl_pool_info.setStyleSheet(
            "color: #555; font-style: italic; font-size: 12px; margin-top: 4px;"
        )
        # Update estimated segment count card
        cut_cfg = self.cut_panel.get_config()
        self.source_panel.update_estimated_segments(cut_cfg.segment_duration, cut_cfg.merge_remainder)

        # Auto-resume export if pending
        if self._pending_start_export:
            export_target = self.audio_panel.get_export_target()
            # If also waiting for audio scanner, let audio scanner finish first
            if export_target in ("video_with_random_audio", "audio_only"):
                if self.audio_panel.scanner_thread and self.audio_panel.scanner_thread.isRunning():
                    self.progress_panel.log("INFO", "✓ Đã xong video nguồn. Đang chờ quét nốt kho âm thanh...")
                    return
            QTimer.singleShot(150, self._on_start_rendering)

    def _on_audios_scanned(self, valid_audios: List[AudioInfo]):
        self.audio_pool = AudioPool(valid_audios)
        self.current_audio_playlists = []
        self._plan_is_stale = True
        self._plan_revision += 1
        self.progress_panel.log(
            "INFO",
            f"Đã nạp {len(valid_audios)} file âm thanh hợp lệ ({self.audio_pool.total_duration:.1f}s)."
        )

        # Auto-resume export if pending
        if self._pending_start_export:
            export_target = self.audio_panel.get_export_target()
            # If also waiting for video scanner, let video scanner finish first
            if export_target != "audio_only":
                if self.source_panel.scanner_thread and self.source_panel.scanner_thread.isRunning():
                    self.progress_panel.log("INFO", "✓ Đã xong kho âm thanh. Đang chờ quét nốt kho video nguồn...")
                    return
            QTimer.singleShot(150, self._on_start_rendering)

    def _on_audio_config_changed(self):
        self._plan_is_stale = True
        self._plan_revision += 1
        target = self.audio_panel.get_export_target()
        self.output_panel.update_audio_mode_display(target)
        if target == "audio_only":
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT ÂM THANH ĐỘC LẬP")
        elif target == "video_only":
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT VIDEO (CẤU HÌNH KHU C)")
        else:
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT (VIDEO + NHẠC NGẪU NHIÊN)")

    def _on_cut_config_changed(self):
        self._plan_is_stale = True
        self._plan_revision += 1
        self.current_batch_plans = []
        self.table_view.mark_plan_stale()
        cut_cfg = self.cut_panel.get_config()
        self.source_panel.update_estimated_segments(cut_cfg.segment_duration, cut_cfg.merge_remainder)

    def _generate_audio_playlists(self, batch_count: int = 1) -> Optional[List[List[AudioPlaylistItem]]]:
        """Stage 2–3 (Phần 2): Pick audio tracks for K batch outputs using V3 AudioPlanner."""
        if not self.audio_pool or self.audio_pool.total_tracks == 0:
            if self.audio_panel.valid_audios:
                self.audio_pool = AudioPool(self.audio_panel.valid_audios)
            else:
                audio_dir = self.audio_panel.folder_edit.text().strip()
                if audio_dir and os.path.isdir(audio_dir):
                    self.progress_panel.log("INFO", "Đang quét kho âm thanh...")
                    valid_audios, _ = scan_audio_directory(
                        audio_dir, recursive=self.audio_panel.chk_recursive.isChecked()
                    )
                    if valid_audios:
                        self.audio_panel.valid_audios = valid_audios
                        self.audio_pool = AudioPool(valid_audios)

        if not self.audio_pool or self.audio_pool.total_tracks == 0:
            InfoBar.warning(
                title="Chưa có kho âm thanh",
                content="Vui lòng chọn thư mục và quét file âm thanh trước!",
                parent=self,
                position=InfoBarPosition.TOP
            )
            return None

        audio_cfg = self.audio_panel.get_config()
        cut_cfg = self.cut_panel.get_config()
        target_dur = cut_cfg.target_total_duration

        try:
            from core.audio_planner import AudioPlanner
            planner = AudioPlanner(self.audio_pool.tracks, audio_cfg)
            playlists = planner.plan_batch(batch_count=batch_count, target_duration=target_dur)
            self.current_audio_playlists = playlists
            if playlists:
                self.table_view.set_audio_playlists(playlists)
            self._plan_is_stale = False
            tot_tracks = sum(len(p) for p in playlists)
            self.progress_panel.log(
                "SUCCESS",
                f"✓ Đã lập {len(playlists)} playlist âm thanh ({tot_tracks} lượt bài)."
            )
            InfoBar.success(
                title="Tạo playlist nhạc thành công",
                content=f"{len(playlists)} playlist âm thanh ngẫu nhiên ({tot_tracks} lượt bài).",
                parent=self,
                position=InfoBarPosition.TOP
            )
            return playlists
        except InsufficientAudioError as e:
            self.progress_panel.log("ERROR", str(e))
            InfoBar.error(title="Lỗi kho âm thanh", content=str(e), parent=self, position=InfoBarPosition.TOP)
            return None
        except Exception as e:
            self.progress_panel.log("ERROR", f"Lỗi tạo playlist âm thanh: {e}")
            return None

    def _on_generate_audio_playlist(self) -> Optional[List[AudioPlaylistItem]]:
        """Slot for manual audio playlist button."""
        cut_cfg = self.cut_panel.get_config()
        playlists = self._generate_audio_playlists(batch_count=cut_cfg.batch_count)
        return playlists[0] if playlists else None

    def _on_build_pool(self):
        """Stage 2–3: Slice all videos → build pool."""
        self._plan_is_stale = True
        self.current_batch_plans = []
        self.table_view.mark_plan_stale()

        if not self.source_videos:
            video_dir = self.source_panel.folder_edit.text().strip()
            if video_dir and os.path.isdir(video_dir):
                self.progress_panel.log("INFO", f"🔍 Đang tự động quét kho video nguồn từ: {video_dir}...")
                self.source_panel._start_scan()
                return
            InfoBar.error(
                title="Chưa có video nguồn",
                content="Vui lòng quét thư mục video nguồn trước!",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        cut_cfg = self.cut_panel.get_config()
        self.progress_panel.log(
            "INFO",
            f"Đang chia {len(self.source_videos)} video thành các đoạn "
            f"{cut_cfg.segment_duration:.0f}s (merge_remainder={cut_cfg.merge_remainder})..."
        )

        try:
            pool = SegmentPool()
            pool.build(
                videos=self.source_videos,
                segment_duration=cut_cfg.segment_duration,
                merge_remainder=cut_cfg.merge_remainder,
            )
            self.pool = pool

            # Show pool in table tab
            self.table_view.set_pool(pool.draw_pool)

            # Update cut panel info label
            self.cut_panel.update_pool_info(pool.total_segments, pool.total_duration)

            self.progress_panel.log(
                "SUCCESS",
                f"✓ Kho đoạn đã tạo: {pool.total_segments} đoạn "
                f"| Tổng: {pool.total_duration:.1f}s\n{pool.stats_summary()}"
            )
            InfoBar.success(
                title="Tạo kho đoạn thành công",
                content=f"{pool.total_segments} đoạn từ {len(self.source_videos)} video nguồn.",
                parent=self,
                position=InfoBarPosition.TOP,
            )

            # Invalidate existing plans since pool changed
            self.current_batch_plans = []
            self._plan_is_stale = True
            self.table_view.mark_plan_stale()

        except Exception as e:
            self.progress_panel.log("ERROR", f"Lỗi tạo kho đoạn: {e}")
            InfoBar.error(title="Lỗi", content=str(e), parent=self, position=InfoBarPosition.TOP)

    def _on_shuffle_pool(self):
        """Stage 4: Shuffle the pool."""
        if not self.pool:
            # Auto-build first
            self._on_build_pool()
            if not self.pool:
                return

        self.pool.shuffle()
        self.table_view.set_pool(self.pool.draw_pool)
        self.current_batch_plans = []  # Plans from old order are invalid
        self._plan_is_stale = True
        self.table_view.mark_plan_stale()
        self.progress_panel.log(
            "SUCCESS",
            f"🔀 Đã xáo trộn kho {self.pool.total_segments} đoạn ngẫu nhiên."
        )
        InfoBar.success(
            title="Xáo trộn hoàn tất",
            content=f"Kho {self.pool.total_segments} đoạn đã được xáo trộn.",
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _on_preview_plan(self):
        """Stage 5: Pick segments → build batch plans."""
        if not self.pool:
            self._on_shuffle_pool()
            if not self.pool:
                return

        cut_cfg = self.cut_panel.get_config()
        if cut_cfg.target_total_duration <= 0:
            InfoBar.warning(
                title="Thời lượng không hợp lệ",
                content="Tổng thời lượng video xuất phải lớn hơn 0 giây!",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        self.progress_panel.log("INFO", "Đang lập kế hoạch ghép từ kho đã xáo trộn...")
        plans = self._build_plans(cut_cfg)
        if plans:
            total_segs = sum(len(p) for p in plans)
            self.progress_panel.log(
                "SUCCESS",
                f"✓ Kế hoạch: {len(plans)} video | {total_segs} đoạn sẽ ghép."
            )
            InfoBar.success(
                title="Lập kế hoạch thành công",
                content=f"{len(plans)} video đầu ra với {total_segs} đoạn ghép.",
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _build_plans(self, cut_cfg: CutConfig) -> Optional[List[List[SegmentInfo]]]:
        """Internal: run BatchPlanner and populate table. Returns plans or None on error."""
        if not self.pool or self.pool.total_segments == 0:
            InfoBar.warning(
                title="Chưa có kho đoạn",
                content="Kho đoạn video trống! Vui lòng nhấn 'Tạo kho video' trước.",
                parent=self,
                position=InfoBarPosition.TOP
            )
            return None

        # Friendly check if total pool duration is less than needed without reuse
        total_pool_dur = self.pool.total_duration
        target_dur = cut_cfg.target_total_duration
        batch = cut_cfg.batch_count

        if total_pool_dur < target_dur and not cut_cfg.allow_reuse:
            m_pool, s_pool = divmod(int(total_pool_dur), 60)
            m_tgt, s_tgt = divmod(int(target_dur), 60)
            reply = QMessageBox.question(
                self,
                "Kho video nguồn ngắn hơn thời lượng xuất",
                f"Kho video nguồn hiện có {self.pool.total_segments} đoạn (tổng {m_pool}m {s_pool}s), "
                f"ngắn hơn thời lượng video bạn muốn xuất ({m_tgt}m {s_tgt:02d}s).\n\n"
                f"👉 Bạn có muốn tự động BẬT tùy chọn 'Bốc có hoàn lại' (tái sử dụng và xáo trộn lại các đoạn video) để xuất đủ thời lượng không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.cut_panel.chk_allow_reuse.setChecked(True)
                if batch > 1:
                    self.cut_panel.chk_no_repeat_batch.setChecked(False)
                cut_cfg = self.cut_panel.get_config()
            else:
                return None

    def _convert_segments_to_catalog(self, segments: List[SegmentInfo], fps: float = 30.0) -> List[CatalogSegment]:
        res = []
        path_to_asset_id = {}
        for s in segments:
            start_f = sec_to_frames(s.start_sec, fps)
            end_f = sec_to_frames(s.end_sec, fps)
            frames = max(1, end_f - start_f)
            src_path = s.source_video_path or s.source_video_name
            if src_path not in path_to_asset_id:
                if os.path.isfile(src_path):
                    path_to_asset_id[src_path] = f"asset_{compute_media_content_hash(src_path)}"
                else:
                    path_to_asset_id[src_path] = f"asset_{hashlib.sha256(src_path.encode('utf-8')).hexdigest()[:12]}"
            aid = path_to_asset_id[src_path]
            res.append(
                CatalogSegment(
                    segment_id=s.segment_id,
                    asset_id=aid,
                    source_path=s.source_video_path,
                    source_filename=s.source_video_name,
                    start_sec=s.start_sec,
                    end_sec=s.end_sec,
                    duration_sec=s.duration,
                    start_frame=start_f,
                    end_frame=end_f,
                    frame_count=frames
                )
            )
        return res

    def _build_plans(self, cut_cfg: Optional[CutConfig] = None):
        """Builds reproducible plans via V3 ExportPlanner."""
        if cut_cfg is None:
            cut_cfg = self.cut_panel.get_config()

        if not self.pool or self.pool.total_segments == 0:
            return None

        # Reset used state in pool
        self.pool.reset_used()

        v3_cut_cfg = ConfigAdapterV2.from_v2_cut_config(cut_cfg, fps=self.output_panel.get_config().fps)

        # 1. Convert master pool segments to catalog segments with content-based asset_id (V01, V03)
        all_catalog_segs = self._convert_segments_to_catalog(self.pool.master_pool, fps=v3_cut_cfg.fps)

        # 2. Filter unexported segments via SQLite interval overlap query (V02, V03)
        if cut_cfg.use_export_history:
            history_repo = HistoryRepository()
            catalog_segs = history_repo.filter_unexported_segments(all_catalog_segs)
        else:
            catalog_segs = all_catalog_segs

        if not catalog_segs:
            self.progress_panel.log("ERROR", "Tất cả các đoạn video trong kho đều đã xuất trước đó.")
            return None

        audio_tracks = self.audio_pool.tracks if (hasattr(self, "audio_pool") and self.audio_pool) else []
        audio_cfg = self.audio_panel.get_config()
        out_cfg = self.output_panel.get_config()
        out_cfg.export_target = self.audio_panel.get_export_target()

        planner = ExportPlanner(
            video_segments=catalog_segs,
            audio_tracks=audio_tracks,
            cut_config=v3_cut_cfg,
            audio_config=audio_cfg,
            output_config=out_cfg
        )

        try:
            manifest = planner.create_manifest()
            self.current_manifest = manifest
            plans = manifest.video_batches
            self.current_batch_plans = plans
            self._plan_is_stale = False
            self.table_view.set_plans(plans)
            self.table_view.refresh_pool_used()
            return plans
        except InsufficientSegmentsError as e:
            self.progress_panel.log("ERROR", str(e))
            reply = QMessageBox.question(
                self,
                "Không đủ đoạn video",
                f"{e}\n\n👉 Bạn có muốn tự động BẬT chế độ 'Bốc có hoàn lại' để tiếp tục không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.cut_panel.chk_allow_reuse.setChecked(True)
                if cut_cfg.batch_count > 1:
                    self.cut_panel.chk_no_repeat_batch.setChecked(False)
                cut_cfg = self.cut_panel.get_config()
                v3_cut_cfg = ConfigAdapterV2.from_v2_cut_config(cut_cfg, fps=self.output_panel.get_config().fps)
                planner = ExportPlanner(
                    video_segments=catalog_segs,
                    audio_tracks=audio_tracks,
                    cut_config=v3_cut_cfg,
                    audio_config=audio_cfg,
                    output_config=out_cfg
                )
                try:
                    manifest = planner.create_manifest()
                    self.current_manifest = manifest
                    plans = manifest.video_batches
                    self.current_batch_plans = plans
                    self._plan_is_stale = False
                    self.table_view.set_plans(plans)
                    self.table_view.refresh_pool_used()
                    return plans
                except Exception as retry_err:
                    self.progress_panel.log("ERROR", f"Lỗi lập kế hoạch: {retry_err}")
                    return None
            return None
        except Exception as e:
            self.progress_panel.log("ERROR", f"Lỗi lập kế hoạch: {e}")
            InfoBar.error(
                title="Lỗi lập kế hoạch",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return None

    def _connect_worker_signals(self):
        if not self.worker:
            return
        self.worker.batch_started.connect(self._on_worker_batch_started)
        self.worker.video_started.connect(self._on_worker_video_started)
        self.worker.segment_progress.connect(self._on_worker_segment_progress)
        self.worker.overall_progress.connect(self._on_worker_overall_progress)
        self.worker.log_emitted.connect(self._on_worker_log)
        self.worker.video_completed.connect(self._on_worker_video_completed)
        self.worker.batch_finished.connect(self._on_worker_batch_finished)
        self.worker.error_occurred.connect(self._on_worker_error)
        if hasattr(self, "workflow"):
            self.workflow.attach_worker(self.worker)

    def _on_start_rendering(self):
        """Stage 6–7: Render all planned videos / audio with 1-Click Auto-Preparation."""
        if self.worker and self.worker.isRunning():
            return
        if hasattr(self, "workflow") and not self.workflow.validated():
            self.workflow.go_to(2)
            self.progress_panel.log("WARNING", "Cần kiểm tra và lập kế hoạch trước khi xuất.")
            return
        history_repo = None
        job_id = None
        try:
            self.render_cache = RenderCache(
                cache_dir=settings.get('cache_folder','') or get_cache_dir(),
                max_size_bytes=int(settings.get('cache_max_gb',5))*1024**3,
                enabled=bool(settings.get('cache_enabled',True)))
            export_target = self.audio_panel.get_export_target()
            out_cfg = self.output_panel.get_config()
            out_cfg.export_target = export_target

            if not out_cfg.output_dir:
                out_cfg.output_dir = os.path.join(os.path.expanduser("~"), "Videos", "RemixOutput")
            os.makedirs(out_cfg.output_dir, exist_ok=True)

            cut_cfg = self.cut_panel.get_config()
            audio_cfg = self.audio_panel.get_config()
            out_cfg.audio_output_format = audio_cfg.audio_output_format

            # -------------------------------------------------------------
            # STEP 1: AUTO-PREPARE VIDEO SOURCES (if not audio-only)
            # -------------------------------------------------------------
            if export_target != "audio_only":
                # Check if background scanner thread is currently running
                if self.source_panel.scanner_thread and self.source_panel.scanner_thread.isRunning():
                    self._pending_start_export = True
                    self.progress_panel.log("INFO", "⏳ Kho video nguồn đang được quét tự động, hệ thống sẽ tự động bắt đầu xuất ngay khi quét xong...")
                    self.progress_panel.update_overall_progress(0, "Đang chờ quét video nguồn hoàn tất...")
                    InfoBar.info(
                        title="Đang quét video nguồn",
                        content="Hệ thống đang quét kho video nguồn, sẽ tự động bắt đầu xuất ngay khi quét xong!",
                        parent=self,
                        position=InfoBarPosition.TOP
                    )
                    return

                # If no valid source videos loaded yet
                if not self.source_videos:
                    video_dir = self.source_panel.folder_edit.text().strip()
                    if video_dir and os.path.isdir(video_dir):
                        self._pending_start_export = True
                        self.progress_panel.log("INFO", f"🔍 Đang tự động quét kho video nguồn từ: {video_dir}...")
                        self.progress_panel.update_overall_progress(0, "Đang tự động quét video nguồn...")
                        self.source_panel._start_scan()
                        return
                    else:
                        InfoBar.error(
                            title="Chưa có video nguồn",
                            content="Vui lòng chọn thư mục chứa video nguồn hợp lệ tại Khu vực A trước khi xuất!",
                            parent=self,
                            position=InfoBarPosition.TOP
                        )
                        self.progress_panel.log("ERROR", "Chưa có thư mục video nguồn hợp lệ. Vui lòng chọn tại Khu vực A!")
                        return

            # -------------------------------------------------------------
            # STEP 2: AUTO-PREPARE AUDIO SOURCES (if needed)
            # -------------------------------------------------------------
            if export_target in ("video_with_random_audio", "audio_only"):
                # Check if background audio scanner is currently running
                if self.audio_panel.scanner_thread and self.audio_panel.scanner_thread.isRunning():
                    self._pending_start_export = True
                    self.progress_panel.log("INFO", "⏳ Kho âm thanh đang được quét tự động, hệ thống sẽ tự động bắt đầu xuất ngay khi quét xong...")
                    self.progress_panel.update_overall_progress(0, "Đang chờ quét âm thanh hoàn tất...")
                    return

                # Check if audio pool is loaded
                if not self.audio_pool or self.audio_pool.total_tracks == 0:
                    if self.audio_panel.valid_audios:
                        self.audio_pool = AudioPool(self.audio_panel.valid_audios)
                    else:
                        audio_dir = self.audio_panel.folder_edit.text().strip()
                        if audio_dir and os.path.isdir(audio_dir):
                            self._pending_start_export = True
                            self.progress_panel.log("INFO", f"🎵 Đang tự động quét kho âm thanh từ: {audio_dir}...")
                            self.progress_panel.update_overall_progress(0, "Đang tự động quét kho âm thanh...")
                            self.audio_panel._start_scan()
                            return
                        else:
                            InfoBar.error(
                                title="Chưa có kho âm thanh",
                                content="Vui lòng chọn thư mục chứa file âm thanh hợp lệ trước khi xuất!",
                                parent=self,
                                position=InfoBarPosition.TOP
                            )
                            self.progress_panel.log("ERROR", "Chưa có thư mục âm thanh hợp lệ. Vui lòng chọn tại Phần 2!")
                            return

            # Reset pending flag once all sources are confirmed ready
            self._pending_start_export = False

            # -------------------------------------------------------------
            # PRE-FLIGHT: DISK SPACE ESTIMATION
            # -------------------------------------------------------------
            from core.disk_estimator import DiskEstimator
            tot_dur = cut_cfg.target_total_duration * cut_cfg.batch_count
            w, h = out_cfg.width_height
            is_audio = (export_target == "audio_only")
            req_bytes = DiskEstimator.estimate_required_bytes(
                total_duration_sec=tot_dur,
                width=w,
                height=h,
                is_audio_only=is_audio
            )
            disk_check = DiskEstimator.check_disk_space(out_cfg.output_dir, req_bytes)
            if not disk_check.is_sufficient:
                self.progress_panel.log("WARNING", disk_check.message)
                reply = QMessageBox.warning(
                    self,
                    "Cảnh báo dung lượng ổ đĩa",
                    f"{disk_check.message}\n\nBạn có chắc muốn tiếp tục xuất không?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                if reply != QMessageBox.Yes:
                    self.progress_panel.set_running_state(False)
                    return
            else:
                self.progress_panel.log("INFO", f"💾 Kiểm tra ổ đĩa: Cần ~{disk_check.formatted_required}, trống {disk_check.formatted_available}.")

            # -------------------------------------------------------------
            # CASE 1: STANDALONE AUDIO EXPORT
            # -------------------------------------------------------------
            if export_target == "audio_only":
                batch_count = cut_cfg.batch_count
                if self._plan_is_stale or len(self.current_audio_playlists) != batch_count or not self.current_audio_playlists:
                    self.progress_panel.log("INFO", f"🎵 Đang tự động lập kế hoạch {batch_count} playlist âm thanh...")
                    playlists = self._generate_audio_playlists(batch_count=batch_count)
                    if not playlists:
                        self.progress_panel.log("ERROR", "Không thể tạo đủ playlist âm thanh để xuất.")
                        return
                else:
                    playlists = self.current_audio_playlists

                self.progress_panel.set_running_state(True)
                self.worker = VideoProcessorWorker(
                    batch_plans=[],
                    output_config=out_cfg,
                    audio_playlists=playlists,
                    record_history=False,
                    parent=self
                )
                self._connect_worker_signals()
                self.worker.start()
                return

            # -------------------------------------------------------------
            # CASE 2: VIDEO OR VIDEO + RANDOM AUDIO COMBINED
            # -------------------------------------------------------------
            # Auto-build and shuffle pool if not created yet
            if not self.pool or self.pool.total_segments == 0:
                self.progress_panel.log("INFO", "📦 Đang tự động chia đoạn và tạo kho video...")
                self._on_build_pool()
                if not self.pool or self.pool.total_segments == 0:
                    self.progress_panel.log("ERROR", "Tạo kho đoạn thất bại.")
                    return
                self.pool.shuffle()
                self.table_view.set_pool(self.pool.draw_pool)

            # Auto-plan if plans not generated yet or stale
            plans = self.current_batch_plans
            if not plans or self._plan_is_stale or not hasattr(self, "current_manifest"):
                self.progress_panel.log("INFO", "📋 Đang tự động lập kế hoạch bốc đoạn ngẫu nhiên mới nhất bằng ExportPlanner (V3)...")
                plans = self._build_plans(cut_cfg)
                if not plans:
                    self.progress_panel.log("ERROR", "Lập kế hoạch ghép video thất bại.")
                    return
                self._plan_is_stale = False

            manifest = getattr(self, "current_manifest", None)

            # V3 Pre-export reservation lock (R01, R04, V02)
            history_repo = None
            job_id = None
            if cut_cfg.use_export_history:
                history_repo = HistoryRepository()
                job_id = f"job_{uuid.uuid4().hex[:8]}"
                all_clips = [c for p in plans for c in p]
                if not history_repo.reserve_batch(job_id, all_clips):
                    self.progress_panel.log("ERROR", "Không thể giữ chỗ: Các đoạn trong kế hoạch trùng lặp với lịch sử xuất.")
                    return

            audio_playlists_to_send = []
            if export_target == "video_with_random_audio":
                out_cfg.audio_mode = "random_audio_pool"
                if manifest and manifest.audio_playlists:
                    audio_playlists_to_send = [
                        [
                            AudioPlaylistItem(
                                item_id=it.get("item_id", uuid.uuid4().hex[:12]),
                                source_audio_path=it.get("source_audio_path", ""),
                                source_audio_name=it.get("source_audio_name", ""),
                                duration=float(it.get("duration", 0.0)),
                                effective_duration=float(it.get("effective_duration", 0.0)),
                                trim_end_sec=it.get("trim_end_sec"),
                                cycle_index=int(it.get("cycle_index", 1)),
                                playlist_position=int(it.get("playlist_position", 1))
                            )
                            for it in pl
                        ]
                        for pl in manifest.audio_playlists
                    ]
                else:
                    audio_playlists_to_send = self._generate_audio_playlists(batch_count=len(plans)) or []

                self.current_audio_playlists = audio_playlists_to_send
                if audio_playlists_to_send:
                    self.table_view.set_audio_playlists(audio_playlists_to_send)
                    self.progress_panel.log(
                        "INFO",
                        f"✓ Đã chuẩn bị {len(audio_playlists_to_send[0])} bài nhạc ngẫu nhiên cho video bằng ExportPlanner (V3)."
                    )
            else:
                out_cfg.audio_mode = self.output_panel.get_config().audio_mode

            self.progress_panel.set_running_state(True)
            self.worker = VideoProcessorWorker(
                batch_plans=plans,
                output_config=out_cfg,
                audio_playlists=audio_playlists_to_send,
                record_history=cut_cfg.use_export_history,
                render_cache=getattr(self, "render_cache", None),
                job_id=job_id,
                history_repo=history_repo,
                source_metadata={video.path:video for video in self.source_videos},
                parent=self
            )
            self._connect_worker_signals()
            self.worker.start()

        except Exception as e:
            if history_repo and job_id:
                try:
                    history_repo.release_reservations(job_id)
                except Exception:
                    pass
            self.progress_panel.set_running_state(False)
            self.progress_panel.log("ERROR", f"Lỗi khởi chạy xuất video: {e}")
            InfoBar.error(
                title="Lỗi xuất",
                content=str(e),
                parent=self,
                position=InfoBarPosition.TOP
            )

    def _on_pause_rendering(self):
        if self.worker:
            self.worker.pause()

    def _on_resume_rendering(self):
        if self.worker:
            self.worker.resume()

    def _on_cancel_rendering(self):
        if self.worker:
            self.worker.cancel()

    # ------------------------------------------------------------------
    # Worker signal handlers
    # ------------------------------------------------------------------

    def _on_worker_batch_started(self, total_videos: int):
        self.progress_panel.update_stats(0, total_videos, 0, 0)
        self.progress_panel.log("INFO", f"Khởi động render: {total_videos} video đầu ra...")

    def _on_worker_video_started(self, cur_idx: int, total_idx: int, out_path: str):
        total_segs = (
            len(self.current_batch_plans[cur_idx - 1])
            if cur_idx <= len(self.current_batch_plans) else 0
        )
        self.progress_panel.update_stats(cur_idx, total_idx, 0, total_segs)

    def _on_worker_segment_progress(self, cur_seg: int, total_segs: int, seg: SegmentInfo):
        cur_v = seg.target_video_index
        total_v = len(self.current_batch_plans)
        self.progress_panel.update_stats(cur_v, total_v, cur_seg, total_segs)
        self.table_view.update_clip_status(cur_v, seg.plan_position, seg.status)

    def _on_worker_overall_progress(self, pct: float, msg: str):
        self.progress_panel.update_overall_progress(pct, msg)

    def _on_worker_log(self, level: str, msg: str):
        self.progress_panel.log(level, msg)

    def _on_worker_video_completed(self, video_idx: int, out_path: str):
        self.progress_panel.log(
            "SUCCESS",
            f"✓ Video #{video_idx} xuất thành công → {os.path.basename(out_path)}"
        )

    def _on_worker_batch_finished(self, success_cnt: int, total_cnt: int):
        self.progress_panel.set_running_state(False)
        out_cfg = self.output_panel.get_config()

        if success_cnt == total_cnt and total_cnt > 0:
            self.progress_panel.log(
                "SUCCESS",
                f"🎉 HOÀN TẤT! Đã xuất thành công {success_cnt}/{total_cnt} video."
            )
            InfoBar.success(
                title="Tạo video hoàn tất!",
                content=f"Đã xuất {success_cnt}/{total_cnt} video vào: {out_cfg.output_dir}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
        else:
            self.progress_panel.log(
                "WARNING",
                f"Quá trình kết thúc với {success_cnt}/{total_cnt} video thành công."
            )
            InfoBar.warning(
                title="Hoàn tất (có lỗi)",
                content=f"Hoàn thành {success_cnt}/{total_cnt} video.",
                parent=self,
                position=InfoBarPosition.TOP,
            )

    def _on_worker_error(self, err_msg: str):
        self.progress_panel.log("ERROR", err_msg)
        InfoBar.error(
            title="Lỗi xử lý",
            content=err_msg,
            parent=self,
            position=InfoBarPosition.TOP,
        )

    # ------------------------------------------------------------------
    # Window events
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        rendering = bool(self.worker and self.worker.isRunning())
        if rendering and not self._closing_requested:
            reply = QMessageBox.question(self,"Đóng ứng dụng",
                "Đang xuất nội dung. Hủy công việc và đóng ứng dụng?",
                QMessageBox.Yes | QMessageBox.No,QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self._closing_requested = True
        self.workflow.stop_background()
        if rendering:
            self.worker.cancel()
        if rendering or self.workflow.background_running():
            event.ignore()
            self.workflow.state_label.setText("Đang dừng tác vụ…")
            QTimer.singleShot(100,self.close)
            return
        self.workflow.settings_dialog.close()
        event.accept()
