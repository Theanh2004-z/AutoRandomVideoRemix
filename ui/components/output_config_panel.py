"""
Output and Audio Configuration Panel (Khu vực C).
Handles resolution, FPS, aspect ratio scaling, hardware acceleration,
audio stripping/retention/BGM, and output directory.
"""
import os
import subprocess
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout,
    QFileDialog, QToolButton, QCheckBox
)
from qfluentwidgets import (
    CardWidget, ComboBox, LineEdit, PushButton,
    FluentIcon
)
from core.config import OutputConfig
from core.settings_manager import settings


class OutputConfigPanel(CardWidget):
    """Area C: Output Settings & Audio Mode."""
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
        for combo in (self.combo_res, self.combo_fps, self.combo_aspect, self.combo_hw, self.combo_quality, self.combo_audio, self.combo_speed, self.combo_parallel, self.combo_decoder):
            combo.currentIndexChanged.connect(lambda *args: self.config_changed.emit())
        self.edit_outdir.textChanged.connect(lambda *args: self.config_changed.emit())
        self.edit_prefix.textChanged.connect(lambda *args: self.config_changed.emit())
        self.edit_bgm.textChanged.connect(lambda *args: self.config_changed.emit())
        self.chk_gpu_processing.toggled.connect(lambda *args: self.config_changed.emit())
        for combo, key in ((self.combo_quality, 'quality'), (self.combo_aspect, 'aspect_ratio_mode'),
                           (self.combo_audio, 'video_audio_mode')):
            index = combo.findData(settings.get(key, combo.currentData()))
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.currentIndexChanged.connect(lambda *args, c=combo, k=key: settings.set(k,c.currentData()))
        self.edit_bgm.setText(settings.get('bgm_path', ''))
        self.edit_bgm.textChanged.connect(lambda text: settings.set('bgm_path', text.strip()))

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QLabel("Chất lượng và nơi lưu kết quả")
        title.setObjectName("sectionHeader")
        layout.addWidget(title)

        grid = QGridLayout()
        advanced_grid = QGridLayout()
        advanced_grid.setSpacing(10)
        grid.setSpacing(10)

        # 1. Resolution
        lbl_res = QLabel("Độ phân giải xuất:")
        lbl_res.setStyleSheet("font-weight: 500;")
        self.combo_res = ComboBox(self)
        self.combo_res.addItem("1920x1080 (Full HD 16:9 - Chuẩn)", userData="1920x1080")
        self.combo_res.addItem("1280x720 (HD 16:9)", userData="1280x720")
        self.combo_res.addItem("3840x2160 (4K UHD 16:9)", userData="3840x2160")
        self.combo_res.addItem("1080x1920 (Dọc 9:16 - TikTok / Shorts)", userData="1080x1920")
        saved_res = settings.get("resolution", "1920x1080")
        idx_res = self.combo_res.findData(saved_res)
        if idx_res >= 0:
            self.combo_res.setCurrentIndex(idx_res)
        self.combo_res.currentIndexChanged.connect(lambda: settings.set("resolution", self.combo_res.currentData()))
        grid.addWidget(lbl_res, 0, 0)
        grid.addWidget(self.combo_res, 0, 1)

        # 2. FPS
        lbl_fps = QLabel("Tốc độ khung hình (FPS):")
        lbl_fps.setStyleSheet("font-weight: 500;")
        self.combo_fps = ComboBox(self)
        self.combo_fps.addItem("30 FPS (Mặc định chuẩn)", userData=30)
        self.combo_fps.addItem("60 FPS (Siêu mượt)", userData=60)
        self.combo_fps.addItem("24 FPS (Điện ảnh Cinema)", userData=24)
        self.combo_fps.addItem("25 FPS (Chuẩn PAL)", userData=25)
        saved_fps = settings.get("fps", 30)
        idx_fps = self.combo_fps.findData(saved_fps)
        if idx_fps >= 0:
            self.combo_fps.setCurrentIndex(idx_fps)
        self.combo_fps.currentIndexChanged.connect(lambda: settings.set("fps", self.combo_fps.currentData()))
        advanced_grid.addWidget(lbl_fps, 0, 0)
        advanced_grid.addWidget(self.combo_fps, 0, 1)

        # 3. Aspect Ratio Handling
        lbl_ar = QLabel("Xử lý tỷ lệ khung hình:")
        lbl_ar.setStyleSheet("font-weight: 500;")
        self.combo_aspect = ComboBox(self)
        self.combo_aspect.addItem("Giữ đủ hình + Viền đen (Fit / Letterbox) [Mặc định]", userData="fit")
        self.combo_aspect.addItem("Phóng to lấp đầy khung (Crop / Fill)", userData="crop")
        advanced_grid.addWidget(lbl_ar, 1, 0)
        advanced_grid.addWidget(self.combo_aspect, 1, 1)

        # 4. Hardware Acceleration
        # Hardware probing happens in the export worker, not while opening UI.

        lbl_hw = QLabel("Mã hóa phần cứng:")
        lbl_hw.setStyleSheet("font-weight: 500;")
        self.combo_hw = ComboBox(self)
        self.combo_hw.addItem("Tự động (Tối ưu theo phần cứng máy - Khuyên dùng)", userData="auto")
        self.combo_hw.addItem("NVIDIA NVENC (Card rời NVIDIA GeForce/RTX/Quadro)", userData="nvenc")
        self.combo_hw.addItem("AMD AMF (Card rời AMD Radeon RX/Vega/Pro)", userData="amf")
        self.combo_hw.addItem("Intel QSV (Intel QuickSync / Card Arc)", userData="qsv")
        self.combo_hw.addItem("CPU đa luồng thuần (Tương thích 100% mọi dòng máy)", userData="cpu")

        saved_hw = settings.get("hw_accel", "auto")
        idx_hw = self.combo_hw.findData(saved_hw)
        if idx_hw >= 0:
            self.combo_hw.setCurrentIndex(idx_hw)
        self.combo_hw.currentIndexChanged.connect(lambda: settings.set("hw_accel", self.combo_hw.currentData()))
        advanced_grid.addWidget(lbl_hw, 2, 0)
        advanced_grid.addWidget(self.combo_hw, 2, 1)

        # Detected GPU Info Badge
        self.lbl_gpu_detected = QLabel(self)
        self.lbl_gpu_detected.setObjectName('mutedText')
        self.lbl_gpu_detected.setWordWrap(True)
        self.lbl_gpu_detected.setText('Chế độ Tự động sẽ kiểm tra bộ mã hóa trong nền khi bắt đầu xuất.')
        advanced_grid.addWidget(self.lbl_gpu_detected, 3, 1)

        # 5. Quality Preset
        lbl_qual = QLabel("Chất lượng xuất:")
        lbl_qual.setStyleSheet("font-weight: 500;")
        self.combo_quality = ComboBox(self)
        self.combo_quality.addItem("Cao — cân bằng chất lượng và dung lượng", userData="high")
        self.combo_quality.addItem("Tiêu chuẩn — dung lượng nhỏ hơn", userData="standard")
        self.combo_quality.addItem("Rất cao — tệp lớn hơn", userData="very_high")
        grid.addWidget(lbl_qual, 1, 0)
        grid.addWidget(self.combo_quality, 1, 1)

        # Render speed is separate from the output's visual quality setting.
        self.combo_speed = ComboBox(self)
        for title,key in [('Nhanh — ưu tiên thời gian xuất','fast'),
                          ('Cân bằng — khuyên dùng','balanced'),
                          ('Chất lượng — nén kỹ hơn, xuất lâu hơn','quality')]:
            self.combo_speed.addItem(title,userData=key)
        idx=self.combo_speed.findData(settings.get('render_speed','balanced'))
        self.combo_speed.setCurrentIndex(idx if idx>=0 else 1)
        self.combo_speed.currentIndexChanged.connect(lambda: settings.set('render_speed',self.combo_speed.currentData()))
        grid.addWidget(QLabel('Tốc độ xử lý:'),2,0)
        grid.addWidget(self.combo_speed,2,1)
        self.combo_parallel = ComboBox(self)
        for title,value in [('Tự động theo bộ xử lý',0),('1 đoạn — ít tài nguyên hơn',1),('2 đoạn — xử lý song song',2)]:
            self.combo_parallel.addItem(title,userData=value)
        idx=self.combo_parallel.findData(int(settings.get('parallel_segments',0)))
        self.combo_parallel.setCurrentIndex(max(0,idx))
        self.combo_parallel.currentIndexChanged.connect(lambda: settings.set('parallel_segments',self.combo_parallel.currentData()))
        advanced_grid.addWidget(QLabel('Số đoạn xử lý cùng lúc:'),4,0)
        advanced_grid.addWidget(self.combo_parallel,4,1)
        self.chk_gpu_processing = QCheckBox('Tận dụng GPU khi xử lý video',self)
        self.chk_gpu_processing.setChecked(bool(settings.get('gpu_processing',True)))
        self.chk_gpu_processing.setToolTip('Áp dụng khi nguồn và GPU tương thích. Tự thử lại bằng cách thông thường nếu cần.')
        self.chk_gpu_processing.toggled.connect(lambda value: settings.set('gpu_processing',value))
        advanced_grid.addWidget(self.chk_gpu_processing,5,0,1,2)
        self.combo_decoder = ComboBox(self)
        for title,value in [('Tự động theo định dạng nguồn','auto'),('Ưu tiên GPU','gpu'),('CPU','cpu')]:
            self.combo_decoder.addItem(title,userData=value)
        idx=self.combo_decoder.findData(settings.get('decoder_mode','auto'))
        self.combo_decoder.setCurrentIndex(max(0,idx))
        self.combo_decoder.currentIndexChanged.connect(lambda: settings.set('decoder_mode',self.combo_decoder.currentData()))
        advanced_grid.addWidget(QLabel('Đọc video nguồn:'),6,0)
        advanced_grid.addWidget(self.combo_decoder,6,1)

        # 6. Audio Mode
        lbl_audio = QLabel("Cơ chế âm thanh:")
        lbl_audio.setStyleSheet("font-weight: 500;")
        self.combo_audio = ComboBox(self)
        self.combo_audio.addItem("Tắt tiếng video nguồn", userData="none")
        self.combo_audio.addItem("Giữ âm thanh gốc từ video", userData="keep")
        self.combo_audio.addItem("Chèn file nhạc nền BGM (MP3/WAV)", userData="bgm")
        self.combo_audio.currentIndexChanged.connect(self._on_audio_mode_changed)
        grid.addWidget(lbl_audio, 3, 0)
        grid.addWidget(self.combo_audio, 3, 1)

        self.video_options = QWidget(self)
        self.video_options.setLayout(grid)
        layout.addWidget(self.video_options)
        self.btn_advanced_video = QToolButton(self)
        self.btn_advanced_video.setText("Tỷ lệ khung hình, FPS và tăng tốc")
        self.btn_advanced_video.setCheckable(True)
        layout.addWidget(self.btn_advanced_video)
        self.advanced_video = QWidget(self)
        self.advanced_video.setLayout(advanced_grid)
        layout.addWidget(self.advanced_video)
        self.advanced_video.setVisible(False)
        self.btn_advanced_video.toggled.connect(self.advanced_video.setVisible)

        self.lbl_audio_status = QLabel(self)
        self.lbl_audio_status.setWordWrap(True)
        self.lbl_audio_status.setStyleSheet("color: #0078D4; font-size: 11px; font-weight: 500; margin-top: 2px;")
        layout.addWidget(self.lbl_audio_status)

        # BGM file selection row (hidden by default)
        self.bgm_widget = QWidget(self)
        bgm_l = QHBoxLayout(self.bgm_widget)
        bgm_l.setContentsMargins(0, 0, 0, 0)
        lbl_bgm_file = QLabel("File nhạc nền:")
        self.edit_bgm = LineEdit(self)
        self.edit_bgm.setPlaceholderText("Chọn file âm thanh MP3 hoặc WAV...")
        self.btn_browse_bgm = PushButton("Chọn file BGM...", self)
        self.btn_browse_bgm.clicked.connect(self._choose_bgm)
        bgm_l.addWidget(lbl_bgm_file)
        bgm_l.addWidget(self.edit_bgm, 1)
        bgm_l.addWidget(self.btn_browse_bgm)
        self.bgm_widget.setVisible(False)
        layout.addWidget(self.bgm_widget)

        # Output folder row
        out_lbl = QLabel("Thư mục lưu kết quả:")
        out_lbl.setStyleSheet("font-weight: 500;")
        layout.addWidget(out_lbl)

        out_row = QHBoxLayout()
        self.edit_outdir = LineEdit(self)
        default_export = os.path.join(os.path.expanduser("~"), "Videos", "RemixOutput")
        saved_outdir = settings.get("output_folder", "")
        self.edit_outdir.setText(saved_outdir if saved_outdir else default_export)
        self.edit_outdir.textChanged.connect(lambda t: settings.set("output_folder", t.strip()))

        self.btn_browse_out = PushButton("Chọn thư mục...", self)
        self.btn_browse_out.clicked.connect(self._choose_outdir)
        self.btn_open_out = PushButton("Mở thư mục", self)
        self.btn_open_out.clicked.connect(self._open_outdir)

        out_row.addWidget(self.edit_outdir, 1)
        out_row.addWidget(self.btn_browse_out)
        out_row.addWidget(self.btn_open_out)
        layout.addLayout(out_row)

        # Output prefix row
        from datetime import datetime
        cur_mmdd = datetime.now().strftime("%m%d")
        prefix_row = QHBoxLayout()
        lbl_prefix = QLabel("Quy tắc đặt tên file:")
        lbl_prefix.setStyleSheet("font-weight: 500;")
        self.edit_prefix = LineEdit(self)
        self.edit_prefix.setPlaceholderText(f"Mặc định theo ngày tháng: {cur_mmdd}.mp4, {cur_mmdd}(0).mp4...")
        self.edit_prefix.setClearButtonEnabled(True)
        saved_prefix = settings.get("output_prefix", "")
        if saved_prefix and saved_prefix != "Random_Output":
            self.edit_prefix.setText(saved_prefix)
        self.edit_prefix.textChanged.connect(lambda t: settings.set("output_prefix", t.strip()))
        prefix_row.addWidget(lbl_prefix)
        prefix_row.addWidget(self.edit_prefix, 1)
        layout.addLayout(prefix_row)

    def _on_audio_mode_changed(self):
        is_bgm = (self.combo_audio.currentData() == "bgm")
        self.bgm_widget.setVisible(is_bgm)

    def update_audio_mode_display(self, export_target: str):
        if export_target == "video_with_random_audio":
            self.lbl_audio_status.setText("Dùng nhạc từ thư mục âm thanh; âm thanh gốc trong video sẽ được bỏ qua.")
            self.lbl_audio_status.setStyleSheet("color: #0078D4; font-size: 11px; font-weight: 500; margin-top: 2px;")
            self.combo_audio.setEnabled(False)
            self.bgm_widget.setVisible(False)
        elif export_target == "audio_only":
            self.lbl_audio_status.setText("🎵 Chế độ: Chỉ xuất file âm thanh độc lập (không xuất video).")
            self.lbl_audio_status.setStyleSheet("color: #107C41; font-size: 11px; font-weight: 500; margin-top: 2px;")
            self.combo_audio.setEnabled(False)
            self.bgm_widget.setVisible(False)
        else:
            self.lbl_audio_status.setText("🎞 Xuất video theo cơ chế âm thanh bên trên:")
            self.lbl_audio_status.setStyleSheet("color: #555; font-size: 11px; font-weight: 500; margin-top: 2px;")
            self.combo_audio.setEnabled(True)
            self._on_audio_mode_changed()

    def _choose_bgm(self):
        f, _ = QFileDialog.getOpenFileName(
            self,
            "Chọn file nhạc nền",
            os.path.expanduser("~"),
            "Audio Files (*.mp3 *.wav *.aac *.m4a *.ogg *.flac);;All Files (*.*)"
        )
        if f:
            self.edit_bgm.setText(f)

    def _choose_outdir(self):
        curr = self.edit_outdir.text().strip()
        start = curr if os.path.isdir(curr) else os.path.expanduser("~")
        chosen = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu video", start)
        if chosen:
            self.edit_outdir.setText(chosen)

    def _open_outdir(self):
        d = self.edit_outdir.text().strip()
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        if os.name == 'nt':
            os.startfile(d)
        else:
            subprocess.run(["open" if os.name == "darwin" else "xdg-open", d])

    def get_config(self) -> OutputConfig:
        return OutputConfig(
            resolution=self.combo_res.currentData(),
            fps=self.combo_fps.currentData(),
            aspect_ratio_mode=self.combo_aspect.currentData(),
            hw_accel=self.combo_hw.currentData(),
            quality=self.combo_quality.currentData(),
            render_speed=self.combo_speed.currentData(),
            parallel_segments=self.combo_parallel.currentData(),
            gpu_processing=self.chk_gpu_processing.isChecked(),
            decoder_mode=self.combo_decoder.currentData(),
            audio_mode=self.combo_audio.currentData(),
            bgm_path=self.edit_bgm.text().strip(),
            output_dir=self.edit_outdir.text().strip(),
            output_prefix="" if self.edit_prefix.text().strip() == "Random_Output" else self.edit_prefix.text().strip()
        )

    def set_audio_only(self, audio_only: bool):
        self.video_options.setVisible(not audio_only)
        self.btn_advanced_video.setVisible(not audio_only)
        self.advanced_video.setVisible(not audio_only and self.btn_advanced_video.isChecked())
        self.lbl_audio_status.setVisible(not audio_only)
        if audio_only:
            self.bgm_widget.setVisible(False)
