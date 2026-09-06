from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
)

from backend.samples import FASTQ_EXTENSIONS, FASTQ_FILE_FILTER, ReadLayout
from backend.execution.stages.trimming import FastpStage
from gui.pages.qc_tool_page import QCToolPage, tool_context
from gui import dialogs


#: The two modes fastp supports, paired with the layout the backend models.
LAYOUT_OPTIONS = tuple(
    (layout.label, layout) for layout in (ReadLayout.SINGLE, ReadLayout.PAIRED)
)


class FastPPage(QCToolPage):
    """Run fastp for one single-end sample or one paired-end sample."""

    def __init__(self):
        super().__init__("fastp")
        self.fastq_files: list[str] = []
        self.description.setText("Trim one single-end file or exactly two paired-end files.")

        self.file_label = QLabel("No FASTQ file(s) selected")
        self.file_label.setWordWrap(True)
        self.controls.addWidget(self.file_label)

        # fastp genuinely has two modes, so the layout is stated rather than
        # merely inferred. Selecting files still sets this automatically; the
        # control lets the user correct that choice before running.
        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Read layout"))
        self.layout_choice = QComboBox()
        for option in LAYOUT_OPTIONS:
            self.layout_choice.addItem(option[0])
        self.layout_choice.currentIndexChanged.connect(self._show_layout_expectation)
        layout_row.addWidget(self.layout_choice)
        self.layout_hint = QLabel("")
        self.layout_hint.setObjectName("componentDescription")
        layout_row.addWidget(self.layout_hint, 1)
        self.controls.addLayout(layout_row)

        row = QHBoxLayout()
        self.browse_button = QPushButton("Browse one or two FASTQ files")
        self.browse_button.clicked.connect(self.select_files)
        row.addWidget(self.browse_button)
        row.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(self.default_thread_count())
        self.threads.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threads.setTickInterval(4)
        self.threads.valueChanged.connect(self._show_thread_count)
        row.addWidget(self.threads)
        self.thread_count = QLabel(self.thread_label(self.threads.value()))
        self.thread_count.setObjectName("threadCount")
        row.addWidget(self.thread_count)
        self.controls.addLayout(row)
        self.add_output_selector()
        self._show_layout_expectation()

    @property
    def read_layout(self) -> ReadLayout:
        """The layout the user has selected."""
        return LAYOUT_OPTIONS[self.layout_choice.currentIndex()][1]

    def _show_layout_expectation(self):
        expected = 2 if self.read_layout is ReadLayout.PAIRED else 1
        self.layout_hint.setText(
            f"expects {expected} FASTQ file{'s' if expected == 2 else ''}"
        )

    def _show_thread_count(self, value: int):
        self.thread_count.setText(f"{value:02d}")

    def select_files(self):
        files = dialogs.open_files(self, "Select one or two FASTQ files", FASTQ_FILE_FILTER)
        if files:
            self.fastq_files = files
            self.set_default_output_directory(
                Path(files[0]).resolve().parent / "bioflow_results" / "fastp"
            )
            self.file_label.setText("\n".join(files))
            detected = ReadLayout.PAIRED if len(files) == 2 else ReadLayout.SINGLE
            self.layout_choice.setCurrentIndex(
                next(i for i, (_, layout) in enumerate(LAYOUT_OPTIONS) if layout is detected)
            )
            self.set_input_summary(
                f"1 {detected.label.lower()} sample staged for fastp"
            )
            self.add_log(f"Selected {len(files)} FASTQ file(s).")

    @staticmethod
    def _trimmed_name(file_name: str) -> str:
        path = Path(file_name)
        name = path.name
        for extension in FASTQ_EXTENSIONS:
            if name.endswith(extension):
                return f"{name[:-len(extension)]}.trimmed{extension}"
        # Nothing recognisable to strip, so add rather than cut: a stem removes
        # only the last suffix, which turns "sample.fastq.gz" into
        # "sample.fastq.trimmed.fastq.gz" and buries the extension mid-name.
        return f"{name}.trimmed.fastq.gz"

    def run_analysis(self):
        expected = 2 if self.read_layout is ReadLayout.PAIRED else 1
        if len(self.fastq_files) != expected:
            self.add_log(
                f"{self.read_layout.label} trimming needs exactly {expected} FASTQ "
                f"file{'s' if expected == 2 else ''}; {len(self.fastq_files)} selected."
            )
            return
        assert self.output_directory is not None
        self.output_directory.mkdir(parents=True, exist_ok=True)

        paired = len(self.fastq_files) == 2
        trimmed = [
            self.output_directory / self._trimmed_name(name) for name in self.fastq_files
        ]
        report_prefix = self.output_directory / "fastp_report"
        command = FastpStage().trim_command(
            reads=[Path(name) for name in self.fastq_files],
            trimmed=trimmed,
            html=Path(f"{report_prefix}.html"),
            report_json=Path(f"{report_prefix}.json"),
            context=tool_context(self.output_directory, self.threads.value()),
            paired=paired,
        )
        self.start_tool(list(command.command))
