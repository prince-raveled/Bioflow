"""Host-read removal with Bowtie2 and a configured human reference index."""

from pathlib import Path
import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from gui.pages.fastqc_page import FASTQ_FILTER
from gui.pages.qc_tool_page import QCToolPage


class HostRemovalPage(QCToolPage):
    """Run Bowtie2 against GRCh38 and retain unmapped microbial reads."""

    def __init__(self):
        # This matches the installed BioFlow host-removal environment. Future
        # setup/configuration work will make the name user-configurable.
        super().__init__("Host Removal", environment_name="bioflow-hostrem")
        self.paired_fastq_files: list[str] = []
        self.single_fastq_files: list[str] = []
        self.index_prefix: Path | None = None
        self._pending_jobs: list[tuple[str, list[str], Path]] = []
        self._completed_jobs = 0
        self._failed_jobs = 0
        self.description.setText(
            "Remove human reads with Bowtie2. Select read layout, GRCh38 index, and an output folder."
        )

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Read layout"))
        self.layout_choice = QComboBox()
        self.layout_choice.addItems(["Paired-end", "Single-end"])
        self.layout_choice.currentIndexChanged.connect(self._change_layout)
        mode_row.addWidget(self.layout_choice)
        mode_row.addStretch()
        self.controls.addLayout(mode_row)

        self.paired_inputs = QWidget()
        paired_layout = QVBoxLayout(self.paired_inputs)
        paired_layout.setContentsMargins(0, 0, 0, 0)
        self.paired_label = QLabel("No paired-end FASTQ files selected")
        self.paired_label.setWordWrap(True)
        paired_layout.addWidget(self.paired_label)
        paired_button = QPushButton("Browse paired-end FASTQ files")
        paired_button.clicked.connect(self._select_paired_files)
        paired_layout.addWidget(paired_button)
        self.controls.addWidget(self.paired_inputs)

        self.single_inputs = QWidget()
        single_layout = QVBoxLayout(self.single_inputs)
        single_layout.setContentsMargins(0, 0, 0, 0)
        self.single_label = QLabel("No single-end FASTQ files selected")
        self.single_label.setWordWrap(True)
        single_layout.addWidget(self.single_label)
        single_button = QPushButton("Browse single-end FASTQ files")
        single_button.clicked.connect(self._select_single_files)
        single_layout.addWidget(single_button)
        self.controls.addWidget(self.single_inputs)
        self.single_inputs.hide()

        self.index_label = QLabel("GRCh38 Bowtie2 index files: not selected")
        self.index_label.setWordWrap(True)
        self.controls.addWidget(self.index_label)
        index_button = QPushButton("Select all 6 GRCh38 index files")
        index_button.clicked.connect(self._select_index_files)
        self.controls.addWidget(index_button)
        self._load_configured_index()

        thread_row = QHBoxLayout()
        thread_row.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(8)
        self.threads.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threads.setTickInterval(4)
        self.threads.valueChanged.connect(self._show_thread_count)
        thread_row.addWidget(self.threads)
        self.thread_count = QLabel("08")
        self.thread_count.setObjectName("threadCount")
        thread_row.addWidget(self.thread_count)
        self.controls.addLayout(thread_row)
        self.add_output_selector()

    def _change_layout(self):
        paired = self.layout_choice.currentText() == "Paired-end"
        self.paired_inputs.setVisible(paired)
        self.single_inputs.setVisible(not paired)

    def _select_paired_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select all paired-end FASTQ files", "", FASTQ_FILTER
        )
        if not files:
            return
        self.paired_fastq_files = files
        pairs, unmatched = self._pair_reads(files)
        self.paired_label.setText(
            f"{len(files)} files selected  •  {len(pairs)} R1/R2 pair(s) detected"
            + (f"  •  {len(unmatched)} file(s) could not be paired" if unmatched else "")
        )
        self.add_log(f"Selected {len(files)} paired-end FASTQ files; detected {len(pairs)} pair(s).")
        if unmatched:
            self.add_log("Unpaired files: " + ", ".join(Path(file_name).name for file_name in unmatched))
        self._set_default_output_from_files(files)

    def _select_single_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select single-end FASTQ files", "", FASTQ_FILTER
        )
        if not files:
            return
        self.single_fastq_files = files
        self.single_label.setText(f"{len(files)} single-end FASTQ file(s) selected")
        self.add_log(f"Selected {len(files)} single-end FASTQ file(s).")
        self._set_default_output_from_files(files)

    def _set_default_output_from_files(self, files: list[str]):
        if self.output_directory is None:
            self.set_default_output_directory(
                Path(files[0]).resolve().parent / "bioflow_results" / "host_removed"
            )

    def _select_index_files(self):
        file_names, _ = QFileDialog.getOpenFileNames(
            self,
            "Select all 6 GRCh38 Bowtie2 index files",
            "",
            "Bowtie2 index (*.bt2 *.bt2l);;All files (*)",
        )
        if not file_names:
            return
        prefix = self._find_complete_index([Path(file_name) for file_name in file_names])
        if prefix is None:
            self.index_prefix = None
            self.index_label.setText(
                "Incomplete index selection. Select all 6 files: "
                "<prefix>.1/.2/.3/.4/.rev.1/.rev.2.bt2 (or .bt2l)."
            )
            self.add_log("Select all six matching GRCh38 Bowtie2 index files before running host removal.")
            return
        self.index_prefix = prefix
        self.index_label.setText(
            f"GRCh38 index ready (6 files selected and verified): {self.index_prefix}"
        )
        self.add_log(f"Verified complete GRCh38 Bowtie2 index: {self.index_prefix}")

    def _load_configured_index(self):
        """Use the reference installed by BioFlow's Linux launcher when present."""
        configured_prefix = os.environ.get("BIOFLOW_GRCH38_INDEX")
        if configured_prefix and self._index_prefix_is_complete(Path(configured_prefix)):
            self.index_prefix = Path(configured_prefix)
            self.index_label.setText(
                f"GRCh38 index ready (BioFlow setup): {self.index_prefix}"
            )

    @staticmethod
    def _find_complete_index(index_files: list[Path]) -> Path | None:
        """Return a prefix only when the user selected all six matching files."""
        required_parts = ("1", "2", "3", "4", "rev.1", "rev.2")
        selected_files = set(index_files)
        for extension in ("bt2", "bt2l"):
            for first_part in index_files:
                if not first_part.name.endswith(f".1.{extension}"):
                    continue
                prefix = first_part.with_name(first_part.name[: -len(f".1.{extension}")])
                expected_files = {Path(f"{prefix}.{part}.{extension}") for part in required_parts}
                if expected_files.issubset(selected_files):
                    return prefix
        return None

    @staticmethod
    def _index_prefix_is_complete(prefix: Path) -> bool:
        required_parts = ("1", "2", "3", "4", "rev.1", "rev.2")
        return any(
            all(Path(f"{prefix}.{part}.{extension}").is_file() for part in required_parts)
            for extension in ("bt2", "bt2l")
        )

    @staticmethod
    def _sample_name(file_name: str) -> str:
        name = Path(file_name).name
        for extension in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
            if name.endswith(extension):
                name = name[:-len(extension)]
                break
        return re.sub(r"(?:[_\.]R?1)?(?:[_\.]trim)?$", "", name, flags=re.IGNORECASE)

    @staticmethod
    def _safe_output_name(sample_name: str) -> str:
        """Keep Bowtie2's shell-created gzip output free of unsafe characters."""
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", sample_name).strip("._")
        return safe_name or "host_removed_sample"

    @staticmethod
    def _pair_reads(files: list[str]) -> tuple[list[tuple[str, str]], list[str]]:
        """Pair common R1/R2 or 1/2 FASTQ naming conventions automatically."""
        reads: dict[str, dict[str, str]] = {}
        unmatched: list[str] = []
        pattern = re.compile(r"(?:[_\.-])(R?[12])(?=[_\.-]|$)", re.IGNORECASE)
        for file_name in files:
            stem = Path(file_name).name
            for extension in (".fastq.gz", ".fq.gz", ".fastq", ".fq"):
                if stem.lower().endswith(extension):
                    stem = stem[: -len(extension)]
                    break
            match = pattern.search(stem)
            if not match:
                unmatched.append(file_name)
                continue
            read_number = match.group(1)[-1]
            key = f"{stem[:match.start()]}{stem[match.end():]}".lower()
            reads.setdefault(key, {})[read_number] = file_name

        pairs = []
        for pair in reads.values():
            if "1" in pair and "2" in pair:
                pairs.append((pair["1"], pair["2"]))
            else:
                unmatched.extend(pair.values())
        return pairs, unmatched

    def _show_thread_count(self, value: int):
        self.thread_count.setText(f"{value:02d}")

    def run_analysis(self):
        paired = self.layout_choice.currentText() == "Paired-end"
        if self.process is not None:
            self.add_log("Host Removal is already running.")
            return
        if paired:
            read_pairs, unmatched = self._pair_reads(self.paired_fastq_files)
            if not read_pairs or unmatched:
                self.add_log("Select complete R1/R2 pairs only. Resolve the unpaired FASTQ files before running.")
                return
        else:
            read_pairs = []
            if not self.single_fastq_files:
                self.add_log("Select one or more single-end FASTQ files before running.")
                return
        if self.index_prefix is None:
            self.add_log("Select all six GRCh38 Bowtie2 index files before running host removal.")
            return
        if self.output_directory is None:
            self.add_log("Select an output folder before running host removal.")
            return

        self.output_directory.mkdir(parents=True, exist_ok=True)
        self._pending_jobs = []
        source_sets = read_pairs if paired else [(file_name, "") for file_name in self.single_fastq_files]
        for read_1, read_2 in source_sets:
            sample = self._safe_output_name(self._sample_name(read_1))
            log_file = self.output_directory / f"{sample}_bowtie2.log"
            command = ["bowtie2", "--very-sensitive", "-p", str(self.threads.value()), "-x", str(self.index_prefix)]
            if paired:
                command.extend(["-1", read_1, "-2", read_2, "--un-conc-gz", str(self.output_directory / f"{sample}_nohost_R%.fastq.gz")])
            else:
                command.extend(["-U", read_1, "--un-gz", str(self.output_directory / f"{sample}_nohost.fastq.gz")])
            command.extend(["-S", "/dev/null"])
            self._pending_jobs.append((sample, command, log_file))

        self._completed_jobs = 0
        self._failed_jobs = 0
        self.add_log(f"Queued {len(self._pending_jobs)} sample(s) for host removal.")
        self._start_next_job()

    def _start_next_job(self):
        if not self._pending_jobs:
            self.add_log(
                f"Batch complete: {self._completed_jobs} succeeded, {self._failed_jobs} failed."
            )
            return
        sample, command, log_file = self._pending_jobs.pop(0)
        position = self._completed_jobs + self._failed_jobs + 1
        self.add_log(f"Starting sample {position}: {sample}. Log: {log_file}")
        self.start_tool(command, stderr_log=log_file)

    def _process_finished(self, exit_code, exit_status):
        super()._process_finished(exit_code, exit_status)
        if exit_code == 0:
            self._completed_jobs += 1
        else:
            self._failed_jobs += 1
        self._start_next_job()
