from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QFileDialog,
    QTextEdit,
    QVBoxLayout,
    QHBoxLayout,
)

import sys


class MainWindow(QWidget):

    def __init__(self):
        super().__init__()

        self.fastq_file = ""

        self.setWindowTitle("BioFlow v0.1")
        self.setGeometry(300, 200, 600, 350)

        self.build_ui()

    def build_ui(self):

        layout = QVBoxLayout()

        title = QLabel("BioFlow - FastQC Test")
        title.setStyleSheet("font-size:20px;font-weight:bold;")
        layout.addWidget(title)

        self.file_label = QLabel("No FASTQ selected")
        layout.addWidget(self.file_label)

        browse_btn = QPushButton("Browse FASTQ")

        browse_btn.clicked.connect(self.select_fastq)

        layout.addWidget(browse_btn)

        run_btn = QPushButton("Run FastQC")

        run_btn.clicked.connect(self.run_fastqc)

        layout.addWidget(run_btn)

        self.log = QTextEdit()

        self.log.setReadOnly(True)

        layout.addWidget(self.log)

        self.setLayout(layout)

    def select_fastq(self):

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Select FASTQ",
            "",
            "FASTQ Files (*.fastq *.fastq.gz)"
        )

        if filename:

            self.fastq_file = filename

            self.file_label.setText(filename)

            self.log.append("FASTQ Selected")

            self.log.append(filename)

    def run_fastqc(self):

        if self.fastq_file == "":

            self.log.append("No FASTQ selected")

            return

        self.log.append("Starting FastQC...")

        #
        # We will connect Runner here
        #

        self.log.append("FastQC Finished")


if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(app.exec())