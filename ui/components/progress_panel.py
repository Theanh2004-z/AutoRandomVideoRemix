"""
Control and Progress Panel (Khu vực D & E).
Handles Action Buttons (Start, Pause, Resume, Cancel), Progress Bars,
and Real-Time Syntax-Colored Log Console.
"""
import time
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QFrame
)
from qfluentwidgets import (
    CardWidget, PushButton, PrimaryPushButton,
    ProgressBar, ToolButton, FluentIcon
)


class ProgressPanel(CardWidget):
    """Areas D & E: Process Controls, Progress Bars, and Real-time Logs."""
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
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        header = QHBoxLayout()
        title = QLabel("KHU VỰC D & E: ĐIỀU KHIỂN & TIẾN TRÌNH XỬ LÝ")
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
        layout.addLayout(prep_layout)

        # Action Buttons Row (Area D)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_start = PrimaryPushButton("BẮT ĐẦU XUẤT (RENDER)", self)
        self.btn_start.setMinimumHeight(38)
        self.btn_start.setStyleSheet("font-size: 14px; font-weight: bold; background-color: #0078D4;")
        self.btn_start.clicked.connect(self.start_requested.emit)

        self.btn_pause = PushButton("⏸ Tạm dừng", self)
        self.btn_pause.setMinimumHeight(38)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)

        self.btn_cancel = PushButton("⏹ Hủy bỏ", self)
        self.btn_cancel.setMinimumHeight(38)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setStyleSheet("color: #E81123;")
        self.btn_cancel.clicked.connect(self.cancel_requested.emit)

        btn_layout.addWidget(self.btn_start, 2)
        btn_layout.addWidget(self.btn_pause, 1)
        btn_layout.addWidget(self.btn_cancel, 1)
        layout.addLayout(btn_layout)

        # Progress Bars (Area E)
        prog_layout = QVBoxLayout()
        prog_layout.setSpacing(6)

        # Overall Progress
        self.lbl_overall = QLabel("Tiến độ tổng thể: Sẵn sàng")
        self.lbl_overall.setStyleSheet("font-weight: 600;")
        self.bar_overall = ProgressBar(self)
        self.bar_overall.setValue(0)
        prog_layout.addWidget(self.lbl_overall)
        prog_layout.addWidget(self.bar_overall)

        # Video progress / segment count info
        stats_row = QHBoxLayout()
        self.lbl_video_info = QLabel("Video: 0/0")
        self.lbl_segment_info = QLabel("Đoạn: 0/0")
        self.lbl_time_info = QLabel("Thời gian: 00:00")
        self.lbl_video_info.setStyleSheet("color: #555; font-size: 12px;")
        self.lbl_segment_info.setStyleSheet("color: #555; font-size: 12px;")
        self.lbl_time_info.setStyleSheet("color: #555; font-size: 12px;")

        stats_row.addWidget(self.lbl_video_info)
        stats_row.addWidget(self.lbl_segment_info)
        stats_row.addStretch()
        stats_row.addWidget(self.lbl_time_info)
        prog_layout.addLayout(stats_row)

        layout.addLayout(prog_layout)

        # Real-time Log Header & Console
        log_header = QHBoxLayout()
        log_lbl = QLabel("Nhật ký xử lý chi tiết (Logs):")
        log_lbl.setStyleSheet("font-weight: 600; color: #444;")
        log_header.addWidget(log_lbl)
        log_header.addStretch()

        self.btn_clear_log = PushButton("Xóa nhật ký", self)
        self.btn_clear_log.setMaximumHeight(26)
        self.btn_clear_log.clicked.connect(self._clear_logs)
        log_header.addWidget(self.btn_clear_log)
        layout.addLayout(log_header)

        self.console = QPlainTextEdit(self)
        self.console.setObjectName("logConsole")
        self.console.setReadOnly(True)
        self.console.setMinimumHeight(150)
        layout.addWidget(self.console)

    def _toggle_pause(self):
        if not self.is_paused:
            self.is_paused = True
            self.btn_pause.setText("▶ Tiếp tục")
            self.btn_pause.setStyleSheet("color: #107C41; font-weight: bold;")
            self.pause_requested.emit()
        else:
            self.is_paused = False
            self.btn_pause.setText("⏸ Tạm dừng")
            self.btn_pause.setStyleSheet("")
            self.resume_requested.emit()

    def _clear_logs(self):
        self.console.clear()

    def log(self, level: str, message: str):
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
        self.btn_pause.setText("⏸ Tạm dừng")
        self.btn_pause.setStyleSheet("")
        if running:
            self.start_time = time.time()
            self.bar_overall.setValue(0)

    def update_overall_progress(self, percent: float, status_msg: str):
        self.bar_overall.setValue(int(percent))
        self.lbl_overall.setText(f"Tiến độ: {int(percent)}% - {status_msg}")
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
