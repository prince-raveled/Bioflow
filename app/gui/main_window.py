"""BioFlow's quality-control desktop interface."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from gui.pages.fastp_page import FastPPage
from gui.pages.fastqc_page import FastQCPage
from gui.pages.multiqc_page import MultiQCPage
from gui.widgets.nucleotide_loom import NucleotideLoom
from gui.widgets.sidebar import Sidebar


VINTAGE_STYLESHEET = """
QWidget { background: #F5EFE3; color: #2F2924; font-family: Georgia, 'DejaVu Serif', serif; font-size: 14px; }
QWidget#topBar { background: #995C4A; border: 2px solid #734231; border-radius: 14px; }
QLabel#brand { background: transparent; color: #FFF9EC; font-size: 30px; font-weight: bold; letter-spacing: 5px; }
QLabel#brandSub { background: transparent; color: #F8E2C4; font-size: 11px; font-weight: bold; letter-spacing: 4px; }
QTreeWidget#sidebar { background: #83916B; border: 2px solid #64704F; border-radius: 14px; color: #FAF3E6; font-size: 15px; outline: 0; padding: 15px 9px; }
QTreeWidget#sidebar::item { background: transparent; border-radius: 7px; min-height: 31px; padding: 3px 7px; }
QTreeWidget#sidebar::item:hover { background: #A7B18D; color: #2F2924; }
QTreeWidget#sidebar::item:selected { background: #F1D7AF; color: #673D30; font-weight: bold; }
QTreeWidget#sidebar::branch { background: transparent; }
QWidget#toolPage { background: #FFF9EE; border: 2px solid #D8C6A8; border-radius: 14px; }
QLabel#eyebrow, QLabel#logTitle { color: #83916B; font-size: 10px; font-weight: bold; letter-spacing: 2px; }
QLabel#pageTitle { color: #995C4A; font-size: 31px; font-weight: bold; }
QLabel#pageDescription { color: #62574D; font-size: 15px; }
QLabel#ornament { color: #C99865; font-size: 15px; letter-spacing: 8px; }
QPushButton { background: #E9DDC9; border: 1px solid #C9B38E; border-radius: 8px; color: #43372F; font-weight: bold; padding: 9px 14px; }
QPushButton:hover { background: #D9C6A7; border-color: #995C4A; }
QPushButton:disabled { background: #E8E1D5; color: #9B9184; border-color: #D4CABC; }
QPushButton#runButton { background: #995C4A; border: 2px solid #734231; border-radius: 10px; color: #FFF9EE; font-size: 15px; padding: 12px 18px; }
QPushButton#runButton:hover { background: #B76D57; }
QLabel#threadCount { background: #83916B; border: 1px solid #64704F; border-radius: 11px; color: #FFF9EE; font-weight: bold; min-width: 27px; padding: 5px 4px; }
QSlider::groove:horizontal { background: #D8C6A8; border: 1px solid #C2A57B; border-radius: 5px; height: 8px; }
QSlider::sub-page:horizontal { background: #83916B; border-radius: 4px; }
QSlider::handle:horizontal { background: #995C4A; border: 2px solid #734231; border-radius: 10px; margin: -6px 0; width: 18px; }
QSlider::handle:horizontal:hover { background: #B76D57; }
QTextEdit#executionLog { background: #2F2924; border: 3px solid #83916B; border-radius: 9px; color: #F7EAD3; font-family: 'DejaVu Sans Mono', monospace; font-size: 12px; padding: 8px; }
QScrollBar:vertical { background: #E9DDC9; width: 11px; margin: 3px; }
QScrollBar::handle:vertical { background: #995C4A; border-radius: 5px; min-height: 24px; }
"""


class MainWindow(QWidget):
    """Show quality-control tools in a warm, vintage-inspired workbench."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BioFlow — Metagenomics")
        self.resize(1060, 690)
        self.setStyleSheet(VINTAGE_STYLESHEET)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(16)
        root.addWidget(self._make_header())

        content = QHBoxLayout()
        content.setSpacing(16)
        self.sidebar = Sidebar()
        self.sidebar.setMinimumWidth(210)
        self.sidebar.setMaximumWidth(230)
        self.sidebar.itemClicked.connect(self.show_page)
        content.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.page_by_name = {"FastQC": FastQCPage(), "fastp": FastPPage(), "MultiQC": MultiQCPage()}
        for page in self.page_by_name.values():
            self.pages.addWidget(page)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)

    @staticmethod
    def _make_header() -> QWidget:
        header = QFrame()
        header.setObjectName("topBar")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 15, 24, 15)
        text = QVBoxLayout()
        brand = QLabel("BIOFLOW")
        brand.setObjectName("brand")
        text.addWidget(brand)
        sub_brand = QLabel("METAGENOMICS")
        sub_brand.setObjectName("brandSub")
        text.addWidget(sub_brand)
        layout.addLayout(text)
        layout.addStretch()
        layout.addWidget(NucleotideLoom())
        return header

    def show_page(self, item, _column):
        page = self.page_by_name.get(item.text(0))
        if page:
            self.pages.setCurrentWidget(page)
