def workflow_style_sheet(dark=False):
    """Palette for the guided shell and the existing Fluent controls."""
    palette = ({
        "canvas": "#15191F", "surface": "#20262F", "sidebar": "#1A2028",
        "ink": "#EDF2F7", "muted": "#A9B6C7", "line": "#35404D",
        "accent": "#75B4FF", "tint": "#243A54", "warning": "#DDBB76",
        "alert": "#F5A59A", "success": "#98C9B6",
    } if dark else {
        "canvas": "#F5F7FA", "surface": "#FFFFFF", "sidebar": "#EDF1F6",
        "ink": "#1E2B3C", "muted": "#607086", "line": "#DCE3EC",
        "accent": "#2465B7", "tint": "#EAF2FD", "warning": "#8A6322",
        "alert": "#AC3F35", "success": "#30765D",
    })
    return GLOBAL_STYLE_SHEET + """
    QWidget#workflowRoot, QWidget#workflowWorkspace, QWidget#stepBody, QWidget#settingsBody, QScrollArea {{
        background: {canvas}; color: {ink};
    }}
    QWidget {{ font-family: "Segoe UI", sans-serif; font-size: 13px; }}
    QLabel {{ color: {ink}; background: transparent; border: none; }}
    QDialog {{ background: {canvas}; color: {ink}; }}
    QTabWidget::pane {{ border: 1px solid {line}; border-radius: 8px; background: {surface}; }}
    QTabBar::tab {{ color: {muted}; padding: 10px 16px; background: transparent; }}
    QTabBar::tab:selected {{ color: {accent}; border-bottom: 2px solid {accent}; }}
    QLabel#sessionHint {{ background: {tint}; color: {accent}; padding: 20px; border-radius: 10px; font-size: 18px; font-weight: 600; }}
    QFrame#workflowSidebar {{ background: {sidebar}; border: none; border-right: 1px solid {line}; }}
    QLabel#brandTitle {{ font-size: 25px; font-weight: 700; letter-spacing: 3px; color: {accent}; }}
    QLabel#pageTitle {{ font-size: 25px; font-weight: 650; padding-top: 1px; }}
    QLabel#pageDescription {{ color: {muted}; padding-bottom: 5px; }}
    QLabel#eyebrow {{ font-size: 11px; font-weight: 650; color: {muted}; letter-spacing: 1px; }}
    QLabel#stateBadge {{ color: {accent}; background: {tint}; border-radius: 11px; padding: 5px 12px; font-size: 12px; }}
    QLabel#mutedText, QLabel#footerHint, QLabel#sourceHint {{ color: {muted}; font-size: 12px; }}
    QLabel#sidebarHintTitle {{ font-weight: 600; font-size: 13px; }}
    QLabel#sectionHeader {{ color: {ink}; font-size: 16px; font-weight: 600; }}
    QPushButton#stepButton {{ background: transparent; color: {muted}; border: none; border-radius: 8px; text-align: left; padding: 9px 11px; line-height: 1.6; }}
    QPushButton#stepButton:checked {{ background: {surface}; color: {accent}; font-weight: 600; }}
    QPushButton#stepButton:hover {{ background: {tint}; }}
    QPushButton#stepButton:disabled {{ color: {muted}; background: transparent; }}
    QPushButton#modeCard {{ background: {surface}; color: {ink}; text-align: left; border: 1px solid {line}; border-radius: 10px; padding: 17px 15px; font-size: 13px; }}
    QPushButton#modeCard:checked {{ background: {tint}; border: 2px solid {accent}; padding: 16px 14px; color: {accent}; font-weight: 600; }}
    QPushButton#modeCard:hover {{ border-color: {accent}; }}
    QFrame#settingsCard, QFrame#summaryCard {{ background: {surface}; border: 1px solid {line}; border-radius: 10px; }}
    QLabel#summaryTitle {{ font-size: 20px; font-weight: 600; }}
    QLabel#summaryDetail {{ font-size: 13px; color: {muted}; }}
    QLabel#exportSummary {{ font-size: 15px; font-weight: 600; padding: 12px 0; }}
    QLabel#planNotice, QLabel#resultMessage {{ color: {muted}; padding: 10px 0; }}
    QFrame#checkRow {{ background: {surface}; border: 1px solid {line}; border-radius: 7px; }}
    QFrame#checkRow[state="ready"] QLabel#checkStatus {{ color: {success}; font-weight: 600; }}
    QFrame#checkRow[state="warning"] QLabel#checkStatus {{ color: {warning}; font-weight: 600; }}
    QFrame#checkRow[state="action"] QLabel#checkStatus {{ color: {alert}; font-weight: 600; }}
    QLabel#checkDetail {{ font-size: 12px; }}
    QFrame[statCard="true"] {{ background: {canvas}; border: none; border-radius: 7px; }}
    QLabel#statNumber {{ color: {accent}; font-size: 22px; border: none; font-weight: 650; }}
    QLabel#statLabel {{ color: {muted}; font-size: 11px; border: none; }}
    QTableWidget {{ background: {surface}; color: {ink}; gridline-color: {line}; border: 1px solid {line}; }}
    QHeaderView::section {{ background: {canvas}; color: {muted}; border: none; padding: 8px; font-size: 12px; }}
    QToolButton {{ color: {accent}; padding: 6px 0; text-align: left; border: none; background: transparent; }}
    QCheckBox {{ color: {ink}; spacing: 8px; }}
    """.format(**palette)
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


def dashboard_style_sheet(dark=False):
    surface = "#20262F" if dark else "#FFFFFF"
    line = "#35404D" if dark else "#E2E7EE"
    canvas = "#15191F" if dark else "#F4F6FA"
    accent = "#75B4FF" if dark else "#2465B7"
    return workflow_style_sheet(dark) + f"""
    QWidget#dashboardCanvas, QWidget#dashboardColumn {{ background: {canvas}; }}
    QWidget#dashboardHeader {{ background: {surface}; border-bottom: 1px solid {line}; }}
    QLabel#brandMark {{ background: #2465B7; color: white; border-radius: 10px; font-size: 23px; font-weight: 700; }}
    QLabel#dashboardBrand {{ color: {accent}; font-size: 18px; font-weight: 700; letter-spacing: 2px; }}
    QLabel#dashboardSummary {{ font-size: 15px; font-weight: 600; }}
    QCheckBox#sourceToggle {{ font-size: 16px; font-weight: 600; spacing: 9px; }}
    QWidget#titleBarContent {{ background: transparent; }}
    QLabel#sessionHint {{ padding: 10px; font-size: 14px; }}
    QLabel#planNotice {{ padding: 1px 0; font-size: 12px; }}
    QLabel#exportSummary {{ padding: 0 0 5px; font-size: 13px; }}
    QLabel#exportPreview {{ background: #10161F; color: #A8B9D0; border-radius: 9px; font-size: 19px; }}
    QLabel#statNumber {{ font-size: 20px; }}
    QFrame#checkRow {{ border-radius: 6px; }}
    """
