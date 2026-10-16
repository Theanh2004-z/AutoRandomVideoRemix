"""Guided, state-aware presentation for the existing remix services."""
import json
import os
from dataclasses import asdict

from PySide6.QtCore import Qt, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QPushButton, QToolButton, QVBoxLayout,
    QHBoxLayout, QGridLayout, QStackedWidget, QScrollArea, QButtonGroup,
    QSizePolicy,
)
from qfluentwidgets import PushButton, PrimaryPushButton, isDarkTheme, setThemeColor, FluentIcon

from ui.components.source_panel import SourcePanel
from ui.components.audio_panel import AudioPanel
from ui.components.cut_config_panel import CutConfigPanel
from ui.components.output_config_panel import OutputConfigPanel
from ui.components.progress_panel import ProgressPanel
from ui.components.cut_table_view import CutTableView
from ui.workflow_state import WorkflowInputs, evaluate_readiness, format_duration
from ui.styles import workflow_style_sheet
from ui.settings_dialog import SettingsDialog
from ui.background import PlanWorker
from core.app_paths import get_history_db_path


MODES = (
    ("video_with_random_audio", "Video + nhạc", "Ghép hình và nhạc từ hai kho nguồn"),
    ("video_only", "Chỉ video", "Tắt tiếng hoặc chọn âm thanh riêng"),
    ("audio_only", "Chỉ âm thanh", "Tạo playlist thành MP3, M4A hoặc WAV"),
)
STEP_TITLES = ("Chọn nguồn nội dung", "Thiết lập lần xuất", "Kiểm tra trước khi xuất", "Xuất và theo dõi")
STEP_DESCRIPTIONS = (
    "Chọn kết quả bạn muốn tạo, sau đó thêm thư mục video và âm thanh cần dùng.",
    "Chọn thời lượng và số lượng. Chất lượng, nơi lưu và các tùy chọn khác nằm trong Cài đặt.",
    "Kế hoạch được chuẩn bị tự động trong nền. Xem lại nội dung trước khi bắt đầu xuất.",
    "Theo dõi công việc tại đây. Kết quả được lưu vào thư mục bạn đã chọn.",
)


def label(text, name, parent=None):
    widget = QLabel(text, parent)
    widget.setObjectName(name)
    widget.setWordWrap(True)
    return widget


class GuidedWorkflow(QWidget):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.prepare_worker = None
        self._pending_prepare = False
        self._closing = False
        self._failed_signature = None
        self._background_message = ""
        self._running = False
        self._preparing = False
        self._validated_signature = None
        self._last_signature = None
        self._pool_config = None
        self._finished = False
        self._checks_key = None
        self._last_output = ""
        self._plan_error = ""
        self._result_state = ""
        self.setObjectName("workflowRoot")
        self._build()

    def _build(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame(self)
        sidebar.setObjectName("workflowSidebar")
        sidebar.setFixedWidth(216)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(22, 28, 18, 24)
        side.setSpacing(10)
        side.addWidget(label("REMIX", "brandTitle"))
        side.addWidget(label("Tạo nội dung từ kho của bạn", "mutedText"))
        side.addSpacing(34)
        self.nav_buttons = []
        for index, (title, detail) in enumerate((("Nguồn", "Chọn video và nhạc"), ("Cấu hình", "Tệp và thời lượng"), ("Kiểm tra", "Xem kế hoạch ghép"), ("Xuất", "Tiến độ và kết quả"))):
            button = QPushButton(f"{index + 1:02d}   {title}\n       {detail}", sidebar)
            button.setObjectName("stepButton")
            button.setCheckable(True)
            button.setMinimumHeight(66)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, i=index: self.go_to(i))
            side.addWidget(button)
            self.nav_buttons.append(button)
        side.addStretch()
        self.settings_button = PushButton(FluentIcon.SETTING, "Cài đặt", sidebar)
        self.settings_button.setMinimumHeight(38)
        self.settings_button.clicked.connect(self.open_settings)
        side.addWidget(self.settings_button)
        side.addSpacing(18)
        self.host.btn_theme = PushButton("Đổi giao diện", sidebar)
        self.host.btn_theme.clicked.connect(self.host._toggle_theme)
        side.addWidget(self.host.btn_theme)
        outer.addWidget(sidebar)

        workspace = QWidget(self)
        workspace.setObjectName("workflowWorkspace")
        content = QVBoxLayout(workspace)
        content.setContentsMargins(30, 25, 30, 20)
        content.setSpacing(12)
        top = QHBoxLayout()
        self.step_label = label("BƯỚC 1 / 4", "eyebrow")
        self.state_label = label("Đang thiết lập", "stateBadge")
        top.addWidget(self.step_label)
        top.addStretch()
        top.addWidget(self.state_label)
        content.addLayout(top)
        self.title_label = label(STEP_TITLES[0], "pageTitle")
        self.description_label = label(STEP_DESCRIPTIONS[0], "pageDescription")
        content.addWidget(self.title_label)
        content.addWidget(self.description_label)
        self.stack = QStackedWidget(workspace)
        self.pages = []
        self.page_layouts = []
        for _ in range(4):
            scroll = QScrollArea(self.stack)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget(scroll)
            body.setObjectName("stepBody")
            page_layout = QVBoxLayout(body)
            page_layout.setContentsMargins(0, 8, 8, 8)
            page_layout.setSpacing(16)
            body.setMaximumWidth(900 if len(self.pages) == 1 else 1120)
            wrapper = QWidget(scroll)
            wrapper.setObjectName("stepBody")
            wrap = QHBoxLayout(wrapper)
            wrap.setContentsMargins(0,0,0,0)
            wrap.addWidget(body,1)
            wrap.addStretch(0)
            scroll.setWidget(wrapper)
            self.stack.addWidget(scroll)
            self.pages.append(body)
            self.page_layouts.append(page_layout)
        content.addWidget(self.stack, 1)

        self._build_sources()
        self._build_settings()
        self._build_review()
        self._build_export()

        footer = QHBoxLayout()
        self.back_button = PushButton("Quay lại", workspace)
        self.back_button.clicked.connect(lambda: self.go_to(self.stack.currentIndex() - 1))
        self.footer_hint = label("", "footerHint")
        self.next_button = PrimaryPushButton("Tiếp tục: Cấu hình", workspace)
        self.next_button.setMinimumHeight(38)
        self.next_button.clicked.connect(self._next)
        footer.addWidget(self.back_button)
        footer.addWidget(self.footer_hint, 1)
        footer.addWidget(self.next_button)
        content.addLayout(footer)
        outer.addWidget(workspace, 1)
        self.apply_theme(isDarkTheme())

    def _build_sources(self):
        layout = self.page_layouts[0]
        mode_row = QHBoxLayout()
        mode_row.setSpacing(12)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for index, (key, title, subtitle) in enumerate(MODES):
            button = QPushButton(f"{title}\n{subtitle}", self.pages[0])
            button.setObjectName("modeCard")
            button.setCheckable(True)
            button.setMinimumHeight(85)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.clicked.connect(lambda checked=False, k=key: self.select_mode(k))
            self.mode_group.addButton(button, index)
            self.mode_buttons[key] = button
            mode_row.addWidget(button, 1)
        layout.addLayout(mode_row)
        self.host.source_panel = SourcePanel(self.pages[0])
        self.host.audio_panel = AudioPanel(self.pages[0])
        self.host.audio_panel.export_selector.setVisible(False)
        layout.addWidget(self.host.source_panel)
        layout.addWidget(self.host.audio_panel)
        self.source_hint = label("Gợi ý", "sourceHint")
        layout.addWidget(self.source_hint)
        layout.addStretch()

    def _build_settings(self):
        layout = self.page_layouts[1]
        self.host.cut_panel = CutConfigPanel(self.pages[1])
        self.host.output_panel = OutputConfigPanel(self.pages[1])
        layout.addWidget(self.host.cut_panel)
        self.audio_settings_card = QFrame(self.pages[1])
        self.audio_settings_card.setObjectName("settingsCard")
        audio_layout = QVBoxLayout(self.audio_settings_card)
        audio_layout.setContentsMargins(20, 18, 20, 18)
        audio_layout.addWidget(label("Cách ghép âm thanh", "sectionHeader"))
        audio_layout.addWidget(self.host.audio_panel.options_widget)
        self.settings_dialog = SettingsDialog(self.host, self.audio_settings_card)
        self.session_hint = label("", "sessionHint")
        layout.addWidget(self.session_hint)
        self.session_preferences = label("", "mutedText")
        layout.addWidget(self.session_preferences)
        shortcut = PushButton("Chỉnh chất lượng và nơi lưu…", self.pages[1])
        shortcut.clicked.connect(self.open_settings)
        layout.addWidget(shortcut,0,Qt.AlignLeft)
        layout.addStretch()

    def _build_review(self):
        layout = self.page_layouts[2]
        self.summary_card = QFrame(self.pages[2])
        self.summary_card.setObjectName("summaryCard")
        summary = QVBoxLayout(self.summary_card)
        summary.setContentsMargins(20, 18, 20, 18)
        self.summary_title = label("Lần xuất sắp tới", "summaryTitle")
        self.summary_detail = label("", "summaryDetail")
        self.summary_output = label("", "mutedText")
        summary.addWidget(self.summary_title)
        summary.addWidget(self.summary_detail)
        summary.addWidget(self.summary_output)
        layout.addWidget(self.summary_card)
        self.checks_widget = QWidget(self.pages[2])
        self.checks_layout = QVBoxLayout(self.checks_widget)
        self.checks_layout.setContentsMargins(0, 0, 0, 0)
        self.checks_layout.setSpacing(8)
        layout.addWidget(self.checks_widget)
        self.plan_notice = label("Chưa lập kế hoạch. Kiểm tra nguồn và cấu hình, sau đó nhấn nút bên dưới.", "planNotice")
        layout.addWidget(self.plan_notice)
        self.host.table_view = CutTableView(self.pages[2])
        self.host.table_view.setMinimumHeight(290)
        layout.addWidget(self.host.table_view, 1)

    def _build_export(self):
        layout = self.page_layouts[3]
        self.export_summary = label("", "exportSummary")
        layout.addWidget(self.export_summary)
        self.host.progress_panel = ProgressPanel(self.pages[3])
        self.host.progress_panel.prep_widget.hide()
        self.host.progress_panel.btn_pause.setToolTip("Yêu cầu tạm dừng sau tác vụ đang chạy; ứng dụng có thể cần hoàn tất đoạn hiện tại.")
        layout.addWidget(self.host.progress_panel)
        actions = QHBoxLayout()
        self.open_output_button = PushButton("Mở thư mục kết quả", self.pages[3])
        self.open_output_button.clicked.connect(self.host.output_panel._open_outdir)
        self.new_run_button = PushButton("Thiết lập lượt mới", self.pages[3])
        self.new_run_button.clicked.connect(self.new_run)
        actions.addWidget(self.open_output_button)
        actions.addWidget(self.new_run_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.result_label = label("Sau khi xuất, bạn có thể mở thư mục để xem kết quả.", "resultMessage")
        layout.addWidget(self.result_label)
        layout.addStretch()

    def bind(self):
        host = self.host
        for signal in (host.cut_panel.config_changed, host.audio_panel.config_changed, host.output_panel.config_changed):
            signal.connect(self.configuration_changed)
        host.source_panel.videos_scanned.connect(self.configuration_changed)
        host.audio_panel.audios_scanned.connect(self.configuration_changed)
        host.source_panel.scan_started.connect(self._video_path_changed)
        host.audio_panel.scan_started.connect(self._audio_path_changed)
        host.source_panel.chk_recursive.toggled.connect(self._video_path_changed)
        host.audio_panel.chk_recursive.toggled.connect(self._audio_path_changed)
        for panel in (host.source_panel,host.audio_panel):
            panel.scan_state_changed.connect(self.schedule_prepare)
        self.settings_dialog.cache_busy_changed.connect(self.refresh)
        host.source_panel.folder_edit.textChanged.connect(self._video_path_changed)
        host.audio_panel.folder_edit.textChanged.connect(self._audio_path_changed)
        host.progress_panel.running_changed.connect(self.running_changed)
        for signal in (host.progress_panel.build_pool_requested, host.progress_panel.shuffle_pool_requested,
                       host.progress_panel.audio_playlist_requested, host.progress_panel.preview_requested):
            signal.connect(self.manual_preparation_changed)
        host.progress_panel.pause_requested.connect(lambda: self.result_label.setText("Đã yêu cầu tạm dừng. Tác vụ hiện tại có thể cần hoàn tất trước khi dừng."))
        host.progress_panel.resume_requested.connect(lambda: self.result_label.setText("Đang tiếp tục xử lý kế hoạch đã chọn."))
        self.prepare_timer = QTimer(self)
        self.prepare_timer.setSingleShot(True)
        self.prepare_timer.setInterval(500)
        self.prepare_timer.timeout.connect(self.prepare)
        self._last_signature = self.signature()
        cfg = host.cut_panel.get_config()
        self._pool_config = (cfg.segment_duration, cfg.merge_remainder)
        self.timer = QTimer(self)
        self.timer.setInterval(350)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.go_to(0)

    def open_settings(self):
        if not self._running:
            self.settings_dialog.open()

    def schedule_prepare(self, *args):
        if not self._closing and not self._running and hasattr(self,'prepare_timer'):
            self.prepare_timer.start()

    def manual_preparation_changed(self):
        # Manual tools can replace a plan without changing any configuration.
        self._validated_signature = None
        self.host._plan_is_stale = True
        self._plan_error = ""
        self.refresh()

    def apply_theme(self, dark):
        setThemeColor("#75B4FF" if dark else "#2465B7", save=False)
        self.host.setStyleSheet(workflow_style_sheet(dark))
        self.settings_dialog.setStyleSheet(workflow_style_sheet(dark))
        self.host.btn_theme.setText("Giao diện sáng" if dark else "Giao diện tối")

    def signature(self):
        host = self.host
        data = {
            "cut": asdict(host.cut_panel.get_config()),
            "audio": asdict(host.audio_panel.get_config()),
            "output": asdict(host.output_panel.get_config()),
            "target": host.audio_panel.get_export_target(),
            "videos": [(v.path, v.duration, v.size_bytes) for v in host.source_videos],
            "tracks": [(a.path, a.duration, a.size_bytes) for a in (host.audio_pool.tracks if host.audio_pool else [])],
        }
        return json.dumps(data, sort_keys=True, default=str)

    def configuration_changed(self, *args):
        if self._running:
            return
        signature = self.signature()
        if signature != self._last_signature:
            self._last_signature = signature
            self._validated_signature = None
            self.host._plan_is_stale = True
            self.host.current_batch_plans = []
            self.host.current_audio_playlists = []
            self.host.table_view.mark_plan_stale()
            cut = self.host.cut_panel.get_config()
            pool_config = (cut.segment_duration, cut.merge_remainder)
            if self._pool_config != pool_config:
                self.host.pool = None
                self._pool_config = pool_config
            self._finished = False
            self._plan_error = ""
            self._result_state = ""
            self._failed_signature = None
            if self.prepare_worker:
                self.prepare_worker.requestInterruption()
        self.refresh()
        self.schedule_prepare()

    def _video_path_changed(self, *args):
        self.host.source_videos = []
        self.host.source_panel.valid_videos = []
        self.host.pool = None
        for stat in (self.host.source_panel.stat_valid_card, self.host.source_panel.stat_invalid_card, self.host.source_panel.stat_segments_card):
            stat.val_lbl.setText("0")
        self.host.source_panel.stat_duration_card.val_lbl.setText("00:00:00")
        self.host.source_panel.table.setRowCount(0)
        self.configuration_changed()

    def _audio_path_changed(self, *args):
        self.host.audio_pool = None
        self.host.audio_panel.valid_audios = []
        self.host.audio_panel.stat_valid_card.val_lbl.setText("0")
        self.host.audio_panel.stat_invalid_card.val_lbl.setText("0")
        self.host.audio_panel.stat_duration_card.val_lbl.setText("00:00:00")
        self.configuration_changed()

    def select_mode(self, key):
        combo = self.host.audio_panel.combo_export_target
        combo.setCurrentIndex(combo.findData(key))
        self.refresh()

    def inputs(self):
        host = self.host
        cut = host.cut_panel.get_config()
        audio = host.audio_panel.get_config()
        def scanning(panel):
            return bool(panel.scanner_thread and panel.scanner_thread.isRunning())
        return WorkflowInputs(
            host.audio_panel.get_export_target(), len(host.source_videos),
            sum(v.duration for v in host.source_videos),
            host.audio_pool.total_tracks if host.audio_pool else 0,
            host.audio_pool.total_duration if host.audio_pool else 0,
            cut.target_total_duration, cut.batch_count, host.cut_panel.get_unique_policy(),
            host.output_panel.get_config().output_dir,
            scanning(host.source_panel), scanning(host.audio_panel), audio.auto_reshuffle_cycle,
        )

    def validated(self):
        return bool(self._validated_signature and self._validated_signature == self.signature()
                    and not self.host._plan_is_stale and not self._finished)

    def refresh(self, *args):
        if not hasattr(self, "settings_button"):
            return
        value = self.inputs()
        readiness = evaluate_readiness(value)
        audio_only = value.target == "audio_only"
        self._refresh_source_visibility(value)
        self.audio_settings_card.setVisible(value.target != "video_only")
        self.host.cut_panel.set_audio_only(audio_only)
        self.host.output_panel.set_audio_only(audio_only)
        self.mode_buttons[value.target].setChecked(True)
        unit = "tệp âm thanh" if audio_only else "video"
        title = f"{value.batch_count} {unit} · Mục tiêu {format_duration(value.duration)} mỗi tệp"
        self.summary_title.setText(title)
        output = self.host.output_panel.get_config()
        profile = (self.host.audio_panel.get_config().audio_output_format.upper() if audio_only else
                   f"{output.resolution.replace('x', ' × ')} · {output.fps} FPS")
        policy = {"unique_batch": "Không lặp đoạn trong cả đợt", "unique_output": "Không lặp đoạn trong một video", "cycle_output": "Cho phép dùng lại đoạn khi hết kho"}[value.unique_policy]
        self.summary_detail.setText(f"{profile}" + (f"  |  {policy}" if not audio_only else "  |  Nhạc ghép theo thứ tự trong kế hoạch"))
        self.summary_output.setText(f"Lưu tại: {value.output_dir}")
        self.session_hint.setText(f"{value.batch_count} {unit} · Mục tiêu {format_duration(value.duration)} mỗi tệp")
        self.session_preferences.setText(f"{profile}  ·  Lưu tại: {value.output_dir}")
        self.export_summary.setText(f"{title}\n{profile}  ·  Lưu tại: {value.output_dir}")
        self.source_hint.setText("Chỉ dùng âm thanh: bạn có thể bỏ qua kho video." if audio_only else
                                 "Chọn hoặc dán đường dẫn thư mục. Ứng dụng tự kiểm tra ngầm và chuẩn bị kế hoạch.")
        prepared = self.validated()
        checks_key = (readiness.checks, prepared)
        if checks_key != self._checks_key:
            self._checks_key = checks_key
            while self.checks_layout.count():
                item = self.checks_layout.takeAt(0)
                item.widget().deleteLater()
            for check in readiness.checks:
                self.checks_layout.addWidget(self._create_check_row(check, prepared))
        if self._preparing:
            self.plan_notice.setText(self._background_message or "Đang chuẩn bị kế hoạch trong nền…")
        elif self._plan_error:
            self.plan_notice.setText(self._plan_error)
        elif prepared:
            self.plan_notice.setText("Kế hoạch đã sẵn sàng. Nội dung bên dưới sẽ được dùng khi xuất; thay đổi cấu hình sẽ yêu cầu kiểm tra lại.")
        elif self.host._plan_is_stale:
            self.plan_notice.setText("Kế hoạch sẽ tự chuẩn bị khi nguồn và cấu hình đã đủ.")
        self.state_label.setText("Đang xuất" if self._running else "Đang chuẩn bị ngầm" if self._preparing else
                                 "Đang kiểm tra nguồn" if value.scanning_video or value.scanning_audio else
                                 self._result_state or ("Sẵn sàng xuất" if prepared else "Đang thiết lập"))
        self.settings_button.setEnabled(not self._running)
        if self._running and self.settings_dialog.isVisible():
            self.settings_dialog.close()
        self._refresh_navigation(value, readiness, prepared)
        self.host.progress_panel.btn_start.setText(f"Bắt đầu xuất {value.batch_count} {unit}")
        self.host.progress_panel.btn_start.setEnabled(prepared and not self._running and readiness.can_prepare and not self.settings_dialog.cache_busy)
        self.open_output_button.setEnabled(os.path.isdir(value.output_dir))
        self.new_run_button.setEnabled(not self._running)

        self.host.progress_panel.btn_build_pool.setVisible(not audio_only)
        self.host.progress_panel.btn_shuffle.setVisible(not audio_only)
        self.host.progress_panel.btn_audio_playlist.setVisible(value.target != "video_only")
        for page in self.pages[:3]:
            page.setEnabled(not self._running)

    def _refresh_source_visibility(self, value):
        self.host.source_panel.setVisible(value.target != "audio_only")
        self.host.audio_panel.setVisible(value.target != "video_only")

    def _create_check_row(self, check, prepared):
        row = QFrame(self.checks_widget)
        row.setObjectName("checkRow")
        row.setProperty("state", check.state)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(14, 10, 14, 10)
        status = label({"ready": "Đã có", "warning": "Lưu ý", "action": "Cần làm"}[check.state], "checkStatus")
        status.setFixedWidth(62)
        row_layout.addWidget(status)
        detail = ("Đã lập kế hoạch đủ các đoạn theo cấu hình đã chọn." if prepared and check.title == "Độ dài kho video" else check.detail)
        row_layout.addWidget(label(f"{check.title} — {detail}", "checkDetail"), 1)
        return row

    def _refresh_navigation(self, value, readiness, prepared):
        index = self.stack.currentIndex()
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
            button.setEnabled((not self._running or i == 3) and (i != 3 or prepared or self._running or self._finished))
        self.back_button.setVisible(index > 0)
        self.back_button.setEnabled(not self._running)
        self.next_button.setVisible(index != 3)
        self.next_button.setText("Đang kiểm tra…" if self._preparing else ("Tiếp tục: Cấu hình", "Tiếp tục: Kiểm tra", "Tiếp tục: Xuất" if prepared else "Thử kiểm tra lại")[min(index, 2)])
        next_ready = readiness.sources_ready if index == 0 else readiness.can_prepare
        self.next_button.setEnabled(next_ready and not self._preparing and not self._running)
        self.footer_hint.setText("" if self._running else "Đang chuẩn bị ngầm; bạn có thể tiếp tục chỉnh thiết lập." if self._preparing else
                                 "Kế hoạch đã sẵn sàng." if prepared else readiness.next_action)
        if index == 3 and not self._running:
            self.footer_hint.setText("Kết quả đã sẵn sàng trong thư mục đã chọn." if self._finished else "Kiểm tra nơi lưu, rồi nhấn Bắt đầu xuất." if prepared else "Quay lại bước Kiểm tra để chuẩn bị lượt xuất mới.")

    def go_to(self, index):
        index = max(0, min(3, index))
        if self._running and index != 3:
            return
        if index == 3 and not (self.validated() or self._running or self._finished):
            index = 2
        self.stack.setCurrentIndex(index)
        self.step_label.setText(f"BƯỚC {index + 1} / 4")
        self.title_label.setText(STEP_TITLES[index])
        self.description_label.setText(STEP_DESCRIPTIONS[index])
        self.refresh()

    def _next(self):
        index = self.stack.currentIndex()
        if index == 2 and not self.validated():
            self.prepare()
        else:
            self.go_to(index + 1)

    def prepare(self):
        self.prepare_timer.stop()
        if self._closing or self._running:
            return
        if self.prepare_worker:
            self._pending_prepare = True
            return
        if not evaluate_readiness(self.inputs()).can_prepare or self.validated():
            self.refresh()
            return
        signature = self.signature()
        # Automatic failures wait for a changed input; explicit Retry clears this.
        if self.sender() is self.prepare_timer and self._failed_signature == signature:
            return
        self._preparing = True
        self._plan_error = ""
        self._background_message = "Đang chuẩn bị kế hoạch trong nền…"
        host = self.host
        output = host.output_panel.get_config()
        output.export_target = host.audio_panel.get_export_target()
        self.prepare_worker = PlanWorker(host.source_videos,
            host.audio_pool.tracks if host.audio_pool else [],
            host.cut_panel.get_config(), host.audio_panel.get_config(), output,
            signature, get_history_db_path(), self)
        self.prepare_worker.prepared.connect(self.accept_prepared)
        self.prepare_worker.failed.connect(self.preparation_failed)
        self.prepare_worker.progress.connect(self.preparation_progress)
        self.prepare_worker.finished.connect(self.preparation_finished)
        self.prepare_worker.start()
        self.refresh()

    def preparation_progress(self, message):
        self._background_message = message
        self.refresh()

    def accept_prepared(self, result):
        worker = self.sender()
        if self._closing or worker.signature != self.signature() or worker.isInterruptionRequested():
            return
        host = self.host
        host.pool = result.pool
        if result.pool:
            with QSignalBlocker(host.cut_panel):
                host.cut_panel.update_pool_info(result.pool.total_segments,result.pool.total_duration)
            host.table_view.set_pool(result.pool.draw_pool)
        host.current_manifest = result.manifest
        host.current_batch_plans = result.manifest.video_batches
        host.current_audio_playlists = result.playlists
        host.table_view.set_plans(host.current_batch_plans)
        host.table_view.set_audio_playlists(result.playlists)
        audio_only = host.audio_panel.get_export_target() == 'audio_only'
        host.table_view.tabs.setTabVisible(0,not audio_only)
        host.table_view.tabs.setTabVisible(1,not audio_only)
        host.table_view.tabs.setTabVisible(2,host.audio_panel.get_export_target() != 'video_only')
        host.table_view.tabs.setCurrentIndex(2 if audio_only else 1)
        host._plan_is_stale = False
        self._validated_signature = worker.signature
        self._last_signature = worker.signature
        self._failed_signature = None
        self._finished = False
        self._result_state = ""
        host.progress_panel.log('SUCCESS','Kế hoạch đã được chuẩn bị ngầm và sẵn sàng xem lại.')

    def preparation_failed(self, message):
        worker = self.sender()
        if not self._closing and worker.signature == self.signature():
            self._validated_signature = None
            self._failed_signature = worker.signature
            self._plan_error = 'Chưa tạo được kế hoạch. '+message
            self.host.progress_panel.log('ERROR',message)

    def preparation_finished(self):
        worker = self.sender()
        if worker is self.prepare_worker:
            self.prepare_worker = None
        worker.deleteLater()
        self._preparing = False
        self.refresh()
        if self._pending_prepare:
            self._pending_prepare = False
            self.schedule_prepare()

    def stop_background(self):
        self._closing = True
        self.prepare_timer.stop()
        self.timer.stop()
        for panel in (self.host.source_panel,self.host.audio_panel):
            panel.auto_scan.stop()
        if self.prepare_worker:
            self.prepare_worker.requestInterruption()
        if self.settings_dialog.cache_worker:
            self.settings_dialog.cache_worker.requestInterruption()

    def background_running(self):
        workers = [self.prepare_worker,self.settings_dialog.cache_worker,
                   self.host.source_panel.scanner_thread,self.host.audio_panel.scanner_thread]
        return any(worker and worker.isRunning() for worker in workers)

    def running_changed(self, running):
        self._running = running
        if running:
            self._finished = False
            self._result_state = ""
            self.result_label.setText("Đang xử lý. Bạn có thể tạm dừng hoặc hủy bằng các nút bên trên.")
            self.go_to(3)
        self.refresh()

    def attach_worker(self, worker):
        worker.batch_finished.connect(self.finished)
        worker.error_occurred.connect(self.show_error)
        worker.video_completed.connect(self.output_completed)

    def output_completed(self, index, path):
        self._last_output = path

    def finished(self, success_count, total_count):
        self._running = False
        self._finished = success_count == total_count and total_count > 0
        if self._finished:
            self._result_state = "Đã hoàn tất"
            self.result_label.setText(f"Đã hoàn tất {success_count}/{total_count} tệp. Mở thư mục kết quả để xem, hoặc thiết lập một lượt mới.")
        else:
            self._result_state = "Cần kiểm tra lại"
            self._validated_signature = None
            self.result_label.setText(f"Đã tạo {success_count}/{total_count} tệp. Kiểm tra thông báo chi tiết trước khi thử lại.")
        self.refresh()

    def show_error(self, message):
        self.result_label.setText(message)
        self.host.progress_panel.set_logs_visible(True)

    def new_run(self):
        if self._running:
            return
        self._finished = False
        self._result_state = ""
        self._plan_error = ""
        self._validated_signature = None
        self.host._plan_is_stale = True
        self.host.current_batch_plans = []
        self.host.current_audio_playlists = []
        self.go_to(1)
        self.schedule_prepare()

    def hideEvent(self, event):
        if hasattr(self, "timer"):
            self.timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        if hasattr(self, "timer"):
            self.timer.start()
        super().showEvent(event)
