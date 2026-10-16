"""Integrated application title bar, retaining native Windows resize and snap."""
import os
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QWidget, QAbstractButton, QLineEdit, QAbstractSpinBox, QComboBox
from qframelesswindow import FramelessMainWindow, TitleBar

# Offscreen rendering has no real Windows HWND for the native frame helper.
_NATIVE_FRAME = os.environ.get('QT_QPA_PLATFORM','').lower() not in ('offscreen','minimal')
_FrameBase = FramelessMainWindow if _NATIVE_FRAME else QMainWindow


class RemixWindow(_FrameBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        if not _NATIVE_FRAME:
            self.setWindowFlag(Qt.FramelessWindowHint, True)

    def install_title_bar(self, title_bar):
        if _NATIVE_FRAME:
            self.setTitleBar(title_bar)
        else:
            self.titleBar = title_bar
        self.setMenuWidget(title_bar)
        title_bar.raise_()


class RemixTitleBar(TitleBar):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('dashboardHeader')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(64)
        self.hBoxLayout.takeAt(0)  # Replace the library's leading stretch with app controls.
        self.content = QWidget(self)
        self.content.setObjectName('titleBarContent')
        self.hBoxLayout.insertWidget(0, self.content, 1)
        for button, name in ((self.minBtn,'Thu nhỏ'),(self.maxBtn,'Phóng to / Khôi phục'),(self.closeBtn,'Đóng ứng dụng')):
            button.setFixedSize(46,32)
            button.setToolTip(name)
            button.setAccessibleName(name)
        self.maxBtn.clicked.disconnect()
        self.maxBtn.clicked.connect(self.toggle_maximized)

    def toggle_maximized(self):
        window = self.window()
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def _isDragRegion(self, pos):
        child = self.childAt(pos)
        while child is not None and child is not self:
            if isinstance(child,(QAbstractButton,QLineEdit,QAbstractSpinBox,QComboBox)):
                return False
            child = child.parentWidget()
        return self.rect().contains(pos)

    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton and self.canDrag(event.position().toPoint()):
            handle=self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
            event.accept()
            return
        QWidget.mousePressEvent(self,event)

    def mouseMoveEvent(self,event):
        QWidget.mouseMoveEvent(self,event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.LeftButton and self.canDrag(event.position().toPoint()):
            self.toggle_maximized()
            event.accept()
            return
        QWidget.mouseDoubleClickEvent(self,event)

    def apply_theme(self,dark):
        ink='#EDF2F7' if dark else '#1E2B3C'
        for button in (self.minBtn,self.maxBtn,self.closeBtn):
            button.setNormalColor(ink)
            button.setHoverColor('#FFFFFF' if button is self.closeBtn else ink)
            button.setPressedColor('#FFFFFF' if button is self.closeBtn else ink)
