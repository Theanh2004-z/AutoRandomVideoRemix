"""One-screen workspace with confirmation and progress in an export dialog."""
import os
import subprocess
import threading

from PySide6.QtCore import Qt, QThread, Signal, QSignalBlocker
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QDialog, QScrollArea, QStackedWidget, QFormLayout, QSizePolicy, QCheckBox,
)
from qfluentwidgets import PushButton, PrimaryPushButton, ComboBox, LineEdit, SpinBox, FluentIcon, isDarkTheme, CheckBox

from core.settings_manager import settings
from ui.workflow import GuidedWorkflow, label
from ui.workflow_state import evaluate_readiness
from ui.components.source_panel import SourcePanel
from ui.components.audio_panel import AudioPanel
from ui.components.cut_config_panel import CutConfigPanel
from ui.components.output_config_panel import OutputConfigPanel
from ui.components.progress_panel import ProgressPanel
from ui.components.cut_table_view import CutTableView
from ui.settings_dialog import SettingsDialog
from ui.styles import dashboard_style_sheet
from ui.window_frame import RemixTitleBar


class PreviewWorker(QThread):
    ready = Signal(QImage)

    def __init__(self, path, start, parent):
        super().__init__(parent)
        self.path, self.start_seconds = path, start
        self._process = None
        self._lock = threading.Lock()

    def stop(self):
        self.requestInterruption()
        with self._lock:
            if self._process and self._process.poll() is None:
                try: self._process.kill()
                except OSError: pass

    def run(self):
        command = ['ffmpeg','-v','error','-nostdin','-threads','2','-ss',str(self.start_seconds),
            '-i',self.path,'-an','-frames:v','1','-vf',
            'scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2',
            '-f','image2pipe','-c:v','png','pipe:1']
        try:
            with self._lock:
                if self.isInterruptionRequested(): return
                self._process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            try:
                data,_ = self._process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                self._process.kill(); self._process.communicate(); return
            if self._process.returncode==0 and not self.isInterruptionRequested():
                image=QImage.fromData(data)
                if not image.isNull(): self.ready.emit(image)
        except OSError:
            pass
        finally:
            with self._lock: self._process=None


class DashboardWorkflow(GuidedWorkflow):
    def _build(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.preview_worker = None
        self._preview_signature = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.header = RemixTitleBar(self.host)
        header = self.header
        self.host.install_title_bar(header)
        bar = QHBoxLayout(header.content); bar.setContentsMargins(20,10,14,10); bar.setSpacing(10)
        mark=QLabel('R',header); mark.setObjectName('brandMark'); mark.setFixedSize(36,36); mark.setAlignment(Qt.AlignCenter)
        bar.addWidget(mark)
        brand=QVBoxLayout();brand.setSpacing(0)
        brand.addWidget(label('REMIX','dashboardBrand'))
        tagline=label('Tạo nội dung từ kho của bạn','mutedText');tagline.setWordWrap(False);brand.addWidget(tagline)
        bar.addLayout(brand);bar.addSpacing(14)
        self.save_button=PushButton(FluentIcon.SAVE,'Lưu thiết lập',header)
        self.save_button.clicked.connect(self.save_preferences);bar.addWidget(self.save_button)
        self.save_status=label('Tự động ghi nhớ','mutedText');bar.addWidget(self.save_status)
        bar.addStretch()
        self.host.btn_theme=PushButton('Giao diện tối',header);self.host.btn_theme.clicked.connect(self.host._toggle_theme)
        bar.addWidget(self.host.btn_theme)
        self.settings_button=PushButton(FluentIcon.SETTING,'Cài đặt',header)
        self.settings_button.clicked.connect(self.open_settings);bar.addWidget(self.settings_button)
        self.export_button=PrimaryPushButton(FluentIcon.SEND,'Xuất',header)
        self.export_button.setMinimumWidth(110);self.export_button.clicked.connect(self.open_export)
        bar.addWidget(self.export_button)

        self.canvas=QWidget(self);self.canvas.setObjectName('dashboardCanvas')
        grid=QGridLayout(self.canvas);grid.setContentsMargins(22,18,22,10);grid.setSpacing(16)
        self.pages=[];self.page_layouts=[]
        for index in range(3):
            page=QWidget(self.canvas);page.setObjectName('dashboardColumn')
            layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
            self.pages.append(page);self.page_layouts.append(layout)
        grid.addWidget(self.pages[0],0,0,2,1);grid.addWidget(self.pages[1],0,1);grid.addWidget(self.pages[2],1,1)
        grid.setColumnStretch(0,1);grid.setColumnStretch(1,1);grid.setRowStretch(1,1)
        outer.addWidget(self.canvas,1)
        footer=QHBoxLayout();footer.setContentsMargins(24,0,24,12)
        self.footer_hint=label('Chọn nguồn để bắt đầu.','footerHint')
        self.state_label=label('Đang thiết lập','stateBadge')
        footer.addWidget(self.footer_hint,1);footer.addWidget(self.state_label);outer.addLayout(footer)
        self._build_sources();self._build_settings();self._build_review();self._build_export()
        self.apply_theme(isDarkTheme())

    def _build_sources(self):
        layout=self.page_layouts[0]
        self.host.source_panel=SourcePanel(self.pages[0]);self.host.audio_panel=AudioPanel(self.pages[0])
        self.host.audio_panel.export_selector.hide()
        self.video_toggle, self.video_body = self._source_toggle(self.host.source_panel, 'Video nguồn')
        self.audio_toggle, self.audio_body = self._source_toggle(self.host.audio_panel, 'Âm thanh nguồn')
        target=self.host.audio_panel.get_export_target()
        self.video_toggle.setChecked(target in ('video_only','video_with_random_audio'))
        self.audio_toggle.setChecked(target in ('audio_only','video_with_random_audio'))
        self.mode_buttons={key:_SourceSelection(self.video_toggle,self.audio_toggle,key)
                           for key in ('video_only','audio_only','video_with_random_audio','none')}
        self.video_toggle.toggled.connect(self._source_selection_changed)
        self.audio_toggle.toggled.connect(self._source_selection_changed)
        self.video_toggle.setToolTip('Bật kho video cho lần xuất này')
        self.audio_toggle.setToolTip('Bật kho nhạc: chọn cùng Video để ghép nhạc vào video')
        for panel in (self.host.source_panel,self.host.audio_panel):
            panel.btn_browse.setText('Chọn…');panel.btn_scan.setText('Kiểm tra lại')
            panel.folder_edit.setMinimumWidth(100)
            panel.layout().setContentsMargins(18,16,18,16)
            layout.addWidget(panel)
        self.source_list_dialog=QDialog(self.host);self.source_list_dialog.setWindowTitle('Danh sách video nguồn')
        self.source_list_dialog.resize(900,520)
        listing=QVBoxLayout(self.source_list_dialog);listing.addWidget(self.host.source_panel.table)
        self.host.source_panel.table.setMaximumHeight(16777215);self.host.source_panel.table.show()
        self.host.source_panel.btn_details.toggled.disconnect()
        self.host.source_panel.btn_details.setCheckable(False)
        self.host.source_panel.btn_details.clicked.connect(self.source_list_dialog.open)
        self.source_hint=label('Chọn thư mục. Nguồn và kế hoạch sẽ được kiểm tra ngầm.','sourceHint')
        layout.addWidget(self.source_hint);layout.addStretch()

    def _source_toggle(self, panel, title):
        layout=panel.layout()
        header=layout.itemAt(0).layout()
        old_title=header.takeAt(0).widget()
        old_title.hide();old_title.deleteLater()
        toggle=CheckBox(title,panel);toggle.setObjectName('sourceToggle')
        header.insertWidget(0,toggle)
        body=QWidget(panel);body_layout=QVBoxLayout(body)
        body_layout.setContentsMargins(0,0,0,0);body_layout.setSpacing(10)
        while layout.count()>1:
            item=layout.takeAt(1)
            if item.widget():
                body_layout.addWidget(item.widget())
            elif item.layout():
                nested=item.layout();nested.setParent(None);body_layout.addLayout(nested)
            else:
                body_layout.addItem(item)
        layout.addWidget(body)
        return toggle,body

    def _source_selection_changed(self):
        if self._running:
            self.refresh()
            return
        video,audio=self.video_toggle.isChecked(),self.audio_toggle.isChecked()
        target='video_with_random_audio' if video and audio else 'video_only' if video else 'audio_only' if audio else 'none'
        self.select_mode(target)

    def _refresh_source_visibility(self,value):
        video=value.target in ('video_only','video_with_random_audio')
        audio=value.target in ('audio_only','video_with_random_audio')
        self.host.source_panel.show();self.host.audio_panel.show()
        self.video_body.setVisible(video);self.audio_body.setVisible(audio)
        self.host.source_panel.auto_scan.set_enabled(video)
        self.host.audio_panel.auto_scan.set_enabled(audio)

    def _build_settings(self):
        layout=self.page_layouts[1]
        self.host.cut_panel=CutConfigPanel(self.pages[1]);self.host.output_panel=OutputConfigPanel(self.pages[1])
        self.host.cut_panel.btn_advanced.hide();self.host.cut_panel.lbl_pool_info.hide()
        self.host.cut_panel.lbl_summary.setObjectName('mutedText')
        self.host.cut_panel.layout().setSpacing(8)
        layout.addWidget(self.host.cut_panel)
        self.audio_settings_card=QFrame(self.host);self.audio_settings_card.setObjectName('settingsCard')
        audio=QVBoxLayout(self.audio_settings_card);audio.addWidget(label('Cách ghép âm thanh','sectionHeader'))
        audio.addWidget(self.host.audio_panel.options_widget)
        self.settings_dialog=SettingsDialog(self.host,self.audio_settings_card)
        self.session_hint=label('','sessionHint');self.session_hint.hide()
        self.session_preferences=label('','mutedText');layout.addWidget(self.session_preferences)
        shortcut=PushButton('Chất lượng và nơi lưu…',self.pages[1]);shortcut.clicked.connect(self.open_settings)
        layout.addWidget(shortcut,0,Qt.AlignLeft)

    def _build_review(self):
        layout=self.page_layouts[2]
        card=QFrame(self.pages[2]);card.setObjectName('settingsCard')
        body=QVBoxLayout(card);body.setContentsMargins(18,16,18,14);body.setSpacing(9)
        title=QHBoxLayout();title.addWidget(label('Kiểm tra trước khi xuất','sectionHeader'));title.addStretch()
        self.plan_button=PushButton('Xem kế hoạch',card);title.addWidget(self.plan_button);body.addLayout(title)
        self.summary_card=QFrame(card);summary=QVBoxLayout(self.summary_card);summary.setContentsMargins(0,0,0,2);summary.setSpacing(4)
        self.summary_title=label('Lần xuất sắp tới','dashboardSummary');self.summary_detail=label('','summaryDetail');self.summary_output=label('','mutedText')
        summary.addWidget(self.summary_title);summary.addWidget(self.summary_detail)
        self.summary_output.hide();body.addWidget(self.summary_card)
        self.checks_widget=QWidget(card);self.checks_layout=QVBoxLayout(self.checks_widget)
        self.checks_layout.setContentsMargins(0,0,0,0);self.checks_layout.setSpacing(6);body.addWidget(self.checks_widget)
        self.plan_notice=label('','planNotice');body.addWidget(self.plan_notice)
        self.retry_button=PushButton('Kiểm tra lại',card);self.retry_button.clicked.connect(self.retry_prepare)
        body.addWidget(self.retry_button,0,Qt.AlignLeft);layout.addWidget(card);layout.addStretch()
        self.plan_dialog=QDialog(self.host);self.plan_dialog.setWindowTitle('Kế hoạch ghép');self.plan_dialog.resize(1000,600)
        plan=QVBoxLayout(self.plan_dialog);self.host.table_view=CutTableView(self.plan_dialog);plan.addWidget(self.host.table_view)
        self.plan_button.clicked.connect(self.plan_dialog.open)

    def _build_export(self):
        self.export_dialog=QDialog(self.host);self.export_dialog.setWindowTitle('Xuất nội dung · Remix')
        self.export_dialog.resize(920,640);self.export_dialog.setMinimumSize(780,560)
        self.export_dialog.setWindowModality(Qt.WindowModal)
        container=QVBoxLayout(self.export_dialog);container.setContentsMargins(22,20,22,18);container.setSpacing(14)
        self.dialog_title=label('Xuất nội dung','pageTitle');container.addWidget(self.dialog_title)
        content=QHBoxLayout();content.setSpacing(20)
        preview=QVBoxLayout();preview.setSpacing(8)
        self.preview=QLabel('REMIX\nKhung hình đại diện',self.export_dialog);self.preview.setObjectName('exportPreview')
        self.preview.setAlignment(Qt.AlignCenter);self.preview.setFixedSize(300,169)
        preview.addWidget(self.preview);preview.addWidget(label('Khung hình đại diện từ kế hoạch','mutedText'))
        self.preview_source=label('','mutedText');self.preview_source.setMaximumWidth(300);preview.addWidget(self.preview_source);preview.addStretch()
        content.addLayout(preview)
        self.export_stack=QStackedWidget(self.export_dialog);content.addWidget(self.export_stack,1);container.addLayout(content,1)
        confirm_scroll=QScrollArea(self.export_dialog);confirm_scroll.setWidgetResizable(True);confirm_scroll.setFrameShape(QFrame.NoFrame)
        confirm_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.confirm_page=QWidget();self.confirm_page.setObjectName('settingsBody');confirm=QVBoxLayout(self.confirm_page);confirm.setContentsMargins(0,0,8,0);confirm.setSpacing(12)
        self.export_summary=label('','exportSummary');confirm.addWidget(self.export_summary)
        form=QFormLayout();form.setVerticalSpacing(10);form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.confirm_prefix=LineEdit();self.confirm_prefix.setPlaceholderText('Tự đặt tên theo ngày')
        self._sync_text(self.confirm_prefix,self.host.output_panel.edit_prefix);form.addRow('Tên tệp:',self.confirm_prefix)
        folder=QWidget();folder_row=QHBoxLayout(folder);folder_row.setContentsMargins(0,0,0,0);folder_row.setSpacing(5)
        self.confirm_folder=LineEdit();self._sync_text(self.confirm_folder,self.host.output_panel.edit_outdir);folder_row.addWidget(self.confirm_folder,1)
        browse=PushButton('…');browse.setFixedWidth(32);browse.clicked.connect(self.host.output_panel._choose_outdir);folder_row.addWidget(browse)
        form.addRow('Lưu tại:',folder)
        self.confirm_count=SpinBox();self.confirm_count.setRange(1,100)
        self.confirm_count.setValue(self.host.cut_panel.spin_batch_count.value())
        self.confirm_count.valueChanged.connect(self.host.cut_panel.spin_batch_count.setValue)
        self.host.cut_panel.spin_batch_count.valueChanged.connect(self.confirm_count.setValue)
        form.addRow('Số lượng:',self.confirm_count)
        self.video_rows=[]
        for title,source in [('Độ phân giải:',self.host.output_panel.combo_res),('Chất lượng:',self.host.output_panel.combo_quality),
                             ('Tốc độ xử lý:',self.host.output_panel.combo_speed),('Khung hình:',self.host.output_panel.combo_fps),
                             ('Bộ mã hóa:',self.host.output_panel.combo_hw)]:
            combo=self._proxy_combo(source);caption=QLabel(title);form.addRow(caption,combo);self.video_rows.extend((caption,combo))
        self.audio_format=self._proxy_combo(self.host.audio_panel.combo_audio_format);self.audio_format_label=QLabel('Định dạng:')
        form.addRow(self.audio_format_label,self.audio_format);confirm.addLayout(form);confirm.addStretch()
        confirm_scroll.setWidget(self.confirm_page);self.export_stack.addWidget(confirm_scroll)
        progress_page=QWidget();progress_page.setObjectName('settingsBody');progress=QVBoxLayout(progress_page);progress.setContentsMargins(0,0,0,0);progress.setSpacing(10)
        self.output_name=label('','summaryDetail');progress.addWidget(self.output_name)
        self.host.progress_panel=ProgressPanel(progress_page);self.host.progress_panel.prep_widget.hide();self.host.progress_panel.btn_start.hide()
        self.host.progress_panel.lbl_overall.setWordWrap(True);self.host.progress_panel.console.setMinimumHeight(120)
        progress.addWidget(self.host.progress_panel)
        self.result_label=label('','resultMessage');progress.addWidget(self.result_label)
        actions=QHBoxLayout();self.open_output_button=PushButton('Mở kết quả',progress_page)
        self.open_output_button.clicked.connect(self.host.output_panel._open_outdir)
        self.new_run_button=PushButton('Lượt mới',progress_page);self.new_run_button.clicked.connect(self.new_run)
        actions.addWidget(self.open_output_button);actions.addWidget(self.new_run_button);progress.addLayout(actions);progress.addStretch()
        self.export_stack.addWidget(progress_page)
        bottom=QHBoxLayout();self.confirm_hint=label('','mutedText');bottom.addWidget(self.confirm_hint,1)
        self.dialog_close=PushButton('Đóng',self.export_dialog);self.dialog_close.clicked.connect(self.export_dialog.reject);bottom.addWidget(self.dialog_close)
        self.confirm_button=PrimaryPushButton('Xuất',self.export_dialog);self.confirm_button.setMinimumWidth(100)
        self.confirm_button.clicked.connect(self.confirm_export);bottom.addWidget(self.confirm_button);container.addLayout(bottom)

    def _proxy_combo(self,source):
        combo=ComboBox(self.export_dialog)
        for i in range(source.count()):combo.addItem(source.itemText(i),userData=source.itemData(i))
        combo.setCurrentIndex(source.currentIndex());combo.currentIndexChanged.connect(source.setCurrentIndex)
        source.currentIndexChanged.connect(combo.setCurrentIndex)
        return combo

    @staticmethod
    def _sync_text(target,source):
        target.setText(source.text());target.textChanged.connect(source.setText);source.textChanged.connect(target.setText)

    def _create_check_row(self,check,prepared):
        row=QFrame(self.checks_widget);row.setObjectName('checkRow');row.setProperty('state',check.state)
        layout=QHBoxLayout(row);layout.setContentsMargins(10,7,10,7)
        layout.addWidget(label(check.title,'checkDetail'),1)
        text={'ready':'Sẵn sàng','warning':'Lưu ý','action':'Cần bổ sung'}[check.state]
        status=label(text,'checkStatus');layout.addWidget(status)
        row.setToolTip(check.detail);return row

    def _refresh_navigation(self,value,readiness,prepared):
        self.export_button.setEnabled(True)
        self.export_button.setText('Xem tiến trình' if self._running else 'Xem kết quả' if self._finished else 'Xuất')
        self.export_button.setToolTip('Mở tiến trình đang chạy' if self._running else readiness.next_action)
        self.save_button.setEnabled(not self._running)
        self.video_toggle.setEnabled(not self._running)
        self.audio_toggle.setEnabled(not self._running)
        self.footer_hint.setText('Đang xuất nội dung. Có thể ẩn bảng tiến trình và mở lại bằng nút phía trên.' if self._running else
                                'Đang chuẩn bị kế hoạch trong nền…' if self._preparing else
                                'Nguồn và kế hoạch đã sẵn sàng. Nhấn Xuất để kiểm tra lần cuối.' if prepared else readiness.next_action)
        self.retry_button.setVisible(bool(self._plan_error or self._result_state=='Cần kiểm tra lại') and not self._preparing)
        self.plan_button.setEnabled(prepared or self._running or self._finished)
        if self.export_dialog.isVisible() and prepared and not self._running:
            self._request_preview()
        enabled=prepared and readiness.can_prepare and not self._running and not self.settings_dialog.cache_busy
        self.confirm_button.setEnabled(enabled);self.confirm_button.setText(f'Xuất {value.batch_count} tệp')
        self.confirm_hint.setText('' if self._running else 'Sẵn sàng xuất' if enabled else 'Đang chuẩn bị kế hoạch…' if self._preparing else readiness.next_action)
        self.confirm_page.setEnabled(not self._running)
        audio_only=value.target=='audio_only'
        for widget in self.video_rows:widget.setVisible(not audio_only)
        self.audio_format.setVisible(audio_only);self.audio_format_label.setVisible(audio_only)
        if self._running or self._finished or self.export_stack.currentIndex()==1:
            self.confirm_button.hide()
        else:self.confirm_button.show()

    def refresh(self, *args):
        super().refresh(*args)
        if self.host.audio_panel.get_export_target()=='none':
            self.summary_title.setText('Chọn nội dung cần xuất')
            self.summary_detail.clear()
            self.plan_notice.setText('Tích Video, Âm thanh hoặc cả hai để bắt đầu.')
            self.export_summary.setText('Chưa chọn nội dung xuất')
        elif self.validated() and not self._preparing:
            self.plan_notice.setText('Kế hoạch đã sẵn sàng. Mở Xem kế hoạch để kiểm tra thứ tự ghép.')

    def open_export(self):
        self.refresh()
        if self._running or self._finished:
            self.export_stack.setCurrentIndex(1)
            self.dialog_title.setText('Đang xuất nội dung' if self._running else 'Kết quả xuất')
            self.confirm_button.hide()
        else:
            self.export_stack.setCurrentIndex(0);self.dialog_title.setText('Kiểm tra và xuất')
            self.confirm_button.show()
        self.dialog_close.setText('Ẩn tiến trình' if self._running else 'Đóng')
        self.export_dialog.open();self._request_preview()

    def confirm_export(self):
        self.refresh()
        if self.validated() and self.confirm_button.isEnabled() and not self._running:
            self.host.progress_panel.btn_start.click()

    def _request_preview(self):
        target=self.host.audio_panel.get_export_target()
        if target=='audio_only':
            self.preview.clear();self.preview.setText('♫\nPlaylist âm thanh');self.preview_source.setText('Ghép nhạc theo kế hoạch đã kiểm tra.');return
        plans=self.host.current_batch_plans
        if not plans or not plans[0]:
            self.preview.clear();self.preview.setText('REMIX\nChưa có kế hoạch');self.preview_source.clear();return
        clip=plans[0][0];signature=(clip.source_video_path,clip.start_sec)
        if self.preview_worker and self.preview_worker.isRunning():return
        if signature==self._preview_signature:return
        self._preview_signature=signature;self.preview.clear();self.preview.setText('Đang lấy khung hình…')
        self.preview_source.setText(clip.source_video_name)
        self.preview_worker=PreviewWorker(clip.source_video_path,clip.start_sec,self)
        self.preview_worker.ready.connect(self._accept_preview);self.preview_worker.finished.connect(self._preview_finished)
        self.preview_worker.start()

    def _accept_preview(self,image):
        worker=self.sender();plans=self.host.current_batch_plans
        if plans and plans[0] and (plans[0][0].source_video_path,plans[0][0].start_sec)==(worker.path,worker.start_seconds):
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def _preview_finished(self):
        worker=self.sender()
        if self.preview_worker is worker:self.preview_worker=None
        worker.deleteLater()
        if self.host.audio_panel.get_export_target()!='audio_only' and (not self.preview.pixmap() or self.preview.pixmap().isNull()):
            self.preview.setText('REMIX\nChưa có khung hình xem trước')

    def go_to(self,index):
        # Existing engine callbacks use these intents; the dashboard stays in place.
        if index==3:self.open_export()
        elif index==1 and not self._running:self.export_dialog.hide()
        self.refresh()

    def retry_prepare(self):
        self._failed_signature=None;self.prepare()

    def save_preferences(self):
        if settings.save():self.save_status.setText('Đã lưu thiết lập')
        else:
            self.save_status.setText('Chưa lưu được — kiểm tra quyền ghi')
            self.save_status.setToolTip(settings.last_error)

    def apply_theme(self,dark):
        super().apply_theme(dark)
        style=dashboard_style_sheet(dark);self.host.setStyleSheet(style)
        self.header.apply_theme(dark)
        for dialog in (self.settings_dialog,self.export_dialog,self.plan_dialog,self.source_list_dialog):dialog.setStyleSheet(style)

    def running_changed(self,running):
        super().running_changed(running)
        if running:
            self.export_stack.setCurrentIndex(1);self.confirm_button.hide();self.dialog_title.setText('Đang xuất nội dung')
            self.dialog_close.setText('Ẩn tiến trình')
            self.host.progress_panel.lbl_overall.setText('Đang chuẩn bị bộ xử lý…')

    def attach_worker(self,worker):
        super().attach_worker(worker)
        worker.video_started.connect(lambda index,total,path:self.output_name.setText(f'Tệp {index}/{total} · {os.path.basename(path)}'))

    def finished(self,success_count,total_count):
        super().finished(success_count,total_count)
        self.dialog_title.setText('Xuất hoàn tất' if self._finished else 'Kết quả xử lý')
        self.dialog_close.setText('Đóng');self.export_stack.setCurrentIndex(1);self.confirm_button.hide()

    def stop_background(self):
        super().stop_background()
        if self.preview_worker:self.preview_worker.stop()
        for dialog in (self.export_dialog,self.plan_dialog,self.source_list_dialog):dialog.close()

    def background_running(self):
        return super().background_running() or bool(self.preview_worker and self.preview_worker.isRunning())


class _SourceSelection:
    """Keep source checkboxes synchronized with the persisted export target."""
    def __init__(self,video,audio,key):self.video,self.audio,self.key=video,audio,key
    def setChecked(self,checked):
        if checked:
            with QSignalBlocker(self.video),QSignalBlocker(self.audio):
                self.video.setChecked(self.key in ('video_only','video_with_random_audio'))
                self.audio.setChecked(self.key in ('audio_only','video_with_random_audio'))
