from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QFileDialog,
    QTextEdit,
    QVBoxLayout,
)

import sys


class MainWindow(QWidget):

    def __init__(self):
        super().__init__()

        # Store all selected FASTQ files
        self.fastq_files = []

        self.setWindowTitle("BioFlow v0.1")
        self.setGeometry(300, 200, 650, 400)

        self.build_ui()

    def build_ui(self):

        layout = QVBoxLayout()

        title = QLabel("BioFlow - FastQC Test")
        title.setStyleSheet("font-size:20px;font-weight:bold;")
        layout.addWidget(title)

        self.file_label = QLabel("No FASTQ files selected")
        layout.addWidget(self.file_label)

        browse_btn = QPushButton("Add FASTQ Files")
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

        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Select FASTQ Files",
            "",
            "FASTQ Files (*.fastq *.fastq.gz *.fq *.fq.gz)"
        )

        if not filenames:
            return

        # Add new files
        self.fastq_files.extend(filenames)

        # Remove duplicates while preserving order
        self.fastq_files = list(dict.fromkeys(self.fastq_files))

        # Update label
        self.file_label.setText(
            f"{len(self.fastq_files)} FASTQ files selected"
        )

        # Log newly added files
        self.log.append(f"\nAdded {len(filenames)} file(s):")

        for file in filenames:
            self.log.append(f"• {file}")

    def run_fastqc(self):

        if not self.fastq_files:
            self.log.append("❌ No FASTQ files selected.\n")
            return

        self.log.append("\n========== Starting FastQC ==========")

        for file in self.fastq_files:

            self.log.append(f"Running FastQC on:")
            self.log.append(f"   {file}")
            self.log.append("Status : Pending Backend\n")

            #
            # Later:
            #
            # FastQC(file).run()
            #

        self.log.append("========== FastQC Finished ==========\n")


if __name__ == "__main__":

    app = QApplication(sys.argv)

    window = MainWindow()

    window.show()

    sys.exit(app.exec())