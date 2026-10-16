"""Debounced scanning with cancellation and stale-result protection."""
import os
from PySide6.QtCore import QObject, QTimer
from ui.background import MediaScanWorker


class AutoScanController(QObject):
    def __init__(self, panel, media_type):
        super().__init__(panel)
        self.panel, self.media_type = panel, media_type
        self.pending = False
        self.enabled = True
        self.stopping = False
        self.generation = 0
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(550)
        self.timer.timeout.connect(self.start)
        panel.folder_edit.textChanged.connect(self.schedule)
        panel.chk_recursive.toggled.connect(self.schedule)
        if panel.folder_edit.text().strip():
            self.timer.start()

    def schedule(self, *args):
        if self.stopping or not self.enabled:
            return
        self.generation += 1
        worker = self.panel.scanner_thread
        if worker and worker.isRunning():
            worker.cancel()
        self.timer.start()
        self.panel.scan_status_lbl.setVisible(True)
        self.panel.scan_status_lbl.setText('Sẽ tự kiểm tra thư mục…' if self.panel.folder_edit.text().strip() else 'Chọn thư mục để tự kiểm tra.')

    def start(self):
        if self.stopping or not self.enabled:
            return
        self.timer.stop()
        panel = self.panel
        if panel.scanner_thread is not None:
            self.pending = True
            panel.scanner_thread.cancel()
            return
        folder = panel.folder_edit.text().strip()
        if not os.path.isdir(folder):
            panel.scan_status_lbl.setVisible(True)
            panel.scan_status_lbl.setText('Thư mục chưa tồn tại.' if folder else 'Chọn thư mục để bắt đầu.')
            return
        self.pending = False
        panel.scan_started.emit()
        panel.btn_scan.setEnabled(False)
        panel.scan_bar.setVisible(True)
        panel.scan_bar.setValue(0)
        panel.scan_status_lbl.setVisible(True)
        panel.scan_status_lbl.setText('Đang kiểm tra ngầm… Bạn có thể tiếp tục thiết lập.')
        worker = MediaScanWorker(folder, panel.chk_recursive.isChecked(), self.media_type, panel)
        worker.generation = self.generation
        panel.scanner_thread = worker
        worker.progress.connect(self.progress)
        worker.scanned.connect(self.accept)
        worker.failed.connect(self.failed)
        worker.finished.connect(self.finished)
        panel.scan_state_changed.emit(True)
        worker.start()

    def current(self, worker):
        return (worker is self.panel.scanner_thread and worker.generation == self.generation
                and worker.folder == self.panel.folder_edit.text().strip()
                and worker.recursive == self.panel.chk_recursive.isChecked() and not self.stopping and self.enabled)

    def progress(self, current, total, name):
        if self.current(self.sender()):
            self.panel._on_scan_progress(current,total,name)

    def accept(self, valid, invalid):
        if self.current(self.sender()):
            self.panel._on_scan_finished(valid,invalid)

    def failed(self, message):
        if self.current(self.sender()):
            self.panel.scan_status_lbl.setText('Không thể kiểm tra thư mục: '+message)

    def finished(self):
        panel, worker = self.panel, self.sender()
        if panel.scanner_thread is worker:
            panel.scanner_thread = None
            panel.scan_bar.setVisible(False)
            panel.btn_scan.setEnabled(self.enabled)
            panel.scan_state_changed.emit(False)
        worker.deleteLater()
        if self.pending and not self.stopping and self.enabled:
            self.pending = False
            self.timer.start(0)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.stopping or self.enabled == enabled:
            return
        self.enabled = enabled
        self.panel.btn_scan.setEnabled(enabled)
        if not enabled:
            self.timer.stop()
            self.pending = False
            self.generation += 1
            if self.panel.scanner_thread:
                self.panel.scanner_thread.cancel()
            self.panel.scan_bar.hide()
        elif self.panel.scanner_thread:
            self.pending = True
            self.panel.scanner_thread.cancel()
        elif not (self.panel.valid_videos if self.media_type == "video" else self.panel.valid_audios):
            self.schedule()

    def stop(self):
        self.stopping = True
        self.timer.stop()
        if self.panel.scanner_thread:
            self.panel.scanner_thread.cancel()
