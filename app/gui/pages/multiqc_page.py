from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QLabel, QPushButton

from gui.pages.qc_tool_page import QCToolPage


REPORT_FILTER = "QC report files (*.html *.htm *.zip *.json *.txt);;All files (*)"


class MultiQCPage(QCToolPage):
    """Aggregate individually selected QC report files."""

    def __init__(self):
        super().__init__("MultiQC")
        self.input_paths: list[str] = []
        self.description.setText("Select FastQC, fastp, or other QC report files to aggregate.")

        self.input_label = QLabel("No report files selected")
        self.input_label.setWordWrap(True)
        self.controls.addWidget(self.input_label)

        self.files_button = QPushButton("Browse HTML/report files")
        self.files_button.clicked.connect(self.select_report_files)
        self.controls.addWidget(self.files_button)
        self.add_output_selector()

    def select_report_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select QC HTML or report files", "", REPORT_FILTER
        )
        if files:
            self.input_paths = files
            selected_parent = Path(files[0]).resolve().parent
            self.set_default_output_directory(selected_parent / "multiqc")
            self.input_label.setText("Selected report files:\n" + "\n".join(files))
            self.add_log(f"Selected {len(files)} report file(s) for MultiQC.")

    def run_analysis(self):
        if not self.input_paths:
            self.add_log("Select one or more report files before running MultiQC.")
            return
        assert self.output_directory is not None
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.start_tool(["multiqc", *self.input_paths, "--outdir", str(self.output_directory), "--force"])
