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

        quality.setExpanded(True)
