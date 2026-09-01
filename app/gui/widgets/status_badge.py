"""A small coloured pill that states one thing clearly: what state something is in."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from gui import theme


class StatusBadge(QLabel):
    """Uniform status pill used by resources, stages, and run history."""

    def __init__(self, text: str = "", kind: str = "idle", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("statusBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state(text, kind)

    def set_state(self, text: str, kind: str) -> None:
        """Set the label and its colour pair in one call."""
        foreground, background = theme.state_colours(kind)
        self.setText(text)
        self.setStyleSheet(
            f"background: {background}; color: {foreground};"
            f" border-radius: 9px; font-size: 11px; font-weight: bold;"
            f" letter-spacing: 0.4px; padding: 5px 10px;"
        )
