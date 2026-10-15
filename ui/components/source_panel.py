"""
Source Videos Panel (Khu vực A).
Allows picking folder, scanning video files, showing statistics and file list.
"""
import os
from typing import List, Tuple
from PySide6.QtCore import Qt, Signal, QThread, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
    QHeaderView, QTableWidgetItem, QFrame, QCheckBox
)
from qfluentwidgets import (
    CardWidget, PushButton, PrimaryPushButton, LineEdit,
    TableWidget, ProgressBar, InfoBar, InfoBarPosition
)
from core.config import VideoInfo
from core.video_scanner import scan_directory
from core.settings_manager import settings


class ScannerThread(QThread):
    progress = Signal(int, int, str)
    finished_scan = Signal(list, list)

    def __init__(self, folder: str, recursive: bool, parent=None):
        super().__init__(parent)
        self.folder = folder
        self.recursive = recursive
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        valid, invalid = scan_directory(
            directory=self.folder,
            recursive=self.recursive,
            progress_callback=lambda cur, tot, name: self.progress.emit(cur, tot, name),
            cancel_check=lambda: self._cancelled
        )
        self.finished_scan.emit(valid, invalid)


class SourcePanel(CardWidget):
    """Area A: Source Videos Management."""
    videos_scanned = Signal(list)  # List[VideoInfo]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.valid_videos: List[VideoInfo] = []
        self.invalid_videos: List[VideoInfo] = []
        self.scanner_thread: ScannerThread = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header_layout = QHBoxLayout()
        title = QLabel("KHU VỰC A: THƯ MỤC VIDEO NGUỒN")
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

        self.btn_scan = PrimaryPushButton("Quét video", self)
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
        layout.addWidget(self.table)

        # Auto-scan on startup if saved directory exists
        if saved_folder and os.path.isdir(saved_folder):
            QTimer.singleShot(250, self._start_scan)

    def _create_stat_card(self, num_text: str, label_text: str) -> QFrame:
        card = QFrame(self)
        card.setStyleSheet("background-color: #F8F9FA; border: 1px solid #E5E5E5; border-radius: 6px; padding: 6px;")
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
            self._start_scan()

    def _start_scan(self):
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            InfoBar.error(
                title="Lỗi thư mục",
                content="Vui lòng chọn một thư mục video nguồn hợp lệ trên máy tính!",
                parent=self,
                position=InfoBarPosition.TOP
            )
            return

        self.btn_scan.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.scan_bar.setVisible(True)
        self.scan_bar.setValue(0)
        self.scan_status_lbl.setVisible(True)
        self.scan_status_lbl.setText("Đang tìm kiếm file video...")

        self.scanner_thread = ScannerThread(folder, self.chk_recursive.isChecked(), self)
        self.scanner_thread.progress.connect(self._on_scan_progress)
        self.scanner_thread.finished_scan.connect(self._on_scan_finished)
        self.scanner_thread.start()

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

        if valid:
            InfoBar.success(
                title="Quét hoàn tất",
                content=f"Tìm thấy {len(valid)} video hợp lệ (Tổng: {dur_str}).",
                parent=self,
                position=InfoBarPosition.TOP
            )
        else:
            InfoBar.warning(
                title="Không tìm thấy video",
                content="Không có video hợp lệ nào trong thư mục đã chọn.",
                parent=self,
                position=InfoBarPosition.TOP
            )

        self.videos_scanned.emit(valid)

    def update_estimated_segments(self, segment_dur: float = 10.0, merge_remainder: bool = True):
        """Calculates and updates total estimated sliced segments for all valid videos."""
        if not self.valid_videos or segment_dur <= 0:
            self.stat_segments_card.val_lbl.setText("0")
            return
        try:
            from core.segment_slicer import SegmentSlicer
            slicer = SegmentSlicer(segment_duration=segment_dur, merge_remainder=merge_remainder)
            total_segs = sum(len(slicer.slice(v)) for v in self.valid_videos)
            self.stat_segments_card.val_lbl.setText(str(total_segs))
        except Exception:
            self.stat_segments_card.val_lbl.setText("0")

