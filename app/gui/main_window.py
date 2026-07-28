"""BioFlow's quality-control desktop interface."""

from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from gui.pages.fastp_page import FastPPage
from gui.pages.fastqc_page import FastQCPage
from gui.pages.host_removal_page import HostRemovalPage
from gui.pages.history_page import HistoryPage
from gui.pages.multiqc_page import MultiQCPage
from gui.widgets.nucleotide_loom import NucleotideLoom
from gui.widgets.sidebar import Sidebar


VINTAGE_STYLESHEET = """
QWidget { background: #F5EFE3; color: #2F2924; font-family: Georgia, 'DejaVu Serif', serif; font-size: 14px; }
QWidget#topBar { background: #995C4A; border: 2px solid #734231; border-radius: 14px; }
QLabel#brand { background: transparent; color: #FFF9EC; font-size: 30px; font-weight: bold; letter-spacing: 5px; }
QLabel#brandSub { background: transparent; color: #F8E2C4; font-size: 11px; font-weight: bold; letter-spacing: 3px; }
QFrame#projectStrip { background: #EEE5D6; border: 1px solid #D8C6A8; border-radius: 9px; }
QLabel#projectTag { background: transparent; color: #765143; font-size: 11px; font-weight: bold; }
QLabel#projectValue { background: #FFF9EE; border: 1px solid #D5BD99; border-radius: 7px; color: #667452; font-size: 11px; padding: 5px 9px; }
QTreeWidget#sidebar { background: #83916B; border: 2px solid #64704F; border-radius: 14px; color: #FAF3E6; font-size: 15px; outline: 0; padding: 15px 9px; }
QTreeWidget#sidebar::item { background: transparent; border-radius: 7px; min-height: 31px; padding: 3px 7px; }
QTreeWidget#sidebar::item:hover { background: #A7B18D; color: #2F2924; }
QTreeWidget#sidebar::item:selected { background: #F1D7AF; color: #673D30; font-weight: bold; }
QTreeWidget#sidebar::branch { background: transparent; }
QWidget#toolPage { background: #FFF9EE; border: 2px solid #D8C6A8; border-radius: 14px; }
QLabel#eyebrow, QLabel#logTitle { color: #73805D; font-size: 11px; font-weight: bold; }
QLabel#pageTitle { color: #995C4A; font-size: 31px; font-weight: bold; }
QLabel#pageDescription { color: #62574D; font-size: 15px; }
QPushButton { background: #E9DDC9; border: 1px solid #C9B38E; border-radius: 8px; color: #43372F; font-weight: bold; padding: 9px 14px; }
QPushButton:hover { background: #D9C6A7; border-color: #995C4A; }
QPushButton:disabled { background: #E8E1D5; color: #9B9184; border-color: #D4CABC; }
QPushButton#runButton { background: #995C4A; border: 2px solid #734231; border-radius: 10px; color: #FFF9EE; font-size: 15px; padding: 12px 18px; }
QPushButton#runButton:hover { background: #B76D57; }
QFrame#resultCard { background: #EEF0E4; border: 1px solid #A9B18E; border-radius: 9px; }
QLabel#resultLabel { background: transparent; color: #596548; font-size: 11px; }
QPushButton#openResultsButton { background: #83916B; border: 1px solid #64704F; color: #FFF9EE; padding: 5px 10px; }
QPushButton#openResultsButton:hover { background: #98A57D; }
QLabel#threadCount { background: #83916B; border: 1px solid #64704F; border-radius: 11px; color: #FFF9EE; font-weight: bold; min-width: 27px; padding: 5px 4px; }
QSlider::groove:horizontal { background: #D8C6A8; border: 1px solid #C2A57B; border-radius: 5px; height: 8px; }
QSlider::sub-page:horizontal { background: #83916B; border-radius: 4px; }
QSlider::handle:horizontal { background: #995C4A; border: 2px solid #734231; border-radius: 10px; margin: -6px 0; width: 18px; }
QSlider::handle:horizontal:hover { background: #B76D57; }
QComboBox { background: #FFF9EE; border: 1px solid #C9B38E; border-radius: 7px; min-height: 27px; padding: 2px 8px; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #FFF9EE; border: 1px solid #C9B38E; selection-background-color: #E9DDC9; }
QScrollArea { border: 0; background: transparent; }
QTreeWidget#historyTable { background: #FFF9EE; border: 1px solid #D8C6A8; border-radius: 8px; alternate-background-color: #F5EFE3; }
QTreeWidget#historyTable::item { min-height: 30px; padding: 3px; }
QTreeWidget#historyTable::item:selected { background: #E9DDC9; color: #43372F; }
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
        root.addWidget(self._make_project_strip())

        content = QHBoxLayout()
        content.setSpacing(16)
        self.sidebar = Sidebar()
        self.sidebar.setMinimumWidth(210)
        self.sidebar.setMaximumWidth(230)
        self.sidebar.itemClicked.connect(self.show_page)
        content.addWidget(self.sidebar)

        self.pages = QStackedWidget()
        self.page_by_name = {
            "FastQC": FastQCPage(),
            "fastp": FastPPage(),
            "MultiQC": MultiQCPage(),
            "Host Removal": HostRemovalPage(),
            "Run History": HistoryPage(),
        }
        self.page_containers = {}
        for name, page in self.page_by_name.items():
            container = QScrollArea()
            container.setWidgetResizable(True)
            container.setWidget(page)
            self.page_containers[name] = container
            self.pages.addWidget(container)
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

    @staticmethod
    def _make_project_strip() -> QWidget:
        strip = QFrame()
        strip.setObjectName("projectStrip")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(14, 7, 14, 7)
        layout.setSpacing(9)
        tag = QLabel("Workflow")
        tag.setObjectName("projectTag")
        layout.addWidget(tag)
        for text in ("FASTQ → QC → Host removal", "Local analysis"):
            value = QLabel(text)
            value.setObjectName("projectValue")
            layout.addWidget(value)
        layout.addStretch()
        return strip

    def show_page(self, item, _column):
        container = self.page_containers.get(item.text(0))
        if container:
            self.pages.setCurrentWidget(container)
            page = self.page_by_name[item.text(0)]
            if isinstance(page, HistoryPage):
                page.refresh()
