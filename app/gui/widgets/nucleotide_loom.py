"""An animated, abstract nucleotide ribbon used as a subtle interface accent."""

from math import sin

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from gui import theme


class NucleotideLoom(QWidget):
    """Draw ATGC pairs travelling through a soft, non-traditional DNA ribbon."""

    BASE_PAIRS = (("A", "T"), ("T", "A"), ("G", "C"), ("C", "G"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.phase = 0.0
        self.setMinimumSize(150, 44)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.timer.start(45)

    def _advance(self):
        self.phase = (self.phase + 0.14) % 1000
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        centre = height / 2

        backbone_pen = QPen(QColor(theme.SAGE_DEEP), 2)
        backbone_pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(backbone_pen)
        for x in range(0, width, 5):
            y_top = centre - 11 * sin((x + self.phase * 8) / 27)
            y_bottom = centre + 11 * sin((x + self.phase * 8) / 27)
            painter.drawPoint(x, int(y_top))
            painter.drawPoint(x, int(y_bottom))

        painter.setFont(QFont("Georgia", 8, QFont.Weight.Bold))
        for index, x in enumerate(range(-14, width + 20, 30)):
            wave = sin((x + self.phase * 8) / 27)
            top_y = int(centre - 11 * wave)
            bottom_y = int(centre + 11 * wave)
            pair = self.BASE_PAIRS[(index + int(self.phase / 4)) % len(self.BASE_PAIRS)]
            painter.setPen(QPen(QColor('#C8D2B4'), 1))
            painter.drawLine(x + 7, top_y + 4, x + 7, bottom_y - 4)
            painter.setPen(QColor(theme.CREAM_TEXT))
            painter.drawText(x, top_y + 5, pair[0])
            painter.setPen(QColor(theme.ACCENT_SOFT))
            painter.drawText(x, bottom_y + 5, pair[1])
