"""A visual chain of the pipeline's stages and their live state.

This is a view over the existing stage model and StageStatus. It holds no
workflow logic of its own and never decides what runs.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from backend.execution.record import StageStatus
from gui import theme


#: How each execution state is presented. Keys are StageStatus values.
STATE_PRESENTATION = {
    StageStatus.PENDING: ("Pending", "idle", "○"),
    StageStatus.RUNNING: ("Running", "running", "◐"),
    StageStatus.COMPLETED: ("Done", "ok", "●"),
    StageStatus.SKIPPED: ("Reused", "info", "◍"),
    StageStatus.FAILED: ("Failed", "error", "✕"),
    StageStatus.BLOCKED: ("Blocked", "warn", "⊘"),
}

#: Frames for the running indicator, cycled by a single shared timer.
SPINNER_FRAMES = ("◐", "◓", "◑", "◒")


class StageChip(QFrame):
    """One stage: its short name, and a dot showing where it stands."""

    def __init__(self, key: str, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.key = key
        self.setObjectName("card")
        self.setMinimumWidth(84)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.indicator = QLabel("○")
        self.indicator.setStyleSheet(f"background: transparent; color: {theme.IDLE}; font-size: 13px;")
        top.addWidget(self.indicator)
        self.name = QLabel(title)
        self.name.setWordWrap(False)
        self.name.setStyleSheet(
            f"background: transparent; color: {theme.TEXT}; font-size: 11px; font-weight: 600;"
        )
        top.addWidget(self.name, 1)
        layout.addLayout(top)

        self.state_label = QLabel("Pending")
        self.state_label.setStyleSheet(
            f"background: transparent; color: {theme.TEXT_FAINT}; font-size: 10px;"
        )
        layout.addWidget(self.state_label)

        self.status = StageStatus.PENDING
        self.set_status(StageStatus.PENDING)

    def set_status(self, status: StageStatus, detail: str = "") -> None:
        label, kind, glyph = STATE_PRESENTATION.get(
            status, ("Pending", "idle", "○")
        )
        colour, soft = theme.state_colours(kind)
        self.status = status
        self.indicator.setText(glyph)
        self.indicator.setStyleSheet(
            f"background: transparent; color: {colour}; font-size: 13px;"
        )
        self.state_label.setText(detail or label)
        self.state_label.setStyleSheet(
            f"background: transparent; color: {colour}; font-size: 10px; font-weight: 600;"
        )
        self.setStyleSheet(
            f"QFrame#card {{ background: {soft if status is not StageStatus.PENDING else theme.SURFACE_RAISED};"
            f" border: 1px solid {colour if status is not StageStatus.PENDING else theme.BORDER};"
            f" border-radius: 10px; }}"
        )

    def advance_spinner(self, frame: int) -> None:
        if self.status is StageStatus.RUNNING:
            self.indicator.setText(SPINNER_FRAMES[frame % len(SPINNER_FRAMES)])


class StageFlow(QWidget):
    """The whole chain, from reads through to the aggregated report."""

    def __init__(self, stages, parent: QWidget | None = None):
        super().__init__(parent)
        self.chips: dict[str, StageChip] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        source = QLabel("FASTQ")
        source.setStyleSheet(
            f"background: {theme.SURFACE_RAISED}; border: 1px dashed {theme.BORDER_STRONG};"
            f" border-radius: 8px; color: {theme.TEXT_MUTED}; font-size: 10px;"
            f" font-weight: 700; letter-spacing: 1px; padding: 12px 8px;"
        )
        source.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(source)

        for index, stage in enumerate(stages):
            if index or True:
                arrow = QLabel("→")
                arrow.setStyleSheet(
                    f"background: transparent; color: {theme.TEXT_FAINT}; font-size: 13px;"
                )
                layout.addWidget(arrow)
            chip = StageChip(stage.key, self._short_title(stage))
            chip.setToolTip(f"{stage.title} — environment: {stage.environment_key}")
            self.chips[stage.key] = chip
            layout.addWidget(chip, 1)

        # One timer for the whole row, started only while something is running.
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(320)
        self._timer.timeout.connect(self._tick)

    @staticmethod
    def _short_title(stage) -> str:
        """The chip label, taken from the stage rather than a table kept here.

        Chip labels are short by design; the full title lives in the tooltip.
        """
        return stage.chip_title or stage.title.split(" (")[0]

    # ------------------------------------------------------------------
    def set_stage_status(self, key: str, status: StageStatus, detail: str = "") -> None:
        chip = self.chips.get(key)
        if chip is None:
            return
        chip.set_status(status, detail)
        self._sync_timer()

    def reset(self, keys=None) -> None:
        """Return every chip (or a chosen subset) to pending."""
        for key, chip in self.chips.items():
            if keys is None or key in keys:
                chip.set_status(StageStatus.PENDING)
        self._sync_timer()

    def set_enabled_stages(self, keys) -> None:
        """Dim the chips for stages the user has not selected."""
        for key, chip in self.chips.items():
            chip.setVisible(True)
            chip.setEnabled(key in keys)
            chip.setStyleSheet(chip.styleSheet())

    def _sync_timer(self) -> None:
        running = any(c.status is StageStatus.RUNNING for c in self.chips.values())
        if running and not self._timer.isActive():
            self._timer.start()
        elif not running and self._timer.isActive():
            self._timer.stop()

    def _tick(self) -> None:
        self._frame += 1
        for chip in self.chips.values():
            chip.advance_spinner(self._frame)

    def stop(self) -> None:
        self._timer.stop()
