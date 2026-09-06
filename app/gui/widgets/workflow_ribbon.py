"""The workflow, drawn as a track rather than a row of buttons.

Painted rather than assembled from labels inside a styled frame. A Qt stylesheet
set on a parent with no type selector reaches its children too, so a border and
a radius meant for the container were being applied to every label inside it -
each step arrived wrapped in its own box, inside another box, all in the same
terracotta. Painting removes both the boxes and the way they got there.

The shape borrows from a genome browser: a hairline track with a node at each
step, which reads as a sequence in a way a row of separated words does not.
"""

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui import theme


class WorkflowRibbon(QWidget):
    """A compact, non-interactive summary of the stages this build runs."""

    #: Room either side of a label, and between the track and the text above it.
    LABEL_PADDING = 16
    TRACK_GAP = 10
    NODE_RADIUS = 2.6

    def __init__(self, steps: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.steps = list(steps)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._font = QFont()
        self._font.setPointSize(9)
        self._font.setBold(True)
        self._font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.3)
        self._metrics = QFontMetrics(self._font)
        self.setFixedHeight(38)
        self.setMinimumWidth(self._natural_width())
        self.setToolTip("The workflow this build of BioFlow runs, in order.")

    # ------------------------------------------------------------------
    def set_steps(self, steps: list[str]) -> None:
        self.steps = list(steps)
        self.setMinimumWidth(self._natural_width())
        self.updateGeometry()
        self.update()

    def _natural_width(self) -> int:
        if not self.steps:
            return 0
        return sum(
            self._metrics.horizontalAdvance(step) + self.LABEL_PADDING * 2
            for step in self.steps
        )

    def _centres(self) -> list[float]:
        """The horizontal centre of each step, spread across the full width."""
        widths = [
            self._metrics.horizontalAdvance(step) + self.LABEL_PADDING * 2
            for step in self.steps
        ]
        total = sum(widths) or 1
        # Distribute any extra width proportionally, so the track fills the
        # space it is given without the labels drifting apart unevenly.
        scale = max(1.0, self.width() / total)
        centres, cursor = [], 0.0
        for width in widths:
            span = width * scale
            centres.append(cursor + span / 2)
            cursor += span
        return centres

    # ------------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        if not self.steps:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self._font)

        centres = self._centres()
        text_baseline = self._metrics.ascent() + 4
        track_y = text_baseline + self.TRACK_GAP

        cream = QColor(theme.CREAM_TEXT)

        # The track: drawn only between the first and last node, so it reads as
        # spanning the workflow rather than running off both edges.
        rail = QColor(cream)
        rail.setAlpha(70)
        painter.setPen(QPen(rail, 1))
        painter.drawLine(int(centres[0]), int(track_y), int(centres[-1]), int(track_y))

        for index, (step, centre) in enumerate(zip(self.steps, centres)):
            painter.setPen(QPen(cream))
            width = self._metrics.horizontalAdvance(step)
            painter.drawText(QRectF(centre - width, 0, width * 2, text_baseline + 2),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                             step)

            # The last node is filled and slightly larger: it is where the run
            # ends, and it gives the track a direction without an arrowhead.
            final = index == len(self.steps) - 1
            radius = self.NODE_RADIUS + (0.9 if final else 0.0)
            node = QColor(theme.ACCENT_SOFT) if final else QColor(cream)
            if not final:
                node.setAlpha(190)
            painter.setBrush(node)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(centre - radius, track_y - radius,
                                       radius * 2, radius * 2))
