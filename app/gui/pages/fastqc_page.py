from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QSlider

from gui.pages.qc_tool_page import QCToolPage


FASTQ_FILTER = "FASTQ files (*.fastq *.fastq.gz *.fq *.fq.gz)"


class FastQCPage(QCToolPage):
    """Run FastQC on one or more FASTQ files."""

    def __init__(self):
        super().__init__("FastQC")
        self.fastq_files: list[str] = []
        self.description.setText("Run read-quality checks for one or more FASTQ files.")

        self.file_label = QLabel("No FASTQ files selected")
        self.file_label.setWordWrap(True)
        self.controls.addWidget(self.file_label)

        row = QHBoxLayout()
        self.browse_button = QPushButton("Browse FASTQ files")
        self.browse_button.clicked.connect(self.select_files)
        row.addWidget(self.browse_button)
        row.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(4)
        self.threads.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threads.setTickInterval(4)
        self.threads.valueChanged.connect(self._show_thread_count)
        row.addWidget(self.threads)
        self.thread_count = QLabel("04")
        self.thread_count.setObjectName("threadCount")
        row.addWidget(self.thread_count)
        self.controls.addLayout(row)
        self.add_output_selector()

    def _show_thread_count(self, value: int):
        self.thread_count.setText(f"{value:02d}")

    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select FASTQ files", "", FASTQ_FILTER)
        if files:
            self.fastq_files = files
            self.set_default_output_directory(
                Path(files[0]).resolve().parent / "bioflow_results" / "fastqc"
            )
            self.file_label.setText("\n".join(files))
            self.add_log(f"Selected {len(files)} FASTQ file(s).")

    def run_analysis(self):
        if not self.fastq_files:
            self.add_log("Select at least one FASTQ file before running FastQC.")
            return
        assert self.output_directory is not None
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.start_tool(["fastqc", "--threads", str(self.threads.value()), "--outdir", str(self.output_directory), *self.fastq_files])
