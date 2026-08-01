from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication


APPLICATION_STYLESHEET = """
QWidget {
    background-color: #080A0E;
    color: #F4F6FA;
    font-family: "DejaVu Sans";
    font-size: 18px;
}
QLabel#statusLabel { color: #AAB2C0; font-size: 18px; }
QLabel#verificationLabel { color: #D6DDEA; font-size: 16px; padding: 4px; }
QLabel#messageLabel { color: #F4F6FA; font-size: 28px; font-weight: 600; }
QToolTip { background: #171B24; color: #F4F6FA; border: 1px solid #4D596D; }
"""


def apply_forced_theme(app: QApplication) -> None:
    style = app.setStyle("Fusion")
    if style is None:
        raise RuntimeError("Qt Fusion style is unavailable")
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#080A0E",
        QPalette.ColorRole.WindowText: "#F4F6FA",
        QPalette.ColorRole.Base: "#0E1118",
        QPalette.ColorRole.AlternateBase: "#171B24",
        QPalette.ColorRole.ToolTipBase: "#171B24",
        QPalette.ColorRole.ToolTipText: "#F4F6FA",
        QPalette.ColorRole.Text: "#F4F6FA",
        QPalette.ColorRole.Button: "#171B24",
        QPalette.ColorRole.ButtonText: "#F4F6FA",
        QPalette.ColorRole.BrightText: "#FFFFFF",
        QPalette.ColorRole.Highlight: "#3977D7",
        QPalette.ColorRole.HighlightedText: "#FFFFFF",
        QPalette.ColorRole.PlaceholderText: "#778195",
    }
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, color in roles.items():
            palette.setColor(group, role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#6E7685"))
    app.setPalette(palette)
    app.setFont(QFont("DejaVu Sans", 12))
    app.setStyleSheet(APPLICATION_STYLESHEET)
