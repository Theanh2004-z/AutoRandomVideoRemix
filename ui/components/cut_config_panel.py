"""
Cut Configuration Panel (Khu vực B) — refactored for sequential-slice pipeline.

Replaces the old "random position" controls with the new options:
  • segment_duration     — how long each slice is
  • merge_remainder      — fold the trailing stub into the preceding segment
  • allow_reuse          — draw with replacement when pool is exhausted
  • no_repeat_across_batch   — never reuse a segment across batch outputs
  • no_consecutive_same_source — avoid two adjacent clips from the same file
  • trim_last_segment    — trim last clip so output matches target exactly
"""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QCheckBox
)
from qfluentwidgets import (
    CardWidget, SpinBox, DoubleSpinBox, PushButton
)
from core.config import CutConfig
from core.settings_manager import settings


class CutConfigPanel(CardWidget):
    """Area B: Segmentation & Randomisation Configuration."""
    config_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QLabel("KHU VỰC B: CẤU HÌNH CẮT & THUẬT TOÁN")
        title.setObjectName("sectionHeader")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(10)

        # ── 1. Segment duration ──────────────────────────────────────
        lbl_seg = QLabel("Thời lượng mỗi đoạn:")
        lbl_seg.setStyleSheet("font-weight: 500;")
        self.spin_seg_dur = DoubleSpinBox(self)
        self.spin_seg_dur.setRange(1.0, 3600.0)
        self.spin_seg_dur.setValue(float(settings.get("segment_duration", 10.0)))
        self.spin_seg_dur.setSingleStep(1.0)
        self.spin_seg_dur.setSuffix(" giây")
        self.spin_seg_dur.valueChanged.connect(self._update_summary)
        grid.addWidget(lbl_seg, 0, 0)
        grid.addWidget(self.spin_seg_dur, 0, 1)

        # ── 2. Total output duration (H / M / S) ─────────────────────
        lbl_tot = QLabel("Tổng thời lượng video xuất:")
        lbl_tot.setStyleSheet("font-weight: 500;")

        time_box = QHBoxLayout()
        time_box.setSpacing(6)

        self.spin_hours = SpinBox(self)
        self.spin_hours.setRange(0, 48)
        self.spin_hours.setValue(int(settings.get("target_hours", 0)))
        self.spin_hours.setSuffix(" giờ")
        self.spin_hours.valueChanged.connect(self._update_summary)

        self.spin_mins = SpinBox(self)
        self.spin_mins.setRange(0, 59)
        self.spin_mins.setValue(int(settings.get("target_mins", 30)))
        self.spin_mins.setSuffix(" phút")
        self.spin_mins.valueChanged.connect(self._update_summary)

        self.spin_secs = SpinBox(self)
        self.spin_secs.setRange(0, 59)
        self.spin_secs.setValue(int(settings.get("target_secs", 0)))
        self.spin_secs.setSuffix(" giây")
        self.spin_secs.valueChanged.connect(self._update_summary)

        time_box.addWidget(self.spin_hours)
        time_box.addWidget(self.spin_mins)
        time_box.addWidget(self.spin_secs)

        grid.addWidget(lbl_tot, 1, 0)
        grid.addLayout(time_box, 1, 1)

        # ── 3. Summary label ──────────────────────────────────────────
        self.lbl_summary = QLabel("-", self)
        self.lbl_summary.setStyleSheet("color: #0078D4; font-weight: 600;")
        grid.addWidget(self.lbl_summary, 2, 0, 1, 2)

        # ── 4. Batch count ────────────────────────────────────────────
        lbl_batch = QLabel("Số lượng video muốn tạo:")
        lbl_batch.setStyleSheet("font-weight: 500;")
        self.spin_batch_count = SpinBox(self)
        self.spin_batch_count.setRange(1, 100)
        self.spin_batch_count.setValue(int(settings.get("batch_count", 1)))
        self.spin_batch_count.setSuffix(" video")
        self.spin_batch_count.valueChanged.connect(self._update_summary)
        grid.addWidget(lbl_batch, 3, 0)
        grid.addWidget(self.spin_batch_count, 3, 1)

        layout.addLayout(grid)

        # ── Separator label ───────────────────────────────────────────
        sep = QLabel("Tùy chọn chia đoạn & xáo trộn:")
        sep.setStyleSheet("font-weight: 600; color: #444; margin-top: 6px;")
        layout.addWidget(sep)

        # ── 5. Checkboxes ─────────────────────────────────────────────
        chk_layout = QVBoxLayout()
        chk_layout.setSpacing(7)

        self.chk_merge_remainder = QCheckBox(
            "Gộp phần dư (đoạn cuối ngắn hơn độ dài đặt) vào đoạn trước [Khuyên dùng]", self
        )
        self.chk_merge_remainder.setChecked(bool(settings.get("merge_remainder", True)))
        self.chk_merge_remainder.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_merge_remainder)

        self.chk_no_consecutive = QCheckBox(
            "Không để 2 đoạn liên tiếp từ cùng video nguồn [Khuyên dùng]", self
        )
        self.chk_no_consecutive.setChecked(bool(settings.get("no_consecutive_video", True)))
        self.chk_no_consecutive.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_no_consecutive)

        self.chk_trim_last = QCheckBox(
            "Cắt ngắn đoạn cuối để video đầu ra đúng thời lượng yêu cầu [Khuyên dùng]", self
        )
        self.chk_trim_last.setChecked(bool(settings.get("trim_last_segment", True)))
        self.chk_trim_last.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_trim_last)

        self.chk_no_repeat_batch = QCheckBox(
            "Không dùng lại đoạn đã xuất ở các video trong cùng đợt batch", self
        )
        self.chk_no_repeat_batch.setChecked(bool(settings.get("no_repeat_batch", True)))
        self.chk_no_repeat_batch.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_no_repeat_batch)

        self.chk_allow_reuse = QCheckBox(
            "Bốc có hoàn lại — xáo trộn lại kho khi hết để tiếp tục (mặc định: TẮT)", self
        )
        self.chk_allow_reuse.setChecked(bool(settings.get("allow_reuse", False)))
        self.chk_allow_reuse.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_allow_reuse)

        self.chk_use_history = QCheckBox(
            "Loại trừ các đoạn đã từng xuất trong lịch sử (export_history.json)", self
        )
        self.chk_use_history.setChecked(bool(settings.get("use_history", False)))
        self.chk_use_history.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_use_history)

        # History counter & clear button
        hist_box = QHBoxLayout()
        self.lbl_hist_count = QLabel("Lịch sử: 0 đoạn", self)
        self.lbl_hist_count.setStyleSheet("color: #666; font-size: 11px;")
        self.btn_clear_hist = PushButton("Xóa lịch sử", self)
        self.btn_clear_hist.setMaximumHeight(26)
        self.btn_clear_hist.clicked.connect(self._clear_history)
        hist_box.addWidget(self.lbl_hist_count)
        hist_box.addStretch()
        hist_box.addWidget(self.btn_clear_hist)
        chk_layout.addLayout(hist_box)

        layout.addLayout(chk_layout)

        # ── Pool info label (updated by main_window after build) ──────
        self.lbl_pool_info = QLabel("Kho đoạn: Chưa tạo", self)
        self.lbl_pool_info.setStyleSheet(
            "color: #555; font-style: italic; font-size: 12px; margin-top: 4px;"
        )
        layout.addWidget(self.lbl_pool_info)

        self.refresh_history_info()
        self._update_summary()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _update_summary(self):
        tot_sec = self.get_total_target_seconds()
        seg_dur = self.spin_seg_dur.value()
        batch = self.spin_batch_count.value()

        if seg_dur <= 0:
            return

        import math
        num_full = int(tot_sec // seg_dur)
        rem = round(tot_sec - num_full * seg_dur, 2)
        # With merge_remainder, the remainder folds into the last full segment
        total_clips = num_full + (1 if rem >= 0.01 and not self.chk_merge_remainder.isChecked() else 0)
        if num_full == 0:
            total_clips = 1  # At least one segment

        rem_desc = f" + phần dư {rem:.1f}s (được gộp)" if rem >= 0.01 else ""
        batch_desc = f" | Cả đợt: ≈{total_clips * batch} đoạn ({batch} video)" if batch > 1 else ""

        pool_dur = getattr(self, "_current_pool_dur", 0.0)
        warn = ""
        if pool_dur > 0 and tot_sec > pool_dur and not self.chk_allow_reuse.isChecked():
            warn = f"\n⚠️ Kho video chỉ có {pool_dur:.0f}s. Cần bật 'Bốc có hoàn lại' để tạo video {tot_sec/60:.0f} phút."

        self.lbl_summary.setText(
            f"→ Mỗi video: ≈{total_clips} đoạn ({num_full} × {seg_dur:.0f}s{rem_desc}){batch_desc}{warn}"
        )

        settings.update({
            "segment_duration": seg_dur,
            "target_hours": self.spin_hours.value(),
            "target_mins": self.spin_mins.value(),
            "target_secs": self.spin_secs.value(),
            "batch_count": batch,
            "merge_remainder": self.chk_merge_remainder.isChecked(),
            "no_consecutive_video": self.chk_no_consecutive.isChecked(),
            "trim_last_segment": self.chk_trim_last.isChecked(),
            "no_repeat_batch": self.chk_no_repeat_batch.isChecked(),
            "allow_reuse": self.chk_allow_reuse.isChecked(),
            "use_history": self.chk_use_history.isChecked(),
        }, auto_save=True)

        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_total_target_seconds(self) -> float:
        h = self.spin_hours.value()
        m = self.spin_mins.value()
        s = self.spin_secs.value()
        return float(h * 3600 + m * 60 + s)

    def update_pool_info(self, total_segments: int, total_duration: float):
        """Called by main_window after pool is built/shuffled."""
        self._current_pool_dur = total_duration
        m, s = divmod(int(total_duration), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
        self.lbl_pool_info.setText(
            f"Kho đoạn: {total_segments} đoạn | Tổng: {dur_str} ({total_duration:.0f}s)"
        )
        self.lbl_pool_info.setStyleSheet(
            "color: #0078D4; font-weight: 600; font-size: 12px; margin-top: 4px;"
        )
        self._update_summary()

    def refresh_history_info(self):
        try:
            from core.history_manager import HistoryManager
            cnt = HistoryManager().get_history_count()
            self.lbl_hist_count.setText(f"Lịch sử: {cnt} đoạn đã dùng")
        except Exception:
            self.lbl_hist_count.setText("Lịch sử: 0 đoạn")

    def _clear_history(self):
        try:
            from core.history_manager import HistoryManager
            HistoryManager().clear_history()
            self.refresh_history_info()
        except Exception:
            pass

    def get_config(self) -> CutConfig:
        return CutConfig(
            segment_duration=self.spin_seg_dur.value(),
            target_total_duration=self.get_total_target_seconds(),
            batch_count=self.spin_batch_count.value(),
            merge_remainder=self.chk_merge_remainder.isChecked(),
            allow_reuse=self.chk_allow_reuse.isChecked(),
            no_repeat_across_batch=self.chk_no_repeat_batch.isChecked(),
            no_consecutive_same_source=self.chk_no_consecutive.isChecked(),
            trim_last_segment=self.chk_trim_last.isChecked(),
            use_export_history=self.chk_use_history.isChecked(),
        )
