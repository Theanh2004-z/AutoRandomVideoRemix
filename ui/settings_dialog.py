"""Application preferences kept outside the per-export workflow."""
import os
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTabWidget, QWidget,
    QScrollArea, QFrame, QCheckBox, QFileDialog,
)
from qfluentwidgets import PushButton, PrimaryPushButton, SpinBox, LineEdit
from core.settings_manager import settings
from core.app_paths import get_cache_dir
from ui.background import CacheWorker


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
        caption = QLabel('Các lựa chọn được lưu tự động cho những lần xuất sau.', self)
        caption.setObjectName('mutedText')
        layout.addWidget(caption)
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        self.add_tab(host.output_panel, 'Xuất tệp')
        self.add_tab(audio_card, 'Âm thanh')
        self.add_tab(host.cut_panel.detach_advanced_settings(), 'Ghép video')
        self._build_cache()
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
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(18)
        heading = QLabel('Bộ nhớ đệm khi xuất')
        heading.setObjectName('sectionHeader')
        layout.addWidget(heading)
        detail = QLabel('Giữ lại những đoạn đã xử lý để tăng tốc lần xuất tiếp theo. Dọn cache chỉ xóa các đoạn tạm do ứng dụng tạo; video nguồn và kết quả xuất được giữ nguyên.')
        detail.setWordWrap(True)
        detail.setObjectName('mutedText')
        layout.addWidget(detail)
        self.cache_enabled = QCheckBox('Dùng cache để tăng tốc các lần xuất sau',body)
        self.cache_enabled.setChecked(bool(settings.get('cache_enabled',True)))
        self.cache_enabled.toggled.connect(lambda value: settings.set('cache_enabled',value))
        layout.addWidget(self.cache_enabled)
        quota_row = QHBoxLayout()
        quota_row.addWidget(QLabel('Dung lượng tối đa'))
        self.cache_quota = SpinBox(body)
        self.cache_quota.setRange(1,500)
        self.cache_quota.setSuffix(' GB')
        self.cache_quota.setValue(int(settings.get('cache_max_gb',5)))
        self.cache_quota.valueChanged.connect(lambda value: settings.set('cache_max_gb',value))
        quota_row.addWidget(self.cache_quota)
        quota_row.addStretch()
        layout.addLayout(quota_row)
        layout.addWidget(QLabel('Thư mục cache'))
        path_row = QHBoxLayout()
        self.cache_path = LineEdit(body)
        self.cache_path.setReadOnly(True)
        self.cache_path.setText(settings.get('cache_folder','') or get_cache_dir())
        self.cache_browse = PushButton('Đổi thư mục…',body)
        self.cache_browse.clicked.connect(self.choose_cache_folder)
        path_row.addWidget(self.cache_path,1)
        path_row.addWidget(self.cache_browse)
        layout.addLayout(path_row)
        self.cache_stats = QLabel('Dung lượng sẽ được kiểm tra khi mở cài đặt.',body)
        self.cache_stats.setWordWrap(True)
        self.cache_stats.setObjectName('mutedText')
        layout.addWidget(self.cache_stats)
        self.cache_clear = PushButton('Dọn cache',body)
        self.cache_clear.clicked.connect(lambda: self.inspect_cache(clear=True))
        layout.addWidget(self.cache_clear,0,Qt.AlignLeft)
        layout.addStretch()
        self.add_tab(body,'Cache')

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
