"""面板主题。

刻意只做一套浅色皮肤并把颜色集中在 THEME 里：
面板是常驻在别人屏幕上的东西，主题太多只会带来"哪个主题在哪个窗口下
看不清"的维护负担，不带来价值。
"""

from __future__ import annotations
from pathlib import Path

DARK = {
    "bg": "#1b1f27",
    "bg_soft": "#232833",
    "cell": "#2a303b",
    "cell_hover": "#39414f",
    "border": "#3a4150",
    "text": "#eef1f6",
    "text_dim": "#98a2b3",
    "accent": "#5b9bff",
    "accent_soft": "#29456e",
    "group": "#8b95a6",
    "shadow": "#000000",
}

LIGHT = {
    "bg": "#f7f9fc",
    "bg_soft": "#ffffff",
    "cell": "#ffffff",
    "cell_hover": "#eef4fe",
    "border": "#8493a8",
    "text": "#1a212c",
    "text_dim": "#475569",
    "accent": "#2458c5",
    "accent_soft": "#e8f1ff",
    "group": "#475569",
    "shadow": "#8a94a6",
}


def palette(name: str) -> dict:
    # Keep the app visually consistent; legacy configs may still say "dark".
    return LIGHT


def qss(p: dict, extra: str = "") -> str:
    return f"""
    QWidget {{
        background: {p["bg"]};
        color: {p["text"]};
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QFrame#PanelRoot {{
        background: {p["bg"]};
        border: 1px solid {p["border"]};
        border-radius: 14px;
    }}
    QWidget#PanelHeader {{ background: transparent; }}
    QLabel#PanelBrand {{
        background: transparent;
        color: {p["text"]};
        font-size: 15px;
        font-weight: 700;
        padding-left: 2px;
    }}
    QPushButton#CategoryTab {{ padding: 6px 4px; font-size: 12px; border-radius: 6px; }}
    QPushButton#CategoryTab:checked {{ background: #2458c5; color: white; border-color: #1946a3; }}
    QPushButton#PanelToolButton {{
        background: transparent;
        border: none;
        border-radius: 7px;
        color: {p["text_dim"]};
        font-size: 17px;
        min-width: 28px;
        min-height: 27px;
        padding: 0;
    }}
    QPushButton#PanelToolButton:hover {{
        background: {p["cell_hover"]};
        color: {p["text"]};
    }}
    QLineEdit {{
        background: {p["bg_soft"]};
        border: 1px solid {p["border"]};
        border-radius: 8px;
        padding: 7px 11px;
        color: {p["text"]};
        selection-background-color: {p["accent"]};
        selection-color: #ffffff;
    }}
    QLineEdit:focus {{ border-color: {p["accent"]}; }}
    QLabel#SearchHint {{
        color: {p["text_dim"]};
        font-size: 11px;
        padding: 1px 4px 0 4px;
        background: transparent;
    }}
    QLabel#GroupTitle {{
        color: {p["group"]};
        font-size: 11px;
        font-weight: 600;
        padding: 3px 4px 1px 4px;
        background: transparent;
    }}
    QFrame#Cell {{
        background: {p["cell"]};
        border: 1px solid #c5cedb;
        border-radius: 10px;
    }}
    QFrame#Cell:hover {{
        background: {p["cell_hover"]};
        border: 1px solid {p["accent"]};
    }}
    QFrame#Cell[selected="true"] {{
        background: {p["accent_soft"]};
        border: 1px solid {p["accent"]};
    }}
    QLabel#CellIcon {{ background: transparent; font-size: 24px; }}
    QLabel#CellName {{ background: transparent; font-size: 12px; color: {p["text"]}; padding: 0 1px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 2px; }}
    QScrollBar::handle:vertical {{ background: {p["border"]}; border-radius: 4px; min-height: 28px; }}
    QScrollBar::handle:vertical:hover {{ background: {p["accent"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    {extra}
    """


def settings_qss() -> str:
    """设置中心统一白底浅灰和蓝色强调色。"""
    return """
    QWidget {
        background: #ffffff;
        color: #172033;
        font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
        font-size: 13px;
    }
    QFrame#SettingsSidebar {
        background: #f7f9fc;
        border: none;
        border-right: 1px solid #e2e8f1;
    }
    QLabel#SettingsBrand {
        color: #24334a;
        font-size: 17px;
        font-weight: 700;
        padding: 8px 12px 16px;
        background: transparent;
    }
    QListWidget#SettingsNavigation {
        background: transparent;
        border: none;
        outline: none;
        padding: 4px 8px;
    }
    QListWidget#SettingsNavigation::item {
        color: #475569;
        border-radius: 7px;
        min-height: 36px;
        padding: 0 11px;
        margin: 2px 0;
    }
    QListWidget#SettingsNavigation::item:hover { background: #edf3fc; color: #24334a; }
    QListWidget#SettingsNavigation::item:selected {
        color: #245fb8;
        background: #e8f1ff;
        border-left: 3px solid #2458c5;
        padding-left: 8px;
    }
    QStackedWidget#SettingsPages { background: #ffffff; border: none; }
    QFrame#SettingsPageHeader {
        background: #ffffff;
        border: none;
        border-bottom: 1px solid #e2e7ef;
    }
    QLabel#SettingsPageTitle {
        color: #24334a;
        font-size: 17px;
        font-weight: 700;
        background: transparent;
    }
    QLabel { background: transparent; }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextBrowser {
        background: #ffffff;
        border: 1px solid #8493a8;
        border-radius: 6px;
        padding: 6px 8px;
        selection-background-color: #2458c5;
        selection-color: #ffffff;
        color: #172033;
    }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
    QPlainTextEdit:focus { border-color: #2458c5; }
    QCheckBox {
        spacing: 8px;
        color: #334155;
        min-height: 24px;
    }
    QCheckBox::indicator {
        width: 17px;
        height: 17px;
        border: 1px solid #8493a8;
        border-radius: 4px;
        background: #ffffff;
    }
    QCheckBox::indicator:hover { border-color: #2458c5; background: #f2f7ff; }
    QCheckBox::indicator:checked {
        background: #2458c5;
        border: 1px solid #2458c5;
        image: url("__CHECK_ICON__");
    }
    QCheckBox::indicator:disabled { background: #eef1f5; border-color: #d5dbe5; }
    QPushButton {
        background: #ffffff;
        border: 1px solid #8493a8;
        border-radius: 6px;
        padding: 7px 12px;
        font-weight: 600;
        color: #172033;
    }
    QPushButton:hover { background: #edf3fc; border-color: #2458c5; }
    QPushButton#PrimaryButton {
        background: #2458c5;
        color: white;
        border: 1px solid #2458c5;
        font-weight: 600;
        padding: 8px 18px;
    }
    QPushButton#PrimaryButton:hover { background: #1946a3; }
    QPushButton#QuietButton { background: transparent; }
    QTableWidget {
        background: #ffffff;
        alternate-background-color: #f8faff;
        border: 1px solid #e0e6ef;
        border-radius: 8px;
        gridline-color: #edf0f5;
        selection-background-color: #e5efff;
        selection-color: #1e3558;
    }
    QTableWidget::item { padding: 5px 7px; }
    QTableWidget::item:selected { background: #e5efff; }
    QHeaderView::section {
        background: #f1f4f9;
        color: #475569;
        border: none;
        border-bottom: 1px solid #dfe5ee;
        padding: 8px;
        font-weight: 600;
    }
    QGroupBox {
        background: #ffffff;
        border: 1px solid #e0e6ef;
        border-radius: 8px;
        margin-top: 12px;
        padding: 10px;
        font-weight: 600;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
    QFrame#SettingsFooter { background: #ffffff; border-top: 1px solid #e1e6ef; }
    QLabel#SettingsStatus { color: #475569; padding-left: 8px; }
    QScrollBar:vertical { background: transparent; width: 9px; margin: 3px; }
    QScrollBar::handle:vertical { background: #cbd4e1; border-radius: 4px; min-height: 25px; }
    QScrollBar::add-line, QScrollBar::sub-line { height: 0; }

    QPushButton:pressed { background: #dbe7fa; border-color: #1946a3; color: #172033; }
    QPushButton:focus, QPushButton:default { border: 2px solid #2458c5; }
    QPushButton#PrimaryButton:pressed { background: #143b8a; color: #ffffff; }
    QPushButton:disabled, QPushButton#PrimaryButton:disabled,
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
        color: #526074; background: #e8edf3; border: 1px solid #a4afbe;
    }
    QCheckBox:disabled, QLabel:disabled { color: #526074; }
    QCheckBox::indicator:checked:disabled { background: #526074; border-color: #526074; }
    QComboBox QAbstractItemView, QMenu {
        background: #ffffff; color: #172033; border: 1px solid #8493a8;
        selection-background-color: #2458c5; selection-color: #ffffff;
    }
    QMenu::item { padding: 6px 20px; }
    QMenu::item:selected { background: #2458c5; color: #ffffff; }
    QMenu::item:disabled { color: #526074; }
    QToolTip { background: #172033; color: #ffffff; border: 1px solid #172033; padding: 5px; }
    """.replace("__CHECK_ICON__", (Path(__file__).parent / "assets" / "check.png").as_posix())


def apply_application_theme(app) -> None:
    """Set every palette role explicitly so Windows dark mode cannot wash out dialogs."""
    from PySide6.QtGui import QColor, QPalette
    colors = QPalette()
    roles = {
        QPalette.Window: "#ffffff", QPalette.WindowText: "#172033",
        QPalette.Base: "#ffffff", QPalette.AlternateBase: "#f4f7fb",
        QPalette.Text: "#172033", QPalette.Button: "#ffffff",
        QPalette.ButtonText: "#172033", QPalette.Highlight: "#2458c5",
        QPalette.HighlightedText: "#ffffff", QPalette.PlaceholderText: "#475569",
        QPalette.ToolTipBase: "#172033", QPalette.ToolTipText: "#ffffff",
        QPalette.Link: "#2458c5", QPalette.LinkVisited: "#6035a1",
    }
    for role, color in roles.items():
        colors.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.PlaceholderText):
        colors.setColor(QPalette.Disabled, role, QColor("#526074"))
    app.setPalette(colors)
    app.setStyleSheet(settings_qss())
