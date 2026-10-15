"""
Source Videos Panel (Khu vực A).
Allows picking folder, scanning video files, showing statistics and file list.
"""
import os
from typing import List, Tuple
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
    QHeaderView, QTableWidgetItem, QFrame, QToolButton, QCheckBox
)
from qfluentwidgets import (
    CardWidget, PushButton, PrimaryPushButton, LineEdit,
    TableWidget, ProgressBar, InfoBar, InfoBarPosition
)
from core.config import VideoInfo
from core.settings_manager import settings
from ui.auto_scan import AutoScanController
from ui.background import MediaScanWorker


class SourcePanel(CardWidget):
    """Area A: Source Videos Management."""
    scan_started = Signal()
    scan_state_changed = Signal(bool)
    videos_scanned = Signal(list)  # List[VideoInfo]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.valid_videos: List[VideoInfo] = []
        self.invalid_videos: List[VideoInfo] = []
        self.scanner_thread: MediaScanWorker = None
        self._init_ui()
        self.auto_scan = AutoScanController(self, "video")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("Video nguồn")
        title.setObjectName("sectionHeader")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        # Folder selection row
        folder_layout = QHBoxLayout()
        self.folder_edit = LineEdit(self)
        self.folder_edit.setPlaceholderText("Chọn thư mục chứa kho video nguồn (ví dụ: D:\\KhoVideo)...")
        self.folder_edit.setClearButtonEnabled(True)

        saved_folder = settings.get("video_folder", "")
        if saved_folder:
            self.folder_edit.setText(saved_folder)
        self.folder_edit.textChanged.connect(lambda t: settings.set("video_folder", t.strip()))

        self.btn_browse = PushButton("Chọn thư mục...", self)
        self.btn_browse.clicked.connect(self._choose_folder)

        self.btn_scan = PushButton("Kiểm tra lại", self)
        self.btn_scan.clicked.connect(self._start_scan)

        folder_layout.addWidget(self.folder_edit, 1)
        folder_layout.addWidget(self.btn_browse)
        folder_layout.addWidget(self.btn_scan)
        layout.addLayout(folder_layout)

        # Sub-folder checkbox
        opt_layout = QHBoxLayout()
        self.chk_recursive = QCheckBox("Quét cả các thư mục con bên trong", self)
        self.chk_recursive.setChecked(bool(settings.get("recursive_video", False)))
        self.chk_recursive.stateChanged.connect(lambda s: settings.set("recursive_video", self.chk_recursive.isChecked()))
        opt_layout.addWidget(self.chk_recursive)
        opt_layout.addStretch()
        layout.addLayout(opt_layout)

        # Scan progress bar (hidden initially)
        self.scan_bar = ProgressBar(self)
        self.scan_bar.setVisible(False)
        self.scan_status_lbl = QLabel("", self)
        self.scan_status_lbl.setWordWrap(True)
        self.scan_status_lbl.setObjectName("mutedText")
        self.scan_status_lbl.setVisible(False)
        layout.addWidget(self.scan_bar)
        layout.addWidget(self.scan_status_lbl)

        # Stats Cards row
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)

        self.stat_valid_card = self._create_stat_card("0", "Video hợp lệ")
        self.stat_invalid_card = self._create_stat_card("0", "Video lỗi / bỏ qua")
        self.stat_duration_card = self._create_stat_card("00:00:00", "Tổng thời lượng")
        self.stat_segments_card = self._create_stat_card("0", "Tổng đoạn sau chia")

        stats_layout.addWidget(self.stat_valid_card)
        stats_layout.addWidget(self.stat_invalid_card)
        stats_layout.addWidget(self.stat_duration_card)
        stats_layout.addWidget(self.stat_segments_card)
        layout.addLayout(stats_layout)

        # Videos preview table
        table_lbl = QLabel("Danh sách video phát hiện:")
        table_lbl.setStyleSheet("font-weight: 600; color: #555;")
        layout.addWidget(table_lbl)

        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Tên Video", "Thời lượng", "Độ phân giải", "FPS", "Trạng thái"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(140)
        self.table.setMaximumHeight(200)
        self.btn_details = QToolButton(self)
        self.btn_details.setText("Xem danh sách video")
        self.btn_details.setCheckable(True)
        self.btn_details.toggled.connect(self.table.setVisible)
        layout.addWidget(self.btn_details)
        table_lbl.setVisible(False)
        self.table.setVisible(False)
        layout.addWidget(self.table)

    def _create_stat_card(self, num_text: str, label_text: str) -> QFrame:
        card = QFrame(self)
        card.setProperty("statCard", True)
        l = QVBoxLayout(card)
        l.setContentsMargins(8, 6, 8, 6)
        l.setSpacing(2)

        val_lbl = QLabel(num_text, card)
        val_lbl.setObjectName("statNumber")
        val_lbl.setAlignment(Qt.AlignCenter)

        sub_lbl = QLabel(label_text, card)
        sub_lbl.setObjectName("statLabel")
        sub_lbl.setAlignment(Qt.AlignCenter)

        l.addWidget(val_lbl)
        l.addWidget(sub_lbl)
        card.val_lbl = val_lbl
        return card

    def _choose_folder(self):
        curr = self.folder_edit.text().strip()
        start_dir = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục video nguồn", start_dir)
        if chosen:
            self.folder_edit.setText(chosen)

    def _start_scan(self):
        self.auto_scan.start()

    def _on_scan_progress(self, current: int, total: int, filename: str):
        if total > 0:
            pct = int((current / total) * 100)
            self.scan_bar.setValue(pct)
            self.scan_status_lbl.setText(f"Đang kiểm tra ({current}/{total}): {filename}")

    def _on_scan_finished(self, valid: List[VideoInfo], invalid: List[VideoInfo]):
        self.valid_videos = valid
        self.invalid_videos = invalid
        self.btn_scan.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.scan_bar.setVisible(False)
        self.scan_status_lbl.setVisible(False)

        # Update stats
        self.stat_valid_card.val_lbl.setText(str(len(valid)))
        self.stat_invalid_card.val_lbl.setText(str(len(invalid)))

        total_sec = sum(v.duration for v in valid)
        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        if h > 0:
            dur_str = f"{h}h {m:02d}m {s:02d}s"
        else:
            dur_str = f"{m:02d}m {s:02d}s"
        self.stat_duration_card.val_lbl.setText(dur_str)

        # Populate table
        all_vids = valid + invalid
        self.table.setRowCount(len(all_vids))
        for row, v in enumerate(all_vids):
            self.table.setItem(row, 0, QTableWidgetItem(v.filename))
            self.table.setItem(row, 1, QTableWidgetItem(v.formatted_duration if v.is_valid else "N/A"))
            self.table.setItem(row, 2, QTableWidgetItem(f"{v.width}x{v.height}" if v.is_valid else "N/A"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{v.fps} FPS" if v.is_valid else "N/A"))

            status_item = QTableWidgetItem("Hợp lệ" if v.is_valid else f"Lỗi: {v.error_msg}")
            if v.is_valid:
                status_item.setForeground(Qt.darkGreen)
            else:
                status_item.setForeground(Qt.red)
            self.table.setItem(row, 4, status_item)

        self.update_estimated_segments()

        self.scan_status_lbl.setVisible(True)
        self.scan_status_lbl.setText(f"Đã kiểm tra {len(valid)} tệp hợp lệ · {len(invalid)} tệp bỏ qua.")

        self.videos_scanned.emit(valid)

    def update_estimated_segments(self, segment_dur: float = 10.0, merge_remainder: bool = True):
        """Calculates and updates total estimated sliced segments for all valid videos."""
        if not self.valid_videos or segment_dur <= 0:
            self.stat_segments_card.val_lbl.setText("0")
            return
        try:
            import math
            total_segs = sum(max(1, math.floor(v.duration / segment_dur) if merge_remainder else
                                 math.ceil(v.duration / segment_dur))
                             for v in self.valid_videos if v.is_valid and v.duration > 0)
            self.stat_segments_card.val_lbl.setText(str(total_segs))
        except Exception:
            self.stat_segments_card.val_lbl.setText("0")

