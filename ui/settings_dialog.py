"""Application preferences kept outside the per-export workflow."""
import os
from PySide6.QtCore import Qt, Signal, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QWidget,
    QScrollArea, QFrame, QCheckBox, QFileDialog,
)
from qfluentwidgets import PushButton, PrimaryPushButton, SpinBox, LineEdit
from core.settings_manager import settings
from core.app_paths import get_cache_dir
from core.version import APP_NAME, APP_VERSION, RELEASE_CHANNEL, GITHUB_REPO, GITHUB_RELEASES_URL
from core.update_manager import update_manager
from ui.background import CacheWorker
from ui.update_dialog import UpdateDialog


class SettingsDialog(QDialog):
    cache_busy_changed = Signal(bool)

    def __init__(self, host, audio_card):
        super().__init__(host)
        self.setWindowTitle('Cài đặt · Remix')
        self.resize(860, 700)
        self.setMinimumSize(680, 500)
        self.setSizeGripEnabled(True)
        self.host = host
        self.cache_worker = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(16)
        title = QLabel('Cài đặt', self)
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        caption = QLabel('Tự động ghi nhớ thiết lập, kể cả khi nâng cấp ứng dụng.', self)
        caption.setObjectName('mutedText')
        layout.addWidget(caption)
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        self.add_tab(host.output_panel, 'Xuất tệp')
        self.add_tab(audio_card, 'Âm thanh')
        self.add_tab(host.cut_panel.detach_advanced_settings(), 'Ghép video')
        host.cut_panel.fill_frame_changed.connect(
            lambda checked: host.output_panel.set_aspect_mode("crop" if checked else "fit")
        )
        self._build_cache()
        self._build_about()
        footer = QHBoxLayout()
        hint = QLabel('Áp dụng ngay · Không cần khởi động lại')
        hint.setObjectName('mutedText')
        footer.addWidget(hint, 1)
        close = PrimaryPushButton('Xong', self)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

    def add_tab(self, widget, title):
        scroll = QScrollArea(self.tabs)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        body.setObjectName('settingsBody')
        contents = QVBoxLayout(body)
        contents.setContentsMargins(0, 16, 0, 8)
        contents.addWidget(widget)
        contents.addStretch()
        scroll.setWidget(body)
        self.tabs.addTab(scroll, title)

    def _build_cache(self):
        from core.app_paths import get_scratch_dir, get_default_scratch_dir
        from core.disk_estimator import DiskEstimator, format_bytes

        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(16)

        # -------------------------------------------------------------
        # 1. THƯ MỤC XỬ LÝ TẠM (SCRATCH)
        # -------------------------------------------------------------
        scratch_heading = QLabel('1. Thư mục xử lý tạm (Khuyên dùng SSD)', body)
        scratch_heading.setObjectName('sectionHeader')
        layout.addWidget(scratch_heading)

        scratch_detail = QLabel(
            'Chứa các đoạn video và âm thanh trung gian trong lượt đang chạy. Tự động xóa ngay khi video hoàn tất.\n'
            'Khuyên dùng ổ đĩa tốc độ cao (SSD như C:) để tránh hiện tượng nghẽn đọc/ghi (disk thrashing) trên HDD.',
            body
        )
        scratch_detail.setWordWrap(True)
        scratch_detail.setObjectName('mutedText')
        layout.addWidget(scratch_detail)

        scratch_row = QHBoxLayout()
        self.scratch_path = LineEdit(body)
        self.scratch_path.setReadOnly(True)
        self.scratch_path.setText(settings.get('scratch_folder', '') or get_scratch_dir())
        self.scratch_browse = PushButton('Chọn thư mục…', body)
        self.scratch_browse.clicked.connect(self.choose_scratch_folder)
        self.scratch_default_btn = PushButton('Mặc định (SSD C:)', body)
        self.scratch_default_btn.clicked.connect(self.reset_scratch_folder)

        scratch_row.addWidget(self.scratch_path, 1)
        scratch_row.addWidget(self.scratch_browse)
        scratch_row.addWidget(self.scratch_default_btn)
        layout.addLayout(scratch_row)

        self.scratch_info = QLabel('', body)
        self.scratch_info.setObjectName('mutedText')
        layout.addWidget(self.scratch_info)

        # Divider
        divider = QFrame(body)
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)

        # -------------------------------------------------------------
        # 2. BỘ NHỚ ĐỆM (CACHE)
        # -------------------------------------------------------------
        cache_heading = QLabel('2. Bộ nhớ đệm tái sử dụng (Cache)', body)
        cache_heading.setObjectName('sectionHeader')
        layout.addWidget(cache_heading)

        detail = QLabel(
            'Giữ lại những đoạn đã xử lý để tăng tốc các lần xuất tiếp theo. '
            'Dọn cache chỉ xóa các đoạn trong cache; video nguồn và kết quả xuất được giữ nguyên.',
            body
        )
        detail.setWordWrap(True)
        detail.setObjectName('mutedText')
        layout.addWidget(detail)

        self.cache_enabled = QCheckBox('Dùng cache để tăng tốc các lần xuất sau', body)
        self.cache_enabled.setChecked(bool(settings.get('cache_enabled', True)))
        self.cache_enabled.toggled.connect(lambda value: settings.set('cache_enabled', value))
        layout.addWidget(self.cache_enabled)

        quota_row = QHBoxLayout()
        quota_row.addWidget(QLabel('Dung lượng tối đa'))
        self.cache_quota = SpinBox(body)
        self.cache_quota.setRange(1, 500)
        self.cache_quota.setSuffix(' GB')
        self.cache_quota.setValue(int(settings.get('cache_max_gb', 5)))
        self.cache_quota.valueChanged.connect(lambda value: settings.set('cache_max_gb', value))
        quota_row.addWidget(self.cache_quota)
        quota_row.addStretch()
        layout.addLayout(quota_row)

        layout.addWidget(QLabel('Thư mục cache'))
        path_row = QHBoxLayout()
        self.cache_path = LineEdit(body)
        self.cache_path.setReadOnly(True)
        self.cache_path.setText(settings.get('cache_folder', '') or get_cache_dir())
        self.cache_browse = PushButton('Đổi thư mục…', body)
        self.cache_browse.clicked.connect(self.choose_cache_folder)
        path_row.addWidget(self.cache_path, 1)
        path_row.addWidget(self.cache_browse)
        layout.addLayout(path_row)

        self.cache_stats = QLabel('Dung lượng sẽ được kiểm tra khi mở cài đặt.', body)
        self.cache_stats.setWordWrap(True)
        self.cache_stats.setObjectName('mutedText')
        layout.addWidget(self.cache_stats)

        self.cache_clear = PushButton('Dọn cache', body)
        self.cache_clear.clicked.connect(lambda: self.inspect_cache(clear=True))
        layout.addWidget(self.cache_clear, 0, Qt.AlignLeft)

        layout.addWidget(QLabel('Tự dọn dung lượng', body))
        self.auto_cleanup = QCheckBox('Tự dọn cache và tệp tạm bỏ lại', body)
        self.auto_cleanup.setChecked(bool(settings.get('auto_cleanup', True)))
        self.auto_cleanup.toggled.connect(lambda value: settings.set('auto_cleanup', value))
        layout.addWidget(self.auto_cleanup)
        self.temp_retention = SpinBox(body)
        self.temp_retention.setRange(1, 720)
        self.temp_retention.setSuffix(' giờ')
        self.temp_retention.setValue(int(settings.get('temp_retention_hours', 24)))
        self.temp_retention.valueChanged.connect(lambda value: settings.set('temp_retention_hours', value))
        retention_row = QHBoxLayout()
        retention_row.addWidget(QLabel('Dọn tệp tạm bỏ lại quá:', body))
        retention_row.addWidget(self.temp_retention)
        layout.addLayout(retention_row)
        self.min_free = SpinBox(body)
        self.min_free.setRange(1, 100)
        self.min_free.setSuffix(' GB')
        self.min_free.setValue(int(settings.get('storage_min_free_gb', 2)))
        self.min_free.valueChanged.connect(lambda value: settings.set('storage_min_free_gb', value))
        free_row = QHBoxLayout()
        free_row.addWidget(QLabel('Giữ trống tối thiểu trên ổ xử lý và ổ kết quả:', body))
        free_row.addWidget(self.min_free)
        layout.addLayout(free_row)
        note = QLabel('Tự dọn khi mở ứng dụng và mỗi 30 phút lúc rảnh. Chỉ dọn dữ liệu do ứng dụng tạo. Bỏ qua lượt đang chạy và thư mục không có dấu nhận diện. Khi ổ sắp đầy, cache cũ được dọn trước; nếu vẫn thiếu chỗ, lượt xuất sẽ dừng và dọn tệp tạm.', body)
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        self.add_tab(body, 'Lưu trữ & Cache')

    def _build_about(self):
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(16)

        # -------------------------------------------------------------
        # 1. THÔNG TIN ỨNG DỤNG
        # -------------------------------------------------------------
        info_heading = QLabel('1. Thông tin phiên bản', body)
        info_heading.setObjectName('sectionHeader')
        layout.addWidget(info_heading)

        app_info = QLabel(
            f"<b>{APP_NAME}</b> — Phiên bản <b>v{APP_VERSION}</b> ({RELEASE_CHANNEL.capitalize()})",
            body
        )
        app_info.setStyleSheet("font-size: 14px;")
        layout.addWidget(app_info)

        repo_row = QHBoxLayout()
        repo_lbl = QLabel(f"Kho lưu trữ GitHub: {GITHUB_REPO}", body)
        repo_lbl.setObjectName('mutedText')
        repo_row.addWidget(repo_lbl)
        btn_github = PushButton('Mở GitHub', body)
        btn_github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_RELEASES_URL)))
        repo_row.addWidget(btn_github)
        repo_row.addStretch()
        layout.addLayout(repo_row)

        # -------------------------------------------------------------
        # 2. CẬP NHẬT ỨNG DỤNG
        # -------------------------------------------------------------
        update_heading = QLabel('2. Cập nhật ứng dụng', body)
        update_heading.setObjectName('sectionHeader')
        layout.addWidget(update_heading)

        self.about_chk_startup = QCheckBox('Kiểm tra bản mới khi mở ứng dụng', body)
        self.about_chk_startup.setChecked(bool(settings.get('check_updates_on_startup', True)))
        self.about_chk_startup.toggled.connect(
            lambda val: settings.update({'check_updates_on_startup': val}, auto_save=True)
        )
        layout.addWidget(self.about_chk_startup)

        last_check = settings.get('last_update_check', '') or 'Chưa kiểm tra'
        self.about_status_lbl = QLabel(f"Trạng thái: Đang chạy v{APP_VERSION} · Lần kiểm tra gần nhất: {last_check}", body)
        self.about_status_lbl.setObjectName('mutedText')
        layout.addWidget(self.about_status_lbl)

        btn_row = QHBoxLayout()
        self.about_btn_check = PushButton('Kiểm tra cập nhật', body)
        self.about_btn_check.clicked.connect(self._on_check_update_clicked)
        btn_row.addWidget(self.about_btn_check)

        self.about_btn_update = PrimaryPushButton('Tự động cập nhật', body)
        self.about_btn_update.setEnabled(False)
        self.about_btn_update.clicked.connect(self._on_auto_update_clicked)
        btn_row.addWidget(self.about_btn_update)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Check if there is already a staged pending update waiting to be applied
        pending = update_manager.check_pending_update()
        if pending:
            new_v = pending.get("new_version", "")
            self.about_status_lbl.setText(f"📦 Đã tải sẵn bản cập nhật v{new_v}. Bấm 'Tự động cập nhật' để cài đặt.")
            self.about_btn_update.setEnabled(True)

        layout.addStretch()
        self.add_tab(body, 'Giới thiệu')

    def _on_check_update_clicked(self):
        self.about_btn_check.setEnabled(False)
        self.about_status_lbl.setText("Đang kiểm tra bản cập nhật mới trên GitHub...")
        update_manager.check_finished.connect(self._on_settings_update_check_finished)
        update_manager.check_updates_async()

    def _on_settings_update_check_finished(self, is_available, info_or_err):
        try:
            update_manager.check_finished.disconnect(self._on_settings_update_check_finished)
        except Exception:
            pass
        self.about_btn_check.setEnabled(True)
        last_check = settings.get('last_update_check', '')
        if is_available and hasattr(info_or_err, 'version'):
            self.about_status_lbl.setText(f"🎉 Đã có bản mới: v{info_or_err.version} (Hiện tại: v{APP_VERSION}) · {last_check}")
            self.about_btn_update.setEnabled(True)
            dlg = UpdateDialog(self, release_info=info_or_err)
            dlg.exec()
        elif not is_available and hasattr(info_or_err, 'version'):
            self.about_status_lbl.setText(f"✅ Bạn đang dùng phiên bản mới nhất v{APP_VERSION} · {last_check}")
            self.about_btn_update.setEnabled(False)
        else:
            self.about_status_lbl.setText(f"⚠️ {str(info_or_err)} · {last_check}")
            self.about_btn_update.setEnabled(False)

    def _on_auto_update_clicked(self):
        dlg = UpdateDialog(self)
        dlg.exec()

    def _update_scratch_free_space(self):
        try:
            path = self.scratch_path.text()
            from core.disk_estimator import DiskEstimator, format_bytes
            free_b = DiskEstimator._get_available_bytes(path)
            drive = os.path.splitdrive(os.path.abspath(path))[0] or path
            self.scratch_info.setText(f"💾 Khả dụng trên ổ ({drive}): còn trống {format_bytes(free_b)}.")
        except Exception:
            self.scratch_info.setText("")

    def reset_scratch_folder(self):
        from core.app_paths import get_default_scratch_dir
        default_dir = get_default_scratch_dir()
        self.scratch_path.setText(default_dir)
        settings.set('scratch_folder', '')
        self._update_scratch_free_space()

    def choose_scratch_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Chọn thư mục xử lý tạm (ưu tiên SSD)', self.scratch_path.text())
        if folder:
            path = os.path.join(folder, 'AutoRandomVideoRemix_scratch')
            os.makedirs(path, exist_ok=True)
            self.scratch_path.setText(path)
            settings.set('scratch_folder', path)
            self._update_scratch_free_space()

    @property
    def cache_busy(self):
        return self.cache_worker is not None

    def choose_cache_folder(self):
        folder = QFileDialog.getExistingDirectory(self,'Chọn nơi chứa cache',self.cache_path.text())
        if folder:
            # Own subdirectory prevents mixing cache entries with user media.
            path = os.path.join(folder,'AutoRandomVideoRemixCache')
            self.cache_path.setText(path)
            settings.set('cache_folder',path)
            self.inspect_cache()

    def inspect_cache(self, clear=False):
        if self.cache_worker or self.host.workflow._running:
            return
        self.cache_stats.setText('Đang dọn cache…' if clear else 'Đang kiểm tra dung lượng cache…')
        self.cache_browse.setEnabled(False)
        self.cache_clear.setEnabled(False)
        self.cache_worker = CacheWorker(self.cache_path.text(),clear,self)
        self.cache_worker.result.connect(self.cache_result)
        self.cache_worker.failed.connect(self.cache_error)
        self.cache_worker.finished.connect(self.cache_finished)
        self.cache_busy_changed.emit(True)
        self.cache_worker.start()

    def cache_result(self,result):
        mb = result['total_bytes']/1024/1024
        text = f"{result['file_count']} đoạn tạm · {mb:.1f} MB đang sử dụng"
        if result['deleted']:
            text = f"Đã dọn {result['deleted']} đoạn tạm."
        self.cache_stats.setText(text)

    def cache_error(self,message):
        self.cache_stats.setText('Không thể xử lý cache: '+message)

    def cache_finished(self):
        worker, self.cache_worker = self.cache_worker, None
        if worker:
            worker.deleteLater()
        self.cache_browse.setEnabled(True)
        self.cache_clear.setEnabled(True)
        self.cache_busy_changed.emit(False)

    def showEvent(self,event):
        super().showEvent(event)
        self.inspect_cache()
        self._update_scratch_free_space()
        current_aspect = self.host.output_panel.get_config().aspect_ratio_mode
        self.host.cut_panel.set_fill_frame(current_aspect == "crop")
