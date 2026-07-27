from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem


class Sidebar(QTreeWidget):

    def __init__(self):
        super().__init__()

        self.setObjectName("sidebar")
        self.setHeaderHidden(True)
        self.setIndentation(18)

        self.build_tree()

    def build_tree(self):

        quality = QTreeWidgetItem(["QUALITY CONTROL"])

        fastqc = QTreeWidgetItem(["FastQC"])
        fastp = QTreeWidgetItem(["fastp"])
        multiqc = QTreeWidgetItem(["MultiQC"])

        quality.addChild(fastqc)
        quality.addChild(fastp)
        quality.addChild(multiqc)

        self.addTopLevelItem(quality)

        host_removal = QTreeWidgetItem(["HOST REMOVAL"])
        host_removal.addChild(QTreeWidgetItem(["Host Removal"]))
        self.addTopLevelItem(host_removal)

        for section in (quality, host_removal):
            section.setFlags(section.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            section_font = QFont(section.font(0))
            section_font.setBold(True)
            section.setFont(0, section_font)
            section.setBackground(0, QBrush(QColor("#995C4A")))
            section.setForeground(0, QBrush(QColor("#FFF9EE")))

        quality.setExpanded(True)
        host_removal.setExpanded(True)
        self.setCurrentItem(fastqc)
