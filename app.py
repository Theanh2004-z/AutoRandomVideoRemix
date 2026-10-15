"""
Application Entry Point for AutoRandomVideoRemix.
"""
import sys
import os

# Ensure current folder is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Ensure bundled bin directory (ffmpeg, ffprobe) is in system PATH
search_dirs = [
    os.path.join(current_dir, "bin"),
]
if getattr(sys, 'frozen', False):
    search_dirs.insert(0, os.path.join(os.path.dirname(sys.executable), "bin"))
    if hasattr(sys, '_MEIPASS'):
        search_dirs.insert(0, os.path.join(sys._MEIPASS, "bin"))

for d in search_dirs:
    if d and os.path.isdir(d) and d not in os.environ.get("PATH", ""):
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import setTheme, Theme

from ui.main_window import MainWindow


def main():
    # Enable High DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("AutoRandomVideoRemix")
    app.setOrganizationName("Antigravity")

    # Set modern Light theme as default (user can switch in header)
    setTheme(Theme.LIGHT)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
