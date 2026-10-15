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
import subprocess
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSplitter, QScrollArea, QLabel, QMessageBox,
    QApplication
)
from qfluentwidgets import (
    setTheme, Theme, PushButton, InfoBar,
    InfoBarPosition, isDarkTheme
)

from core.config import VideoInfo, SegmentInfo, CutConfig, OutputConfig, AudioInfo, AudioConfig, AudioPlaylistItem
from core.pool_manager import SegmentPool, BatchPlanner, InsufficientSegmentsError
from core.audio_pool_manager import AudioPool, AudioPicker, InsufficientAudioError
from core.audio_scanner import scan_audio_directory
from core.video_processor import VideoProcessorWorker
from ui.components.source_panel import SourcePanel
from ui.components.audio_panel import AudioPanel
from ui.components.cut_config_panel import CutConfigPanel
from ui.components.output_config_panel import OutputConfigPanel
from ui.components.progress_panel import ProgressPanel
from ui.components.cut_table_view import CutTableView
from ui.styles import GLOBAL_STYLE_SHEET


class MainWindow(QMainWindow):
    """Main Application Window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("TOOL TỰ ĐỘNG CHIA & GHÉP VIDEO + ÂM THANH NGẪU NHIÊN - v2.5")
        self.resize(1300, 880)
        self.setMinimumSize(1024, 700)
        self.setStyleSheet(GLOBAL_STYLE_SHEET)

        # ── Application state ──────────────────────────────────────────
        self.source_videos: List[VideoInfo] = []
        self.pool: Optional[SegmentPool] = None
        self.current_batch_plans: List[List[SegmentInfo]] = []
        self.audio_pool: Optional[AudioPool] = None
        self.current_audio_playlists: List[List[AudioPlaylistItem]] = []
        self.worker: Optional[VideoProcessorWorker] = None
        self._pending_start_export: bool = False

        self._init_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _init_ui(self):
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_vbox = QVBoxLayout(central_widget)
        main_vbox.setContentsMargins(16, 12, 16, 16)
        main_vbox.setSpacing(12)

        # Top header bar
        header_bar = QHBoxLayout()
        header_title = QLabel("🎬 TOOL TỰ ĐỘNG CHIA & GHÉP VIDEO NGẪU NHIÊN")
        header_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #0078D4;")

        header_sub = QLabel("— Chia tuần tự → Kho đoạn → Xáo trộn → Bốc ngẫu nhiên → Ghép MP4")
        header_sub.setStyleSheet("font-size: 12px; color: #777;")

        header_bar.addWidget(header_title)
        header_bar.addWidget(header_sub)
        header_bar.addStretch()

        self.btn_theme = PushButton("🌓 Đổi Giao Diện", self)
        self.btn_theme.clicked.connect(self._toggle_theme)
        header_bar.addWidget(self.btn_theme)
        main_vbox.addLayout(header_bar)

        # Main splitter (Left: Config & Source / Right: Monitor & Table)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)

        # Left column: scroll area for config panels
        left_scroll = QScrollArea(self)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.NoFrame)

        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(14)

        self.source_panel = SourcePanel(left_container)
        left_layout.addWidget(self.source_panel)

        self.audio_panel = AudioPanel(left_container)
        left_layout.addWidget(self.audio_panel)

        self.cut_panel = CutConfigPanel(left_container)
        left_layout.addWidget(self.cut_panel)

        self.output_panel = OutputConfigPanel(left_container)
        left_layout.addWidget(self.output_panel)

        left_layout.addStretch()
        left_scroll.setWidget(left_container)
        splitter.addWidget(left_scroll)

        # Right column: progress + table
        right_container = QWidget(self)
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(8, 0, 0, 0)
        right_layout.setSpacing(14)

        self.progress_panel = ProgressPanel(right_container)
        right_layout.addWidget(self.progress_panel)

        self.table_view = CutTableView(right_container)
        right_layout.addWidget(self.table_view, 1)

        splitter.addWidget(right_container)
        splitter.setSizes([560, 680])
        main_vbox.addWidget(splitter, 1)

    def _toggle_theme(self):
        setTheme(Theme.LIGHT if isDarkTheme() else Theme.DARK)

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
        target = self.audio_panel.get_export_target()
        self.output_panel.update_audio_mode_display(target)
        if target == "audio_only":
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT ÂM THANH ĐỘC LẬP")
        elif target == "video_only":
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT VIDEO (CẤU HÌNH KHU C)")
        else:
            self.progress_panel.btn_start.setText("BẮT ĐẦU XUẤT (VIDEO + NHẠC NGẪU NHIÊN)")

    def _on_cut_config_changed(self):
        cut_cfg = self.cut_panel.get_config()
        self.source_panel.update_estimated_segments(cut_cfg.segment_duration, cut_cfg.merge_remainder)

    def _on_generate_audio_playlist(self) -> Optional[List[AudioPlaylistItem]]:
        """Stage 2–3 (Phần 2): Pick audio tracks cycle by cycle."""
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
            picker = AudioPicker(self.audio_pool, audio_cfg)
            playlist = picker.pick_playlist(target_duration=target_dur)
            self.current_audio_playlists = [playlist]
            self.table_view.set_audio_playlist(playlist)
            tot_eff = sum(p.effective_duration for p in playlist)
            self.progress_panel.log(
                "SUCCESS",
                f"✓ Đã lập playlist âm thanh: {len(playlist)} bài | Tổng phát: {tot_eff:.1f}s"
            )
            InfoBar.success(
                title="Tạo playlist nhạc thành công",
                content=f"{len(playlist)} bài hát ngẫu nhiên ({tot_eff:.1f}s).",
                parent=self,
                position=InfoBarPosition.TOP
            )
            return playlist
        except InsufficientAudioError as e:
            self.progress_panel.log("ERROR", str(e))
            InfoBar.error(title="Lỗi kho âm thanh", content=str(e), parent=self, position=InfoBarPosition.TOP)
            return None
        except Exception as e:
            self.progress_panel.log("ERROR", f"Lỗi tạo playlist âm thanh: {e}")
            return None

    def _on_build_pool(self):
        """Stage 2–3: Slice all videos → build pool."""
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

        # Re-shuffle to reset is_used state before planning
        self.pool.reset_used()

        planner = BatchPlanner(self.pool, cut_cfg)
        try:
            plans = planner.plan_all(cut_cfg.target_total_duration)
            self.current_batch_plans = plans
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
                if batch > 1:
                    self.cut_panel.chk_no_repeat_batch.setChecked(False)
                cut_cfg = self.cut_panel.get_config()
                # Retry with allow_reuse = True
                planner = BatchPlanner(self.pool, cut_cfg)
                try:
                    plans = planner.plan_all(cut_cfg.target_total_duration)
                    self.current_batch_plans = plans
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

    def _on_start_rendering(self):
        """Stage 6–7: Render all planned videos / audio with 1-Click Auto-Preparation."""
        try:
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
            # CASE 1: STANDALONE AUDIO EXPORT
            # -------------------------------------------------------------
            if export_target == "audio_only":
                playlist = self.current_audio_playlists[0] if self.current_audio_playlists else None
                if not playlist:
                    playlist = self._on_generate_audio_playlist()
                    if not playlist:
                        self.progress_panel.log("ERROR", "Không thể tạo playlist âm thanh để xuất.")
                        return

                self.progress_panel.set_running_state(True)
                self.worker = VideoProcessorWorker(
                    batch_plans=[],
                    output_config=out_cfg,
                    audio_playlists=[playlist],
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

            # Auto-plan if plans not generated yet
            plans = self.current_batch_plans
            if not plans:
                self.progress_panel.log("INFO", "📋 Đang tự động lập kế hoạch bốc đoạn ngẫu nhiên...")
                plans = self._build_plans(cut_cfg)
                if not plans:
                    self.progress_panel.log("ERROR", "Lập kế hoạch ghép video thất bại.")
                    return

            audio_playlists_to_send = []
            if export_target == "video_with_random_audio":
                out_cfg.audio_mode = "random_audio_pool"
                picker = AudioPicker(self.audio_pool, audio_cfg)
                audio_playlists_to_send = []
                for p in plans:
                    target_dur = sum(s.effective_duration for s in p)
                    pl = picker.pick_playlist(target_duration=target_dur)
                    audio_playlists_to_send.append(pl)
                self.current_audio_playlists = audio_playlists_to_send
                if audio_playlists_to_send:
                    self.table_view.set_audio_playlist(audio_playlists_to_send[0])
                self.progress_panel.log(
                    "INFO",
                    f"✓ Đã chuẩn bị {len(audio_playlists_to_send[0])} bài nhạc ngẫu nhiên cho video."
                )
            else:
                out_cfg.audio_mode = self.output_panel.get_config().audio_mode

            self.progress_panel.set_running_state(True)
            self.worker = VideoProcessorWorker(
                batch_plans=plans,
                output_config=out_cfg,
                audio_playlists=audio_playlists_to_send,
                record_history=cut_cfg.use_export_history,
                parent=self
            )
            self._connect_worker_signals()
            self.worker.start()

        except Exception as e:
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
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "Xác nhận đóng",
                "Đang có tiến trình render đang chạy. Bạn có chắc muốn hủy và thoát không?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self.worker.cancel()
                self.worker.wait(3000)
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()
