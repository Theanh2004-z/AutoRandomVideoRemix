"""
Audio Panel (Khu vực Âm Thanh Ngẫu Nhiên - Phần 2).
Handles folder picking, scanning, intact track management, cycle shuffle settings,
and standalone audio export option.
"""
import os
from typing import List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog,
    QHeaderView, QTableWidgetItem, QFrame, QCheckBox, QRadioButton, QButtonGroup
)
from qfluentwidgets import (
    CardWidget, PushButton, PrimaryPushButton, LineEdit,
    ProgressBar, InfoBar, InfoBarPosition, ComboBox
)
from core.config import AudioInfo, AudioConfig
from core.settings_manager import settings
from ui.auto_scan import AutoScanController
from ui.background import MediaScanWorker


class AudioPanel(CardWidget):
    """Part 2: Audio Source & Random Playlist Configuration Panel."""
    scan_started = Signal()
    scan_state_changed = Signal(bool)
    audios_scanned = Signal(list)            # List[AudioInfo]
    shuffle_audio_requested = Signal()
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.valid_audios: List[AudioInfo] = []
        self.invalid_audios: List[AudioInfo] = []
        self.scanner_thread: Optional[MediaScanWorker] = None
        self._init_ui()
        self.auto_scan = AutoScanController(self, "audio")
        self.combo_mode.currentIndexChanged.connect(lambda *args: self.config_changed.emit())
        self.combo_audio_format.currentIndexChanged.connect(lambda *args: self.config_changed.emit())
        self.chk_no_consecutive.stateChanged.connect(lambda *args: self.config_changed.emit())
        self.chk_auto_reshuffle.stateChanged.connect(lambda *args: self.config_changed.emit())

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Âm thanh nguồn")
        title.setObjectName("sectionHeader")
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        # Folder selection
        folder_box = QHBoxLayout()
        self.folder_edit = LineEdit(self)
        self.folder_edit.setPlaceholderText("Chọn thư mục chứa kho nhạc (MP3, WAV, M4A, AAC, FLAC)...")
        self.folder_edit.setClearButtonEnabled(True)

        saved_folder = settings.get("audio_folder", "")
        if saved_folder:
            self.folder_edit.setText(saved_folder)
        self.folder_edit.textChanged.connect(lambda t: settings.set("audio_folder", t.strip()))

        self.btn_browse = PushButton("Chọn thư mục...", self)
        self.btn_browse.clicked.connect(self._choose_folder)

        self.btn_scan = PushButton("Kiểm tra lại", self)
        self.btn_scan.clicked.connect(self._start_scan)

        folder_box.addWidget(self.folder_edit, 1)
        folder_box.addWidget(self.btn_browse)
        folder_box.addWidget(self.btn_scan)
        layout.addLayout(folder_box)

        # Sub-folder checkbox
        opt_box = QHBoxLayout()
        self.chk_recursive = QCheckBox("Quét cả các thư mục con", self)
        self.chk_recursive.setChecked(bool(settings.get("recursive_audio", False)))
        self.chk_recursive.stateChanged.connect(lambda s: settings.set("recursive_audio", self.chk_recursive.isChecked()))
        opt_box.addWidget(self.chk_recursive)
        opt_box.addStretch()
        layout.addLayout(opt_box)

        # Progress bar
        self.scan_bar = ProgressBar(self)
        self.scan_bar.setVisible(False)
        self.scan_status_lbl = QLabel("", self)
        self.scan_status_lbl.setWordWrap(True)
        self.scan_status_lbl.setObjectName("mutedText")
        self.scan_status_lbl.setVisible(False)
        layout.addWidget(self.scan_bar)
        layout.addWidget(self.scan_status_lbl)

        # Stats Cards
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(10)
        self.stat_valid_card = self._create_stat_card("0", "Bài hát hợp lệ")
        self.stat_invalid_card = self._create_stat_card("0", "File lỗi / bỏ qua")
        self.stat_duration_card = self._create_stat_card("00:00:00", "Tổng thời lượng kho")
        stats_layout.addWidget(self.stat_valid_card)
        stats_layout.addWidget(self.stat_invalid_card)
        stats_layout.addWidget(self.stat_duration_card)
        layout.addLayout(stats_layout)

        self.options_widget = QWidget(self)
        options_layout = QVBoxLayout(self.options_widget)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(12)
        # Audio Shuffle & Playback Rules
        sep = QLabel("Các bài được giữ nguyên trong mỗi lượt; ứng dụng chỉ cắt bài cuối khi bạn chọn khớp thời lượng.")
        sep.setObjectName("mutedText")
        sep.setWordWrap(True)
        options_layout.addWidget(sep)

        chk_vbox = QVBoxLayout()
        chk_vbox.setSpacing(6)

        self.chk_no_consecutive = QCheckBox(
            "Tránh phát cùng một bài liên tiếp", self
        )
        self.chk_no_consecutive.setChecked(bool(settings.get("no_consecutive_audio", True)))
        self.chk_no_consecutive.stateChanged.connect(lambda s: settings.set("no_consecutive_audio", self.chk_no_consecutive.isChecked()))
        chk_vbox.addWidget(self.chk_no_consecutive)

        self.chk_cycle = QCheckBox(
            "Mỗi bài được dùng một lần trong mỗi lượt xáo trộn", self
        )
        self.chk_cycle.setChecked(True)
        self.chk_cycle.setEnabled(False)
        self.chk_cycle.setToolTip("Quy tắc cố định: dùng hết lượt trước khi bắt đầu lượt mới.")
        chk_vbox.addWidget(self.chk_cycle)

        self.chk_auto_reshuffle = QCheckBox(
            "Xáo trộn lượt mới nếu nhạc chưa đủ thời lượng", self
        )
        self.chk_auto_reshuffle.setChecked(bool(settings.get("auto_reshuffle_audio", True)))
        self.chk_auto_reshuffle.toggled.connect(lambda value: settings.set("auto_reshuffle_audio", value))
        chk_vbox.addWidget(self.chk_auto_reshuffle)

        options_layout.addLayout(chk_vbox)

        # Duration Mode (Mode A vs Mode B)
        mode_box = QHBoxLayout()
        mode_lbl = QLabel("Chế độ thời lượng:")
        mode_lbl.setStyleSheet("font-weight: 500;")
        self.combo_mode = ComboBox(self)
        self.combo_mode.addItem("Khớp thời lượng — chỉ cắt phần đuôi bài cuối", userData="fit_target")
        self.combo_mode.addItem("Phát trọn bài cuối — kết quả có thể dài hơn mục tiêu", userData="keep_all")
        saved_mode = settings.get("audio_mode", "fit_target")
        idx_mode = self.combo_mode.findData(saved_mode)
        if idx_mode >= 0:
            self.combo_mode.setCurrentIndex(idx_mode)
        self.combo_mode.currentIndexChanged.connect(lambda: settings.set("audio_mode", self.combo_mode.currentData() or "fit_target"))
        mode_box.addWidget(mode_lbl)
        mode_box.addWidget(self.combo_mode, 1)
        options_layout.addLayout(mode_box)

        # Export Mode
        export_box = QHBoxLayout()
        export_lbl = QLabel("Mục tiêu xuất:")
        export_lbl.setStyleSheet("font-weight: 500;")
        self.combo_export_target = ComboBox(self)
        self.combo_export_target.addItem("🎬 Ghép Video + Âm thanh ngẫu nhiên (Hoàn chỉnh MP4)", userData="video_with_random_audio")
        self.combo_export_target.addItem("🎵 Chỉ xuất Âm thanh ngẫu nhiên (File Audio độc lập)", userData="audio_only")
        self.combo_export_target.addItem("🎞 Xuất Video theo cấu hình Khu vực C (Âm thanh gốc / BGM / Tắt tiếng)", userData="video_only")
        self.combo_export_target.addItem("Không chọn nguồn", userData="none")
        saved_export = settings.get("export_target", "video_with_random_audio")
        idx_exp = self.combo_export_target.findData(saved_export)
        if idx_exp >= 0:
            self.combo_export_target.setCurrentIndex(idx_exp)
        self.combo_export_target.currentIndexChanged.connect(self._on_export_target_changed)
        export_box.addWidget(export_lbl)
        export_box.addWidget(self.combo_export_target, 1)
        self.export_selector = QWidget(self)
        self.export_selector.setLayout(export_box)
        options_layout.addWidget(self.export_selector)

        # Audio Format for Audio-only export
        self.format_box = QHBoxLayout()
        fmt_lbl = QLabel("Định dạng âm thanh xuất:")
        fmt_lbl.setStyleSheet("font-weight: 500;")
        self.combo_audio_format = ComboBox(self)
        self.combo_audio_format.addItem("MP3 (320kbps chất lượng cao)", userData="mp3")
        self.combo_audio_format.addItem("M4A / AAC (Chuẩn Apple/YouTube)", userData="m4a")
        self.combo_audio_format.addItem("WAV (Âm thanh không nén)", userData="wav")
        saved_fmt = settings.get("audio_output_format", "mp3")
        idx_fmt = self.combo_audio_format.findData(saved_fmt)
        if idx_fmt >= 0:
            self.combo_audio_format.setCurrentIndex(idx_fmt)
        self.combo_audio_format.currentIndexChanged.connect(lambda: settings.set("audio_output_format", self.combo_audio_format.currentData() or "mp3"))
        self.format_box.addWidget(fmt_lbl)
        self.format_box.addWidget(self.combo_audio_format, 1)
        self.format_container = QWidget()
        self.format_container.setLayout(self.format_box)
        self.format_container.setVisible(self.get_export_target() == "audio_only")
        options_layout.addWidget(self.format_container)

        layout.addWidget(self.options_widget)

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

    def _on_export_target_changed(self):
        target = self.get_export_target()
        settings.set("export_target", target)
        self.format_container.setVisible(target == "audio_only")
        self.config_changed.emit()

    def _choose_folder(self):
        curr = self.folder_edit.text().strip()
        start_dir = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa file âm thanh", start_dir)
        if chosen:
            self.folder_edit.setText(chosen)

    def _start_scan(self):
        self.auto_scan.start()

    def _on_scan_progress(self, current: int, total: int, filename: str):
        if total > 0:
            self.scan_bar.setValue(int(current / total * 100))
        self.scan_status_lbl.setText(f"Đang phân tích ({current}/{total}): {filename}")

    def _on_scan_finished(self, valid: List[AudioInfo], invalid: List[AudioInfo]):
        self.valid_audios = valid
        self.invalid_audios = invalid
        self.btn_scan.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.scan_bar.setVisible(False)
        self.scan_status_lbl.setVisible(False)

        # Update stats
        self.stat_valid_card.val_lbl.setText(str(len(valid)))
        self.stat_invalid_card.val_lbl.setText(str(len(invalid)))

        total_sec = sum(a.duration for a in valid)
        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
        self.stat_duration_card.val_lbl.setText(dur_str)

        self.scan_status_lbl.setVisible(True)
        self.scan_status_lbl.setText(f"Đã kiểm tra {len(valid)} tệp hợp lệ · {len(invalid)} tệp bỏ qua.")

        self.audios_scanned.emit(valid)
        self.config_changed.emit()

    def get_config(self) -> AudioConfig:
        mode_val = self.combo_mode.currentData()
        if not mode_val:
            mode_val = "keep_all" if self.combo_mode.currentIndex() == 1 else "fit_target"
        fmt_val = self.combo_audio_format.currentData()
        if not fmt_val:
            idx = self.combo_audio_format.currentIndex()
            fmt_val = "m4a" if idx == 1 else ("wav" if idx == 2 else "mp3")
        return AudioConfig(
            folder_path=self.folder_edit.text().strip(),
            mode=str(mode_val),
            no_consecutive_same_track=self.chk_no_consecutive.isChecked(),
            auto_reshuffle_cycle=self.chk_auto_reshuffle.isChecked(),
            audio_output_format=str(fmt_val)
        )

    def get_export_target(self) -> str:
        data = self.combo_export_target.currentData()
        if not data:
            idx = self.combo_export_target.currentIndex()
            if idx == 1:
                return "audio_only"
            elif idx == 2:
                return "video_only"
            return "video_with_random_audio"
        return str(data)
