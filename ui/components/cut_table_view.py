"""
Cut List & Pool View (Khu vực F) — refactored with two tabs.

Tab 1 — "Kho đoạn":   Shows all segments built by the slicer, their source
                        file, time range, duration, and is_used status.
Tab 2 — "Kế hoạch ghép": Shows the planned order of segments for each
                           output video (existing functionality).
"""
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QHeaderView, QTableWidgetItem, QTabWidget
)
from qfluentwidgets import CardWidget, TableWidget, ComboBox, PushButton

from core.config import SegmentInfo, AudioPlaylistItem


class CutTableView(CardWidget):
    """Area F: Segment pool viewer + output plan table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool_segments: List[SegmentInfo] = []
        self._batch_plans: List[List[SegmentInfo]] = []
        self._page_offsets = {'pool':0,'plan':0,'audio':0}
        self._pagers = {}
        self._audio_playlists = []
        self._audio_playlist = []
        self._init_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        # Header
        header = QHBoxLayout()
        title = QLabel("Nội dung trong kế hoạch")
        title.setObjectName("sectionHeader")
        header.addWidget(title)
        layout.addLayout(header)

        # Tab widget
        self.tabs = QTabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)

        # ── Tab 1: Pool ───────────────────────────────────────────────
        self.pool_tab = QWidget()
        pool_layout = QVBoxLayout(self.pool_tab)
        pool_layout.setContentsMargins(0, 8, 0, 0)
        pool_layout.setSpacing(6)

        self.lbl_pool_summary = QLabel("Chưa có kho đoạn. Nhấn 'Tạo kho đoạn' để bắt đầu.", self)
        self.lbl_pool_summary.setStyleSheet("color: #666; font-style: italic;")
        pool_layout.addWidget(self.lbl_pool_summary)

        self.pool_table = TableWidget(self.pool_tab)
        self.pool_table.setColumnCount(6)
        self.pool_table.setHorizontalHeaderLabels([
            "STT", "Video nguồn", "Bắt đầu", "Kết thúc", "Thời lượng", "Đã dùng?"
        ])
        self._configure_header(self.pool_table, stretch_col=1)
        self.pool_table.setMinimumHeight(160)
        pool_layout.addWidget(self.pool_table)
        self._add_pager(pool_layout,'pool',self._display_pool)

        self.tabs.addTab(self.pool_tab, "Kho đoạn")

        # ── Tab 2: Plan ───────────────────────────────────────────────
        self.plan_tab = QWidget()
        plan_layout = QVBoxLayout(self.plan_tab)
        plan_layout.setContentsMargins(0, 8, 0, 0)
        plan_layout.setSpacing(6)

        plan_header = QHBoxLayout()
        self.lbl_plan_summary = QLabel(
            "Chưa có kế hoạch. Nhấn 'Xem trước kế hoạch' để lập danh sách.", self
        )
        self.lbl_plan_summary.setStyleSheet("color: #666; font-style: italic;")
        plan_header.addWidget(self.lbl_plan_summary)
        plan_header.addStretch()

        lbl_select = QLabel("Xem:")
        self.combo_target_video = ComboBox(self.plan_tab)
        self.combo_target_video.currentIndexChanged.connect(self._on_plan_video_selected)
        plan_header.addWidget(lbl_select)
        plan_header.addWidget(self.combo_target_video)
        plan_layout.addLayout(plan_header)

        self.plan_table = TableWidget(self.plan_tab)
        self.plan_table.setColumnCount(6)
        self.plan_table.setHorizontalHeaderLabels([
            "STT", "Video nguồn", "Bắt đầu", "Kết thúc", "Thời lượng", "Trạng thái"
        ])
        self._configure_header(self.plan_table, stretch_col=1)
        self.plan_table.setMinimumHeight(160)
        plan_layout.addWidget(self.plan_table)
        self._add_pager(plan_layout,'plan',lambda: self._display_plan(self.combo_target_video.currentIndex()))

        self.tabs.addTab(self.plan_tab, "Kế hoạch video")

        # ── Tab 3: Audio Playlist ─────────────────────────────────────
        self.audio_tab = QWidget()
        audio_layout = QVBoxLayout(self.audio_tab)
        audio_layout.setContentsMargins(0, 8, 0, 0)
        audio_layout.setSpacing(6)

        self.lbl_audio_summary = QLabel(
            "Chưa có playlist âm thanh. Nhấn 'Tạo playlist nhạc' để lập danh sách.", self
        )
        self.lbl_audio_summary.setStyleSheet("color: #666; font-style: italic;")
        audio_header = QHBoxLayout()
        self.lbl_audio_summary.setWordWrap(True)
        audio_header.addWidget(self.lbl_audio_summary,1)
        self.combo_audio_output = ComboBox(self.audio_tab)
        self.combo_audio_output.currentIndexChanged.connect(self._on_audio_output_selected)
        audio_header.addWidget(self.combo_audio_output)
        audio_layout.addLayout(audio_header)

        self.audio_table = TableWidget(self.audio_tab)
        self.audio_table.setColumnCount(6)
        self.audio_table.setHorizontalHeaderLabels([
            "STT", "Tên bài hát", "Thời lượng gốc", "Thời lượng phát", "Lượt (Cycle)", "Trạng thái"
        ])
        self._configure_header(self.audio_table, stretch_col=1)
        self.audio_table.setMinimumHeight(160)
        audio_layout.addWidget(self.audio_table)
        self._add_pager(audio_layout,'audio',self._display_audio)

        self.tabs.addTab(self.audio_tab, "Playlist nhạc")

        layout.addWidget(self.tabs, 1)

    def _add_pager(self, layout, key, render):
        widget = QWidget(self)
        row = QHBoxLayout(widget)
        row.setContentsMargins(0,0,0,0)
        before, after = PushButton('Trang trước',widget), PushButton('Trang sau',widget)
        caption = QLabel('',widget)
        caption.setObjectName('mutedText')
        before.clicked.connect(lambda: self._turn_page(key,-1,render))
        after.clicked.connect(lambda: self._turn_page(key,1,render))
        row.addWidget(caption,1)
        row.addWidget(before)
        row.addWidget(after)
        self._pagers[key] = (widget,caption,before,after)
        layout.addWidget(widget)
        widget.hide()

    def _turn_page(self,key,direction,render):
        self._page_offsets[key] = max(0,self._page_offsets[key]+direction*200)
        render()

    def _page(self,key,items):
        start = min(self._page_offsets[key],max(0,((len(items)-1)//200)*200))
        self._page_offsets[key] = start
        end = min(len(items),start+200)
        widget,caption,before,after = self._pagers[key]
        widget.setVisible(len(items)>200)
        caption.setText(f'{start+1}–{end} / {len(items)} dòng')
        before.setEnabled(start>0)
        after.setEnabled(end<len(items))
        return items[start:end]

    def _configure_header(self, table: TableWidget, stretch_col: int):
        hdr = table.horizontalHeader()
        for col in range(table.columnCount()):
            if col == stretch_col:
                hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)

    # ------------------------------------------------------------------
    # Pool tab API
    # ------------------------------------------------------------------

    def set_pool(self, segments: List[SegmentInfo]):
        """Populate the pool tab with the (possibly shuffled) segment list."""
        self._pool_segments = list(segments)

        total = len(segments)
        total_dur = sum(s.duration for s in segments)
        m, s = divmod(int(total_dur), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
        self.lbl_pool_summary.setText(
            f"Kho đoạn: {total} đoạn | Tổng: {dur_str} ({total_dur:.1f}s)"
        )
        self.lbl_pool_summary.setStyleSheet("color: #0078D4; font-weight: 600;")

        self._page_offsets['pool'] = 0
        self._display_pool()
        self.tabs.setCurrentIndex(0)

    def _display_pool(self):
        entries = self._page('pool',self._pool_segments)
        self.pool_table.setUpdatesEnabled(False)
        try:
            self.pool_table.setRowCount(len(entries))
            for row,seg in enumerate(entries):
                self._render_pool_row(row,seg)
        finally:
            self.pool_table.setUpdatesEnabled(True)

    def _render_pool_row(self, row: int, seg: SegmentInfo):
        items = [
            (f"#{row + 1 + self._page_offsets['pool']}", Qt.AlignCenter),
            (seg.source_video_name, Qt.AlignLeft),
            (seg.formatted_start, Qt.AlignCenter),
            (seg.formatted_end, Qt.AlignCenter),
            (f"{seg.duration:.2f}s", Qt.AlignCenter),
            ("✓ Đã dùng" if seg.is_used else "—", Qt.AlignCenter),
        ]
        for col, (text, align) in enumerate(items):
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            if col == 5:  # Used column
                if seg.is_used:
                    item.setForeground(Qt.darkGreen)
                else:
                    item.setForeground(Qt.darkGray)
            self.pool_table.setItem(row, col, item)

    def refresh_pool_used(self):
        """Re-render the 'Đã dùng?' column after picking updates is_used flags."""
        self.pool_table.setUpdatesEnabled(False)
        try:
            for row, seg in enumerate(self._page("pool",self._pool_segments)):
                if row >= self.pool_table.rowCount():
                    break
                used_item = QTableWidgetItem("✓ Đã dùng" if seg.is_used else "—")
                used_item.setTextAlignment(Qt.AlignCenter)
                used_item.setForeground(Qt.darkGreen if seg.is_used else Qt.darkGray)
                self.pool_table.setItem(row, 5, used_item)
        finally:
            self.pool_table.setUpdatesEnabled(True)

    # ------------------------------------------------------------------
    # Plan tab API
    # ------------------------------------------------------------------

    def set_plans(self, plans: List[List[SegmentInfo]]):
        """Populate the plan tab with a batch of output video plans."""
        self._batch_plans = plans
        self._page_offsets["plan"] = 0

        self.combo_target_video.blockSignals(True)
        self.combo_target_video.clear()
        for i, plan in enumerate(plans):
            total_dur = sum(s.effective_duration for s in plan)
            self.combo_target_video.addItem(
                f"Video #{i + 1} ({len(plan)} đoạn, {total_dur:.1f}s)", userData=i
            )
        self.combo_target_video.blockSignals(False)

        if plans:
            self._display_plan(0)
        else:
            self.plan_table.setRowCount(0)
            self.lbl_plan_summary.setText("Chưa có kế hoạch ghép.")

        # Switch to plan tab
        self.tabs.setCurrentIndex(1)

    def mark_plan_stale(self):
        """Mark current plan in the UI as out-of-date or cleared."""
        self._batch_plans = []
        self.combo_target_video.blockSignals(True)
        self.combo_target_video.clear()
        self.combo_target_video.blockSignals(False)
        self.plan_table.setRowCount(0)
        self.lbl_plan_summary.setText("Kế hoạch cũ đã hết hạn. Kế hoạch mới sẽ tự chuẩn bị trong nền.")
        self.set_audio_playlists([])
        self.lbl_plan_summary.setStyleSheet("color: #D83B01; font-style: italic;")

    def _on_plan_video_selected(self, index: int):
        if 0 <= index < len(self._batch_plans):
            self._page_offsets["plan"] = 0
            self._display_plan(index)

    def _display_plan(self, plan_idx: int):
        if not self._batch_plans or plan_idx >= len(self._batch_plans):
            return

        plan = self._batch_plans[plan_idx]
        total_dur = sum(s.effective_duration for s in plan)
        m, s = divmod(int(total_dur), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
        self.lbl_plan_summary.setText(
            f"Kế hoạch Video #{plan_idx + 1}: {len(plan)} đoạn | Thời lượng: {dur_str} ({total_dur:.1f}s)"
        )
        self.lbl_plan_summary.setStyleSheet("color: #0078D4; font-weight: 600;")

        self.plan_table.setUpdatesEnabled(False)
        try:
            entries = self._page("plan",plan)
            self.plan_table.setRowCount(len(entries))
            for row, seg in enumerate(entries):
                self._render_plan_row(row, seg)
        finally:
            self.plan_table.setUpdatesEnabled(True)

    def _render_plan_row(self, row: int, seg: SegmentInfo):
        dur_str = f"{seg.effective_duration:.2f}s"
        if seg.trim_end_sec is not None:
            dur_str += " ✂"  # indicate trimmed

        items = [
            (f"#{seg.plan_position}", Qt.AlignCenter),
            (seg.source_video_name, Qt.AlignLeft),
            (seg.formatted_start, Qt.AlignCenter),
            (seg.formatted_end, Qt.AlignCenter),
            (dur_str, Qt.AlignCenter),
            (seg.status, Qt.AlignCenter),
        ]
        status_colors = {
            "Hoàn tất": Qt.darkGreen,
            "Đang cắt": Qt.blue,
            "Lỗi": Qt.red,
        }
        for col, (text, align) in enumerate(items):
            item = QTableWidgetItem(text)
            item.setTextAlignment(align)
            if col == 5 and seg.status in status_colors:
                item.setForeground(status_colors[seg.status])
            elif col == 5:
                item.setForeground(Qt.darkGray)
            self.plan_table.setItem(row, col, item)

    def update_clip_status(self, video_idx: int, plan_position: int, new_status: str):
        """Update one segment's status in the plan table (called during rendering)."""
        plan_i = video_idx - 1
        if 0 <= plan_i < len(self._batch_plans):
            plan = self._batch_plans[plan_i]
            for seg in plan:
                if seg.plan_position == plan_position:
                    seg.status = new_status
                    break

            if self.combo_target_video.currentIndex() == plan_i:
                row = plan_position - 1 - self._page_offsets["plan"]
                if 0 <= row < self.plan_table.rowCount():
                    status_item = QTableWidgetItem(new_status)
                    status_item.setTextAlignment(Qt.AlignCenter)
                    color_map = {
                        "Hoàn tất": Qt.darkGreen,
                        "Đang cắt": Qt.blue,
                        "Lỗi": Qt.red,
                    }
                    if new_status in color_map:
                        status_item.setForeground(color_map[new_status])
                    self.plan_table.setItem(row, 5, status_item)

    # ------------------------------------------------------------------
    # Audio playlist tab API (Phần 2)
    # ------------------------------------------------------------------

    def set_audio_playlist(self, playlist: List[AudioPlaylistItem]):
        """Populate the audio playlist tab with selected tracks."""
        self.set_audio_playlists([playlist] if playlist else [])

    def set_audio_playlists(self, playlists):
        self._audio_playlists = playlists
        self.combo_audio_output.blockSignals(True)
        self.combo_audio_output.clear()
        for index in range(len(playlists)):
            self.combo_audio_output.addItem(f'Đầu ra #{index+1}', userData=index)
        self.combo_audio_output.blockSignals(False)
        self._on_audio_output_selected(0)

    def _on_audio_output_selected(self,index):
        self._page_offsets['audio'] = 0
        self._audio_playlist = self._audio_playlists[index] if 0 <= index < len(self._audio_playlists) else []
        self._display_audio()

    def _display_audio(self):
        playlist = self._audio_playlist
        total = len(playlist)
        tot_eff = sum(p.effective_duration for p in playlist)
        m, s = divmod(int(tot_eff), 60)
        h, m = divmod(m, 60)
        dur_str = f"{h}h {m:02d}m {s:02d}s" if h > 0 else f"{m:02d}m {s:02d}s"
        self.lbl_audio_summary.setText(f"Playlist: {total} bài | Tổng phát: {dur_str} ({tot_eff:.1f}s)")
        self.lbl_audio_summary.setStyleSheet("color: #0078D4; font-weight: 600;")

        self.audio_table.setUpdatesEnabled(False)
        try:
            entries = self._page("audio",playlist)
            self.audio_table.setRowCount(len(entries))
            for row, item in enumerate(entries):
                self._render_audio_row(row, item)
        finally:
            self.audio_table.setUpdatesEnabled(True)

        self.tabs.setCurrentIndex(2)

    def _render_audio_row(self, row: int, item: AudioPlaylistItem):
        cut_desc = " ✂ [Cắt cuối]" if item.is_trimmed else ""
        items = [
            (f"#{row + 1 + self._page_offsets['audio']}", Qt.AlignCenter),
            (item.source_audio_name, Qt.AlignLeft | Qt.AlignVCenter),
            (item.formatted_original, Qt.AlignCenter),
            (f"{item.formatted_effective}{cut_desc}", Qt.AlignCenter),
            (f"Lượt #{item.cycle_index}", Qt.AlignCenter),
            (item.status, Qt.AlignCenter),
        ]
        for col, (text, align) in enumerate(items):
            cell = QTableWidgetItem(text)
            cell.setTextAlignment(align)
            if col == 3 and item.is_trimmed:
                cell.setForeground(Qt.darkYellow)
            self.audio_table.setItem(row, col, cell)

