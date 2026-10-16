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

    from core.version import APP_NAME, APP_VERSION

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Antigravity")

    # An independent helper can recover even if the previous directory swap
    # was interrupted. Never construct windows against an unfinished install.
    from core.update_manager import update_manager
    try:
        if update_manager.recover_interrupted_update():
            return
    except Exception as error:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Phục hồi cập nhật", str(error))
        return

    # MainWindow restores the saved theme before constructing its controls.
    window = MainWindow()
    window.show()

    # Confirm health handshake if launched by updater
    if "--update-handshake" in sys.argv:
        try:
            idx = sys.argv.index("--update-handshake")
            if idx + 1 < len(sys.argv):
                handshake_path = sys.argv[idx + 1]
                with open(handshake_path, "w", encoding="utf-8") as f:
                    f.write(f"HEALTHY v{APP_VERSION} PID={os.getpid()}\n")
        except Exception as e:
            print(f"Warning: Failed to write update handshake: {e}", file=sys.stderr)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
