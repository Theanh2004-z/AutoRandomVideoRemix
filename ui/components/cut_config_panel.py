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
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGridLayout, QCheckBox, QToolButton
)
from qfluentwidgets import (
    CardWidget, SpinBox, DoubleSpinBox, PushButton, ComboBox
)
from core.config import CutConfig
from core.settings_manager import settings


class CutConfigPanel(CardWidget):
    """Area B: Segmentation & Randomisation Configuration."""
    config_changed = Signal()
    fill_frame_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        # Title
        title = QLabel("Thời lượng và số lượng")
        title.setObjectName("sectionHeader")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setSpacing(10)

        # ── 1. Segment duration ──────────────────────────────────────
        lbl_seg = QLabel("Độ dài mỗi đoạn video:")
        self.segment_caption = lbl_seg
        lbl_seg.setStyleSheet("font-weight: 500;")
        self.spin_seg_dur = DoubleSpinBox(self)
        self.spin_seg_dur.setRange(1.0, 3600.0)
        self.spin_seg_dur.setValue(float(settings.get("segment_duration", 10.0)))
        self.spin_seg_dur.setSingleStep(1.0)
        self.spin_seg_dur.setSuffix(" giây")
        self.spin_seg_dur.valueChanged.connect(self._update_summary)
        grid.addWidget(lbl_seg, 2, 0)
        grid.addWidget(self.spin_seg_dur, 2, 1)

        # ── 2. Total output duration (H / M / S) ─────────────────────
        lbl_tot = QLabel("Thời lượng mỗi đầu ra:")
        self.duration_caption = lbl_tot
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

        grid.addWidget(lbl_tot, 0, 0)
        grid.addLayout(time_box, 0, 1)

        # ── 3. Summary label ──────────────────────────────────────────
        self.lbl_summary = QLabel("-", self)
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setStyleSheet("color: #0078D4; font-weight: 600;")
        grid.addWidget(self.lbl_summary, 3, 0, 1, 2)

        # ── 4. Batch count ────────────────────────────────────────────
        lbl_batch = QLabel("Số lượng video:")
        self.batch_caption = lbl_batch
        lbl_batch.setStyleSheet("font-weight: 500;")
        self.spin_batch_count = SpinBox(self)
        self.spin_batch_count.setRange(1, 100)
        self.spin_batch_count.setValue(int(settings.get("batch_count", 1)))
        self.spin_batch_count.setSuffix(" video")
        self.spin_batch_count.valueChanged.connect(self._update_summary)
        grid.addWidget(lbl_batch, 1, 0)
        grid.addWidget(self.spin_batch_count, 1, 1)

        layout.addLayout(grid)

        self.btn_advanced = QToolButton(self)
        self.btn_advanced.setText("Tùy chọn chia đoạn và chống trùng")
        self.btn_advanced.setCheckable(True)
        layout.addWidget(self.btn_advanced)
        self.advanced_widget = QWidget(self)
        advanced_layout = QVBoxLayout(self.advanced_widget)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.btn_advanced.toggled.connect(self.advanced_widget.setVisible)

        # ── Separator label ───────────────────────────────────────────
        sep = QLabel("Tùy chọn chia đoạn & xáo trộn:")
        sep.setObjectName("mutedText")
        sep.setStyleSheet("font-weight: 600; margin-top: 6px;")
        advanced_layout.addWidget(sep)

        # ── 5. Checkboxes & Selectors ─────────────────────────────────
        chk_layout = QVBoxLayout()
        chk_layout.setSpacing(7)

        self.chk_merge_remainder = QCheckBox(
            "Gộp phần đuôi ngắn vào đoạn trước để giữ đủ nội dung", self
        )
        self.chk_merge_remainder.setChecked(bool(settings.get("merge_remainder", True)))
        self.chk_merge_remainder.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_merge_remainder)

        self.chk_trim_last = QCheckBox(
            "Cắt đoạn video cuối để khớp thời lượng đã chọn", self
        )
        self.chk_trim_last.setChecked(bool(settings.get("trim_last_segment", True)))
        self.chk_trim_last.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_trim_last)

        self.chk_fill_frame = QCheckBox(
            "Tự động lấp đầy khung hình", self
        )
        self.chk_fill_frame.setChecked(settings.get("aspect_ratio_mode", "fit") == "crop")
        self.chk_fill_frame.toggled.connect(self._on_fill_frame_toggled)
        chk_layout.addWidget(self.chk_fill_frame)

        self.lbl_fill_frame_desc = QLabel(
            "Tự phóng và căn giữa từng video để phủ kín khung xuất. Phần hình dư ở các cạnh sẽ bị cắt.", self
        )
        self.lbl_fill_frame_desc.setWordWrap(True)
        self.lbl_fill_frame_desc.setObjectName("mutedText")
        self.lbl_fill_frame_desc.setStyleSheet("margin-left: 24px; margin-bottom: 4px; font-size: 11px;")
        chk_layout.addWidget(self.lbl_fill_frame_desc)

        # Policy Controls Grid
        pol_grid = QGridLayout()
        pol_grid.setSpacing(8)

        lbl_unique = QLabel("Chính sách chống trùng:")
        lbl_unique.setStyleSheet("font-weight: 500;")
        self.cmb_unique_policy = ComboBox(self)
        self.cmb_unique_policy.addItem("Không dùng lại đoạn trong cả đợt", userData="unique_batch")
        self.cmb_unique_policy.addItem("Không dùng lại đoạn trong mỗi video", userData="unique_output")
        self.cmb_unique_policy.addItem("Cho phép dùng lại đoạn khi hết kho", userData="cycle_output")
        pol_grid.addWidget(lbl_unique, 0, 0)
        pol_grid.addWidget(self.cmb_unique_policy, 0, 1)

        lbl_anti = QLabel("Tránh clip cùng nguồn:")
        lbl_anti.setStyleSheet("font-weight: 500;")
        self.cmb_anti_consecutive = ComboBox(self)
        self.cmb_anti_consecutive.addItem("Ưu tiên khác nguồn nếu còn lựa chọn", userData="soft")
        self.cmb_anti_consecutive.addItem("Luôn khác nguồn ở hai đoạn liền nhau", userData="strict")
        self.cmb_anti_consecutive.addItem("Không hạn chế thứ tự nguồn", userData="off")
        pol_grid.addWidget(lbl_anti, 1, 0)
        pol_grid.addWidget(self.cmb_anti_consecutive, 1, 1)

        chk_layout.addLayout(pol_grid)

        # Legacy checkboxes (hidden for clean UI, synchronized for full backward-compat)
        self.chk_no_repeat_batch = QCheckBox(self)
        self.chk_no_repeat_batch.setVisible(False)
        self.chk_no_repeat_batch.stateChanged.connect(self._on_legacy_no_repeat_changed)

        self.chk_allow_reuse = QCheckBox(self)
        self.chk_allow_reuse.setVisible(False)
        self.chk_allow_reuse.stateChanged.connect(self._on_legacy_allow_reuse_changed)

        self.chk_no_consecutive = QCheckBox(self)
        self.chk_no_consecutive.setVisible(False)
        self.chk_no_consecutive.stateChanged.connect(self._on_legacy_no_consecutive_changed)

        # Initialize combo indices from settings
        saved_policy = settings.get("unique_policy", None)
        if not saved_policy:
            if settings.get("allow_reuse", False):
                saved_policy = "cycle_output"
            elif settings.get("no_repeat_batch", True):
                saved_policy = "unique_batch"
            else:
                saved_policy = "unique_output"
        self.set_unique_policy(saved_policy)

        saved_anti = settings.get("anti_consecutive_policy", None)
        if not saved_anti:
            saved_anti = "soft" if settings.get("no_consecutive_video", True) else "off"
        self.set_anti_consecutive_policy(saved_anti)

        self.cmb_unique_policy.currentIndexChanged.connect(self._on_unique_policy_changed)
        self.cmb_anti_consecutive.currentIndexChanged.connect(self._on_anti_consecutive_changed)

        self.chk_use_history = QCheckBox(
            "Bỏ qua những đoạn đã xuất trong các lần trước", self
        )
        self.chk_use_history.setChecked(bool(settings.get("use_history", False)))
        self.chk_use_history.stateChanged.connect(self._update_summary)
        chk_layout.addWidget(self.chk_use_history)

        # History counter & clear button
        hist_box = QHBoxLayout()
        self.lbl_hist_count = QLabel("Lịch sử: 0 đoạn", self)
        self.lbl_hist_count.setObjectName("mutedText")
        self.btn_clear_hist = PushButton("Xóa lịch sử", self)
        self.btn_clear_hist.setMaximumHeight(26)
        self.btn_clear_hist.clicked.connect(self._clear_history)
        hist_box.addWidget(self.lbl_hist_count)
        hist_box.addStretch()
        hist_box.addWidget(self.btn_clear_hist)
        chk_layout.addLayout(hist_box)

        advanced_layout.addLayout(chk_layout)
        layout.addWidget(self.advanced_widget)
        self.advanced_widget.setVisible(False)

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

    def _on_unique_policy_changed(self, index: int):
        u_pol = self.get_unique_policy()
        self.chk_allow_reuse.blockSignals(True)
        self.chk_no_repeat_batch.blockSignals(True)
        self.chk_allow_reuse.setChecked(u_pol == "cycle_output")
        self.chk_no_repeat_batch.setChecked(u_pol == "unique_batch")
        self.chk_allow_reuse.blockSignals(False)
        self.chk_no_repeat_batch.blockSignals(False)
        self._update_summary()

    def _on_anti_consecutive_changed(self, index: int):
        a_pol = self.get_anti_consecutive_policy()
        self.chk_no_consecutive.blockSignals(True)
        self.chk_no_consecutive.setChecked(a_pol != "off")
        self.chk_no_consecutive.blockSignals(False)
        self._update_summary()

    def _on_legacy_allow_reuse_changed(self, state):
        if self.chk_allow_reuse.isChecked():
            if self.get_unique_policy() != "cycle_output":
                self.set_unique_policy("cycle_output")

    def _on_legacy_no_repeat_changed(self, state):
        if self.chk_no_repeat_batch.isChecked():
            if self.get_unique_policy() != "unique_batch":
                self.set_unique_policy("unique_batch")

    def _on_legacy_no_consecutive_changed(self, state):
        if not self.chk_no_consecutive.isChecked():
            if self.get_anti_consecutive_policy() != "off":
                self.set_anti_consecutive_policy("off")
        else:
            if self.get_anti_consecutive_policy() == "off":
                self.set_anti_consecutive_policy("soft")

    def _update_summary(self):
        tot_sec = self.get_total_target_seconds()
        seg_dur = self.spin_seg_dur.value()
        batch = self.spin_batch_count.value()

        if seg_dur <= 0:
            return

        num_full = int(tot_sec // seg_dur)
        rem = round(tot_sec - num_full * seg_dur, 2)
        total_clips = num_full + (1 if rem >= 0.01 and not self.chk_merge_remainder.isChecked() else 0)
        if num_full == 0:
            total_clips = 1  # At least one segment

        rem_desc = f" + phần dư {rem:.1f}s (được gộp)" if rem >= 0.01 else ""
        batch_desc = f" | Cả đợt: ≈{total_clips * batch} đoạn ({batch} video)" if batch > 1 else ""

        u_pol = self.get_unique_policy()
        a_pol = self.get_anti_consecutive_policy()
        pool_dur = getattr(self, "_current_pool_dur", 0.0)
        warn = ""
        if pool_dur > 0 and tot_sec > pool_dur and u_pol != "cycle_output":
            warn = f"\n⚠️ Kho video chỉ có {pool_dur:.0f}s. Cần chọn chính sách 'Bốc có hoàn lại' để tạo video {tot_sec/60:.0f} phút."

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
            "no_consecutive_video": (a_pol != "off"),
            "trim_last_segment": self.chk_trim_last.isChecked(),
            "no_repeat_batch": (u_pol == "unique_batch"),
            "allow_reuse": (u_pol == "cycle_output"),
            "use_history": self.chk_use_history.isChecked(),
            "unique_policy": u_pol,
            "anti_consecutive_policy": a_pol,
        }, auto_save=True)

        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_unique_policy(self) -> str:
        idx = self.cmb_unique_policy.currentIndex()
        data = self.cmb_unique_policy.itemData(idx)
        return str(data) if data else "unique_batch"

    def set_unique_policy(self, policy: str):
        target = str(policy).lower()
        if "cycle" in target:
            self.cmb_unique_policy.setCurrentIndex(2)
        elif "output" in target:
            self.cmb_unique_policy.setCurrentIndex(1)
        else:
            self.cmb_unique_policy.setCurrentIndex(0)

    def get_anti_consecutive_policy(self) -> str:
        idx = self.cmb_anti_consecutive.currentIndex()
        data = self.cmb_anti_consecutive.itemData(idx)
        return str(data) if data else "soft"

    def set_anti_consecutive_policy(self, policy: str):
        target = str(policy).lower()
        if "strict" in target:
            self.cmb_anti_consecutive.setCurrentIndex(1)
        elif "off" in target:
            self.cmb_anti_consecutive.setCurrentIndex(2)
        else:
            self.cmb_anti_consecutive.setCurrentIndex(0)

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
        u_pol = self.get_unique_policy()
        a_pol = self.get_anti_consecutive_policy()
        return CutConfig(
            segment_duration=self.spin_seg_dur.value(),
            target_total_duration=self.get_total_target_seconds(),
            batch_count=self.spin_batch_count.value(),
            merge_remainder=self.chk_merge_remainder.isChecked(),
            allow_reuse=(u_pol == "cycle_output"),
            no_repeat_across_batch=(u_pol == "unique_batch"),
            no_consecutive_same_source=(a_pol != "off"),
            trim_last_segment=self.chk_trim_last.isChecked(),
            use_export_history=self.chk_use_history.isChecked(),
            unique_policy=u_pol,
            anti_consecutive_policy=a_pol,
        )

    def detach_advanced_settings(self):
        """Move policies into the dedicated settings dialog."""
        self._guided_compact = True
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.addWidget(self.advanced_widget)
        self.advanced_widget.setVisible(True)
        self.btn_advanced.hide()
        self.lbl_summary.hide()
        self.lbl_pool_info.hide()
        self._advanced_settings_box = box
        return box

    def _on_fill_frame_toggled(self, checked: bool):
        self.fill_frame_changed.emit(checked)

    def set_fill_frame(self, checked: bool):
        from PySide6.QtCore import QSignalBlocker
        with QSignalBlocker(self.chk_fill_frame):
            self.chk_fill_frame.setChecked(checked)

    def set_exporting(self, is_exporting: bool):
        self._exporting = is_exporting
        self.chk_fill_frame.setEnabled(not is_exporting and not getattr(self, '_audio_only', False))

    def set_audio_only(self, audio_only: bool):
        self._audio_only = audio_only
        self.chk_fill_frame.setEnabled(not audio_only and not getattr(self, '_exporting', False))
        if getattr(self, '_guided_compact', False):
            self._advanced_settings_box.setEnabled(not audio_only)
            self.batch_caption.setText("Số lượng tệp:" if audio_only else "Số lượng video:")
            self.spin_batch_count.setSuffix(" tệp" if audio_only else " video")
            return
        self.segment_caption.setVisible(not audio_only)
        self.spin_seg_dur.setVisible(not audio_only)
        self.lbl_summary.setVisible(not audio_only)
        self.btn_advanced.setVisible(not audio_only)
        self.advanced_widget.setVisible(not audio_only and self.btn_advanced.isChecked())
        self.lbl_pool_info.setVisible(not audio_only)
        self.batch_caption.setText("Số lượng tệp âm thanh:" if audio_only else "Số lượng video:")
        self.spin_batch_count.setSuffix(" tệp" if audio_only else " video")
