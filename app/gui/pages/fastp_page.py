from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QLabel, QPushButton, QSlider

from gui.pages.qc_tool_page import QCToolPage
from gui.pages.fastqc_page import FASTQ_FILTER


class FastPPage(QCToolPage):
    """Run fastp for one single-end sample or one paired-end sample."""

    def __init__(self):
        super().__init__("fastp")
        self.fastq_files: list[str] = []
        self.description.setText("Trim one single-end file or exactly two paired-end files.")

        self.file_label = QLabel("No FASTQ file(s) selected")
        self.file_label.setWordWrap(True)
        self.controls.addWidget(self.file_label)

        row = QHBoxLayout()
        self.browse_button = QPushButton("Browse one or two FASTQ files")
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
        files, _ = QFileDialog.getOpenFileNames(self, "Select one or two FASTQ files", "", FASTQ_FILTER)
        if files:
            self.fastq_files = files
            self.set_default_output_directory(
                Path(files[0]).resolve().parent / "bioflow_results" / "fastp"
            )
            self.file_label.setText("\n".join(files))
            self.add_log(f"Selected {len(files)} FASTQ file(s).")

    @staticmethod
    def _trimmed_name(file_name: str) -> str:
        path = Path(file_name)
        name = path.name
        for extension in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
            if name.endswith(extension):
                return f"{name[:-len(extension)]}.trimmed{extension}"
        return f"{path.stem}.trimmed.fastq.gz"

    def run_analysis(self):
        if len(self.fastq_files) not in (1, 2):
            self.add_log("fastp requires one single-end file or exactly two paired-end files.")
            return
        assert self.output_directory is not None
        self.output_directory.mkdir(parents=True, exist_ok=True)

        output_1 = self.output_directory / self._trimmed_name(self.fastq_files[0])
        report_prefix = self.output_directory / "fastp_report"
        command = [
            "fastp", "--thread", str(self.threads.value()), "-i", self.fastq_files[0], "-o", str(output_1),
            "--html", f"{report_prefix}.html", "--json", f"{report_prefix}.json",
        ]
        if len(self.fastq_files) == 2:
            output_2 = self.output_directory / self._trimmed_name(self.fastq_files[1])
            command.extend(["-I", self.fastq_files[1], "-O", str(output_2)])
        self.start_tool(command)
