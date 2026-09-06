from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPalette
from PyQt6.QtWidgets import QFrame, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from gui import theme
from gui.navigation import NAVIGATION
from gui.widgets.author_signature import AuthorSignature
from gui.widgets.nucleotide_loom import NucleotideLoom


class Sidebar(QTreeWidget):

    def __init__(self):
        super().__init__()

        self.setObjectName("sidebar")
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setAnimated(True)
        self.setUniformRowHeights(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRootIsDecorated(False)

        # The branch column falls back to the default Highlight colour, which
        # the stylesheet cannot reach; clear it so selection reads as one shape.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(theme.ACCENT))
        self.setPalette(palette)

        self.build_tree()

    def build_tree(self):
        """Build the tree from the shared navigation structure.

        The names are not repeated here: the window looks its pages up by the
        text of the item that was clicked, so a name spelled differently in the
        two places produces an entry that silently does nothing.
        """
        sections = []
        for heading, pages in NAVIGATION:
            section = QTreeWidgetItem([heading])
            for name in pages:
                section.addChild(QTreeWidgetItem([name]))
            self.addTopLevelItem(section)
            sections.append(section)

        for section in sections:
            section.setFlags(section.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            section_font = QFont(section.font(0))
            section_font.setBold(True)
            section_font.setPointSize(max(7, section_font.pointSize() - 2))
            section.setFont(0, section_font)
            # BioFlow's original treatment: a terracotta band with cream text,
            # which separates sections clearly and clears AA (5.03:1).
            section.setBackground(0, QBrush(QColor(theme.ACCENT)))
            section.setForeground(0, QBrush(QColor(theme.CREAM_TEXT)))

        for section in sections:
            section.setExpanded(True)

    def select(self, page_name: str) -> bool:
        """Highlight a page by name so the window can open on it at startup."""
        matches = self.findItems(
            page_name, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive
        )
        if matches:
            self.setCurrentItem(matches[0])
            return True
        return False


class SidebarPanel(QWidget):
    """The sidebar in its card, with a quiet animated accent at the foot."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        frame = QFrame(self)
        frame.setObjectName("sidebarPanel")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        layout = QVBoxLayout(frame)
        layout.setContentsMargins(6, 10, 6, 10)
        layout.setSpacing(6)

        self.tree = Sidebar()
        layout.addWidget(self.tree, 1)

        self.accent = NucleotideLoom()
        self.accent.setFixedHeight(44)
        layout.addWidget(self.accent)

        # The ribbon above is abstract; this is the same alphabet carrying
        # something real. It stays collapsed to a single line until pressed.
        self.signature = AuthorSignature()
        layout.addWidget(self.signature)
