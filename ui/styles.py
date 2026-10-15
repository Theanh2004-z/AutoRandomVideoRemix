"""
Custom Styles and Theme Helpers for AutoRandomVideoRemix.
Provides Fluent-inspired styling, color accents, and dark/light mode helpers.
"""

# Color Palette
ACCENT_COLOR = "#0078D4"       # Windows Fluent Blue
SUCCESS_COLOR = "#107C41"      # Emerald Green
WARNING_COLOR = "#D83B01"      # Amber Orange
ERROR_COLOR = "#E81123"        # Crimson Red
CARD_BG_LIGHT = "#FFFFFF"
CARD_BG_DARK = "#202020"
BORDER_LIGHT = "#E5E5E5"
BORDER_DARK = "#333333"

GLOBAL_STYLE_SHEET = """
/* Base typography and clean margins */
QWidget {
    font-family: "Segoe UI Variable Display", "Segoe UI", "Arial", sans-serif;
    font-size: 13px;
}

QGroupBox {
    font-weight: 600;
    font-size: 14px;
    border: 1px solid #E0E0E0;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 14px;
    background-color: #FAFAFA;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 12px;
    color: #0078D4;
}

QPlainTextEdit#logConsole {
    background-color: #1E1E1E;
    color: #D4D4D4;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #333333;
    border-radius: 6px;
    padding: 8px;
}

QTableWidget {
    gridline-color: #E8E8E8;
    border-radius: 6px;
    selection-background-color: #E0EEF9;
    selection-color: #000000;
}

QHeaderView::section {
    background-color: #F3F3F3;
    font-weight: 600;
    padding: 6px;
    border: 1px solid #E0E0E0;
}

QLabel#statNumber {
    font-size: 20px;
    font-weight: bold;
    color: #0078D4;
}

QLabel#statLabel {
    font-size: 11px;
    color: #666666;
}

QLabel#sectionHeader {
    font-size: 15px;
    font-weight: bold;
    color: #1A1A1A;
}
"""
