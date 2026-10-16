"""
Update Dialog Module.
Displays release notes, download progress, and restart/install prompts.
Respects active exports and source/development environments.
"""
import os
import webbrowser
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QWidget, QFrame, QCheckBox
)
from qfluentwidgets import (
    PushButton, PrimaryPushButton, ProgressBar, InfoBar, InfoBarPosition
)

from core.settings_manager import settings
from core.update_manager import (
    STATE_DOWNLOADING, STATE_CANCELLING, STATE_CANCELLED,
    STATE_ERROR, STATE_EXTRACTING, STATE_INSTALLING,
    STATE_READY_TO_INSTALL, STATE_UPDATE_AVAILABLE,
    update_manager
)
from core.update_service import ReleaseInfo
from core.version import APP_NAME, APP_VERSION, GITHUB_RELEASES_URL


class UpdateDialog(QDialog):
    """
    Modal or modeless dialog notifying user of new version, showing changelog,
    tracking download/extraction progress, and offering atomic restart.
    """
    def __init__(self, parent=None, release_info: Optional[ReleaseInfo] = None):
        super().__init__(parent)
        self.setWindowTitle(f"Cập nhật {APP_NAME}")
        self.resize(580, 520)
        self.setMinimumSize(480, 400)

        self._release = release_info or update_manager.current_release
        self._is_dev_mode = update_manager.is_running_from_source()

        self._init_ui()
        self._connect_signals()

        if self._release:
            self._display_release(self._release)

        # If already staged and ready to install, display ready state immediately
        if update_manager.state == STATE_READY_TO_INSTALL and update_manager._install_plan:
            self._on_ready_to_install(update_manager._install_plan)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Title / Header
        title_row = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(4)

        self.lbl_title = QLabel(f"Có bản cập nhật mới cho {APP_NAME}", self)
        self.lbl_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        title_col.addWidget(self.lbl_title)

        self.lbl_versions = QLabel(f"Phiên bản hiện tại: v{APP_VERSION}", self)
        self.lbl_versions.setStyleSheet("color: #888888; font-size: 12px;")
        title_col.addWidget(self.lbl_versions)
        title_row.addLayout(title_col, 1)

        layout.addLayout(title_row)

        # Dev mode banner
        if self._is_dev_mode:
            dev_frame = QFrame(self)
            dev_frame.setStyleSheet(
                "background-color: rgba(255, 165, 0, 0.15); border: 1px solid rgba(255, 165, 0, 0.4); "
                "border-radius: 6px; padding: 8px;"
            )
            dev_layout = QVBoxLayout(dev_frame)
            dev_layout.setContentsMargins(8, 6, 8, 6)
            dev_label = QLabel(
                "💡 <b>Chế độ nhà phát triển (Mã nguồn):</b> Ứng dụng đang chạy trực tiếp từ mã nguồn python. "
                "Chức năng tự động ghi đè tệp chỉ áp dụng cho bản phát hành chính thức (PyInstaller). "
                "Bạn có thể cập nhật mã nguồn bằng <code>git pull</code>.",
                dev_frame
            )
            dev_label.setWordWrap(True)
            dev_label.setStyleSheet("color: #e09000; font-size: 12px;")
            dev_layout.addWidget(dev_label)
            layout.addWidget(dev_frame)

        # Changelog / Release notes
        lbl_notes = QLabel("Nội dung cập nhật:", self)
        lbl_notes.setStyleSheet("font-weight: 600; font-size: 13px;")
        layout.addWidget(lbl_notes)

        self.txt_changelog = QTextBrowser(self)
        self.txt_changelog.setOpenExternalLinks(True)
        self.txt_changelog.setStyleSheet(
            "background-color: rgba(128, 128, 128, 0.08); border: 1px solid rgba(128, 128, 128, 0.2); "
            "border-radius: 6px; padding: 8px; font-size: 12px; line-height: 1.4;"
        )
        layout.addWidget(self.txt_changelog, 1)

        # Startup check setting checkbox
        self.chk_startup = QCheckBox("Tự động kiểm tra bản cập nhật khi mở ứng dụng", self)
        self.chk_startup.setChecked(bool(settings.get("check_updates_on_startup", True)))
        self.chk_startup.toggled.connect(lambda val: settings.update({"check_updates_on_startup": val}, auto_save=True))
        layout.addWidget(self.chk_startup)

        # Progress Section (Hidden initially)
        self.progress_container = QWidget(self)
        prog_layout = QVBoxLayout(self.progress_container)
        prog_layout.setContentsMargins(0, 4, 0, 4)
        prog_layout.setSpacing(6)

        self.lbl_status = QLabel("Sẵn sàng.", self.progress_container)
        self.lbl_status.setStyleSheet("font-size: 12px; color: #aaaaaa;")
        prog_layout.addWidget(self.lbl_status)

        self.prog_bar = ProgressBar(self.progress_container)
        self.prog_bar.setRange(0, 100)
        self.prog_bar.setValue(0)
        prog_layout.addWidget(self.prog_bar)

        self.progress_container.setVisible(False)
        layout.addWidget(self.progress_container)

        # Footer Buttons
        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.btn_github = PushButton("Xem trên GitHub", self)
        self.btn_github.clicked.connect(self._open_github)
        footer.addWidget(self.btn_github)

        footer.addStretch(1)

        self.btn_cancel = PushButton("Hủy", self)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_cancel.setVisible(False)
        footer.addWidget(self.btn_cancel)

        self.btn_dismiss = PushButton("Để sau", self)
        self.btn_dismiss.clicked.connect(self.reject)
        footer.addWidget(self.btn_dismiss)

        self.btn_action = PrimaryPushButton("Tải và cập nhật", self)
        self.btn_action.clicked.connect(self._on_action_clicked)
        footer.addWidget(self.btn_action)

        layout.addLayout(footer)

    def _display_release(self, rel: ReleaseInfo):
        self._release = rel
        self.lbl_title.setText(f"Có bản cập nhật mới: v{rel.version}")
        self.lbl_versions.setText(
            f"Đang dùng: v{APP_VERSION} ➔ Mới nhất: v{rel.version} "
            f"({rel.published_at[:10] if rel.published_at else ''})"
        )

        # Format release notes into clean HTML
        notes = rel.body or "Không có ghi chú phát hành."
        html_notes = notes.replace("\r\n", "\n").replace("\n", "<br/>")
        self.txt_changelog.setHtml(f"<div style='font-family: Segoe UI, sans-serif;'>{html_notes}</div>")

        if self._is_dev_mode:
            self.btn_action.setText("Tải gói ZIP")
        else:
            self.btn_action.setText("Tải và cập nhật")

    def _connect_signals(self):
        update_manager.state_changed.connect(self._on_state_changed)
        update_manager.download_progress.connect(self._on_download_progress)
        update_manager.extract_progress.connect(self._on_extract_progress)
        update_manager.ready_to_install.connect(self._on_ready_to_install)
        update_manager.error_occurred.connect(self._on_error)

    def _open_github(self):
        url = self._release.html_url if self._release and self._release.html_url else GITHUB_RELEASES_URL
        QDesktopServices.openUrl(QUrl(url))

    def _on_action_clicked(self):
        if update_manager.state == STATE_READY_TO_INSTALL:
            # User wants to restart and apply
            if update_manager.is_export_busy():
                InfoBar.warning(
                    title="Đang xuất video",
                    content="Vui lòng chờ lượt xuất hoàn tất trước khi khởi động lại.",
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=4000
                )
                return
            if update_manager.apply_update_and_restart():
                self.accept()
            return

        # Start download
        if update_manager.is_export_busy():
            InfoBar.warning(
                title="Đang xuất video",
                content="Đang xuất video. Quá trình tải bản cập nhật sẽ diễn ra ở chế độ nền nhẹ.",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000
            )

        self.progress_container.setVisible(True)
        self.btn_action.setEnabled(False)
        self.btn_dismiss.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.lbl_status.setText("Đang khởi tạo tiến trình tải...")
        update_manager.download_update_async(self._release)

    def _on_cancel(self):
        self.btn_cancel.setEnabled(False)
        self.btn_action.setEnabled(False)
        self.btn_dismiss.setEnabled(False)
        self.lbl_status.setText("Đang hủy tải và dọn dẹp tệp tạm...")
        update_manager.cancel_operation()

    def _on_state_changed(self, state: str, message: str):
        self.lbl_status.setText(message)
        if state == STATE_DOWNLOADING:
            self.progress_container.setVisible(True)
            self.btn_cancel.setVisible(True)
            self.btn_cancel.setEnabled(True)
            self.btn_dismiss.setVisible(False)
            self.btn_action.setEnabled(False)
        elif state == STATE_CANCELLING:
            self.btn_cancel.setEnabled(False)
            self.btn_action.setEnabled(False)
            self.btn_dismiss.setEnabled(False)
        elif state == STATE_CANCELLED:
            self.btn_cancel.setVisible(False)
            self.btn_cancel.setEnabled(True)
            self.btn_dismiss.setVisible(True)
            self.btn_dismiss.setEnabled(True)
            self.btn_action.setEnabled(True)
            self.btn_action.setText("Tải lại")
        elif state == STATE_EXTRACTING:
            self.btn_cancel.setVisible(False)
            self.lbl_status.setText("Đang giải nén và kiểm tra tệp...")
        elif state == STATE_ERROR:
            self.btn_cancel.setVisible(False)
            self.btn_dismiss.setVisible(True)
            self.btn_dismiss.setEnabled(True)
            self.btn_action.setEnabled(True)
            self.btn_action.setText("Thử lại")

    def _on_download_progress(self, downloaded: int, total: int, percent: float):
        self.prog_bar.setValue(int(percent))
        dl_mb = downloaded / (1024 * 1024)
        tot_mb = total / (1024 * 1024) if total > 0 else 0
        if tot_mb > 0:
            self.lbl_status.setText(f"Đang tải: {dl_mb:.1f} MB / {tot_mb:.1f} MB ({percent:.1f}%)")
        else:
            self.lbl_status.setText(f"Đang tải: {dl_mb:.1f} MB...")

    def _on_extract_progress(self, current: int, total: int):
        pct = (current / total * 100.0) if total > 0 else 0
        self.prog_bar.setValue(int(pct))
        self.lbl_status.setText(f"Đang giải nén: {current}/{total} tệp ({pct:.0f}%)")

    def _on_ready_to_install(self, plan: dict):
        self.progress_container.setVisible(True)
        self.prog_bar.setValue(100)
        self.btn_cancel.setVisible(False)
        self.btn_dismiss.setVisible(True)
        self.btn_dismiss.setText("Để sau (cài khi mở lại)")
        self.btn_action.setEnabled(True)

        if self._is_dev_mode:
            self.lbl_status.setText(
                "Đã tải và giải nén thành công vào thư mục staging.\n"
                "(Mã nguồn không tự động ghi đè)."
            )
            self.btn_action.setText("Đã xong")
            self.btn_action.clicked.disconnect()
            self.btn_action.clicked.connect(self.accept)
        else:
            self.lbl_status.setText("Bản cập nhật đã sẵn sàng! Bấm 'Khởi động lại ngay' để áp dụng.")
            self.btn_action.setText("Khởi động lại ngay")

    def _on_error(self, message: str):
        self.lbl_status.setText(f"Lỗi: {message}")
        self.btn_cancel.setVisible(False)
        self.btn_dismiss.setVisible(True)
        self.btn_action.setEnabled(True)
        self.btn_action.setText("Thử lại")
