"""
Control and Progress Panel (Khu vực D & E).
Handles Action Buttons (Start, Pause, Resume, Cancel), Progress Bars,
and Real-Time Syntax-Colored Log Console.
"""
import time
from typing import Optional
from html import escape
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QFrame, QToolButton
)
from qfluentwidgets import (
    CardWidget, PushButton, PrimaryPushButton,
    ProgressBar, ToolButton, FluentIcon
)


class ProgressPanel(CardWidget):
    """Areas D & E: Process Controls, Progress Bars, and Real-time Logs."""
    running_changed = Signal(bool)
    start_requested = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    cancel_requested = Signal()
    preview_requested = Signal()
    build_pool_requested = Signal()          # trigger pool build
    shuffle_pool_requested = Signal()        # trigger pool shuffle
    audio_playlist_requested = Signal()      # trigger audio playlist build

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_paused = False
        self.start_time: float = 0.0
        self.last_error = ""
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        header = QHBoxLayout()
        title = QLabel("Tiến trình xuất")
        title.setObjectName("sectionHeader")
        header.addWidget(title)
        layout.addLayout(header)

        # Preparation Buttons Row (pipeline steps 1→3)
        prep_layout = QHBoxLayout()
        prep_layout.setSpacing(6)

        self.btn_build_pool = PushButton("📦 Tạo kho video", self)
        self.btn_build_pool.setMinimumHeight(32)
        self.btn_build_pool.setToolTip(
            "Chia toàn bộ video nguồn thành các đoạn liên tiếp, gom vào kho chung"
        )
        self.btn_build_pool.clicked.connect(self.build_pool_requested.emit)

        self.btn_shuffle = PushButton("🔀 Xáo trộn kho", self)
        self.btn_shuffle.setMinimumHeight(32)
        self.btn_shuffle.setToolTip("Xáo trộn ngẫu nhiên thứ tự các đoạn trong kho")
        self.btn_shuffle.clicked.connect(self.shuffle_pool_requested.emit)

        self.btn_audio_playlist = PushButton("🎵 Tạo playlist nhạc", self)
        self.btn_audio_playlist.setMinimumHeight(32)
        self.btn_audio_playlist.setToolTip("Xáo trộn chu kỳ và bốc các bài âm thanh cho video")
        self.btn_audio_playlist.clicked.connect(self.audio_playlist_requested.emit)

        self.btn_preview = PushButton("📋 Xem kế hoạch", self)
        self.btn_preview.setMinimumHeight(32)
        self.btn_preview.setToolTip("Lập danh sách thứ tự ghép video từ kho đã xáo trộn")
        self.btn_preview.clicked.connect(self.preview_requested.emit)

        prep_layout.addWidget(self.btn_build_pool, 1)
        prep_layout.addWidget(self.btn_shuffle, 1)
        prep_layout.addWidget(self.btn_audio_playlist, 1)
        prep_layout.addWidget(self.btn_preview, 1)
        self.prep_widget = QWidget(self)
        self.prep_widget.setLayout(prep_layout)
        layout.addWidget(self.prep_widget)

        # Action Buttons Row (Area D)
        self.action_widget = QWidget(self)
        btn_layout = QHBoxLayout(self.action_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(10)

        self.btn_start = PrimaryPushButton("BẮT ĐẦU XUẤT (RENDER)", self)
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setStyleSheet("font-size: 14px; font-weight: bold; background-color: #0078D4;")
        self.btn_start.clicked.connect(self.start_requested.emit)

        self.btn_pause = PushButton("Tạm dừng", self)
        self.btn_pause.setMinimumHeight(38)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)

        self.btn_cancel = PushButton("Hủy xuất", self)
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setStyleSheet("color: #E81123;")
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)

        btn_layout.addWidget(self.btn_start, 2)
        btn_layout.addWidget(self.btn_pause, 1)
        btn_layout.addWidget(self.btn_cancel, 1)
        layout.addWidget(self.action_widget)

        # Progress Bars (Area E) - CapCut style dual horizontal bars
        prog_layout = QVBoxLayout()
        prog_layout.setSpacing(10)

        # Overall Progress
        self.lbl_overall = QLabel("Tiến độ: Sẵn sàng")
        self.lbl_overall.setObjectName("progressStatusLabel")
        self.lbl_overall.setStyleSheet("font-weight: 600; font-size: 13px;")
        prog_layout.addWidget(self.lbl_overall)

        # 1. Video Progress Bar
        self.video_container = QWidget(self)
        self.video_box_widget = self.video_container
        video_box = QVBoxLayout(self.video_container)
        video_box.setContentsMargins(0, 0, 0, 0)
        video_box.setSpacing(4)
        video_header = QHBoxLayout()
        self.lbl_video_title = QLabel("Video")
        self.lbl_video_title.setObjectName("progressSubTitle")
        self.lbl_video_title.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.lbl_video_val = QLabel("0%")
        self.lbl_video_val.setObjectName("progressSubVal")
        self.lbl_video_val.setStyleSheet("font-weight: 600; font-size: 12px; color: #00B4D8;")
        video_header.addWidget(self.lbl_video_title)
        video_header.addStretch()
        video_header.addWidget(self.lbl_video_val)
        video_box.addLayout(video_header)

        self.bar_video = ProgressBar(self.video_container)
        self.bar_video.setValue(0)
        self.bar_video.setFixedHeight(8)
        video_box.addWidget(self.bar_video)
        prog_layout.addWidget(self.video_container)

        # 2. Audio Progress Bar
        self.audio_container = QWidget(self)
        self.audio_box_widget = self.audio_container
        audio_box = QVBoxLayout(self.audio_container)
        audio_box.setContentsMargins(0, 0, 0, 0)
        audio_box.setSpacing(4)
        audio_header = QHBoxLayout()
        self.lbl_audio_title = QLabel("Âm thanh")
        self.lbl_audio_title.setObjectName("progressSubTitle")
        self.lbl_audio_title.setStyleSheet("font-weight: 600; font-size: 12px;")
        self.lbl_audio_val = QLabel("0%")
        self.lbl_audio_val.setObjectName("progressSubVal")
        self.lbl_audio_val.setStyleSheet("font-weight: 600; font-size: 12px; color: #10B981;")
        audio_header.addWidget(self.lbl_audio_title)
        audio_header.addStretch()
        audio_header.addWidget(self.lbl_audio_val)
        audio_box.addLayout(audio_header)

        self.bar_audio = ProgressBar(self.audio_container)
        self.bar_audio.setValue(0)
        self.bar_audio.setFixedHeight(8)
        audio_box.addWidget(self.bar_audio)
        prog_layout.addWidget(self.audio_container)

        # Backward compatibility alias
        self.bar_overall = self.bar_video

        # Video progress / segment count info
        stats_row = QHBoxLayout()
        self.lbl_video_info = QLabel("Video: 0/0")
        self.lbl_segment_info = QLabel("Đoạn: 0/0")
        self.lbl_time_info = QLabel("Thời gian: 00:00")
        self.lbl_video_info.setObjectName("mutedText")
        self.lbl_segment_info.setObjectName("mutedText")
        self.lbl_time_info.setObjectName("mutedText")

        stats_row.addWidget(self.lbl_video_info)
        stats_row.addWidget(self.lbl_segment_info)
        stats_row.addStretch()
        stats_row.addWidget(self.lbl_time_info)
        prog_layout.addLayout(stats_row)

        layout.addLayout(prog_layout)

        self.btn_toggle_logs = QToolButton(self)
        self.btn_toggle_logs.setText("Xem nhật ký chi tiết")
        self.btn_toggle_logs.setCheckable(True)
        layout.addWidget(self.btn_toggle_logs)
        self.log_widget = QWidget(self)
        log_layout = QVBoxLayout(self.log_widget)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_toggle_logs.toggled.connect(self.log_widget.setVisible)

        # Real-time Log Header & Console
        log_header = QHBoxLayout()
        log_lbl = QLabel("Nhật ký xử lý chi tiết (Logs):")
        log_lbl.setObjectName("mutedText")
        log_header.addWidget(log_lbl)
        log_header.addStretch()

        self.btn_clear_log = PushButton("Xóa nhật ký", self)
        self.btn_clear_log.setMaximumHeight(26)
        self.btn_clear_log.clicked.connect(self._clear_logs)
        log_header.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_header)

        self.console = QPlainTextEdit(self)
        self.console.setObjectName("logConsole")
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(150)
        self.console.setMaximumBlockCount(2000)
        log_layout.addWidget(self.console)
        layout.addWidget(self.log_widget)
        self.log_widget.setVisible(False)

    def _toggle_pause(self):
        if not self.is_paused:
            self.is_paused = True
            self.btn_pause.setText("Tiếp tục")
            self.btn_pause.setStyleSheet("color: #107C41; font-weight: bold;")
            self.pause_requested.emit()
        else:
            self.is_paused = False
            self.btn_pause.setText("Tạm dừng")
            self.btn_pause.setStyleSheet("")
            self.resume_requested.emit()

    def _clear_logs(self):
        self.console.clear()

    def log(self, level: str, message: str):
        if level.upper() == "ERROR":
            self.last_error = str(message)
        message = escape(str(message))
        level = escape(str(level))
        color_map = {
            "INFO": "#D4D4D4",
            "SUCCESS": "#4EC9B0",
            "WARNING": "#CE9178",
            "ERROR": "#F44747"
        }
        color = color_map.get(level.upper(), "#D4D4D4")
        timestamp = time.strftime("%H:%M:%S")
        html_msg = f'<span style="color: #858585;">[{timestamp}]</span> <b style="color: {color};">[{level}]</b> <span style="color: {color};">{message}</span>'
        self.console.appendHtml(html_msg)
        # Scroll to bottom
        sb = self.console.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_running_state(self, running: bool):
        # Prep buttons disabled while rendering
        self.btn_build_pool.setEnabled(not running)
        self.btn_shuffle.setEnabled(not running)
        self.btn_audio_playlist.setEnabled(not running)
        self.btn_preview.setEnabled(not running)
        # Main controls
        self.btn_start.setEnabled(not running)
        self.btn_pause.setEnabled(running)
        self.btn_cancel.setEnabled(running)
        self.is_paused = False
        self.btn_pause.setText("Tạm dừng")
        self.btn_pause.setStyleSheet("")
        if running:
            self.start_time = time.time()
            self.reset_bars()
            self.lbl_overall.setText("Đang khởi động tiến trình...")
        self.running_changed.emit(running)

    def reset_bars(self):
        """Reset both progress bars to 0%."""
        self.bar_video.setValue(0)
        self.lbl_video_val.setText("0%")
        self.bar_audio.setValue(0)
        self.lbl_audio_val.setText("0%")

    def update_video_progress(self, percent: float):
        """Update video progress bar directly from processor percent."""
        pct = max(0, min(100, int(round(float(percent)))))
        self.bar_video.setValue(pct)
        self.lbl_video_val.setText(f"{pct}%")

    def update_audio_progress(self, percent: float):
        """Update audio progress bar directly from processor percent."""
        pct = max(0, min(100, int(round(float(percent)))))
        self.bar_audio.setValue(pct)
        self.lbl_audio_val.setText(f"{pct}%")

    def set_export_mode(self, target: str = "video_with_random_audio", has_audio: Optional[bool] = None, audio_mode: Optional[str] = None):
        """
        Configure visibility of Video and Audio progress bars based on export mode:
        - 'audio_only': hide Video bar, show Audio bar.
        - 'video_only': show Video bar, hide Audio bar if no audio processing.
        - 'video_with_random_audio': show both Video and Audio bars.
        """
        if has_audio is None:
            if audio_mode is not None:
                has_audio = (audio_mode not in ("none", "mute"))
            else:
                has_audio = True

        if target == "audio_only":
            self.video_container.setVisible(False)
            self.audio_container.setVisible(True)
        elif target == "video_only":
            self.video_container.setVisible(True)
            self.audio_container.setVisible(bool(has_audio))
        else:
            self.video_container.setVisible(True)
            self.audio_container.setVisible(True)

    def update_overall_progress(self, percent: float, status_msg: str):
        pct = max(0.0, min(100.0, float(percent)))
        self.lbl_overall.setText(f"Tiến độ: {int(pct)}% · {status_msg}")

        if self.start_time > 0:
            elapsed = int(time.time() - self.start_time)
            m, s = divmod(elapsed, 60)
            h, m = divmod(m, 60)
            if h > 0:
                self.lbl_time_info.setText(f"Thời gian: {h:02d}:{m:02d}:{s:02d}")
            else:
                self.lbl_time_info.setText(f"Thời gian: {m:02d}:{s:02d}")

    def update_stats(self, cur_video: int, total_videos: int, cur_seg: int, total_segs: int):
        self.lbl_video_info.setText(f"Video: {cur_video}/{total_videos}")
        self.lbl_segment_info.setText(f"Đoạn: {cur_seg}/{total_segs}")
        if total_segs > 0 and cur_seg > 0:
            v_pct = min(100.0, max(0.0, (cur_seg / total_segs) * 100.0))
            self.update_video_progress(v_pct)

    def set_logs_visible(self, visible: bool):
        self.btn_toggle_logs.setChecked(visible)
        self.log_widget.setVisible(visible)
