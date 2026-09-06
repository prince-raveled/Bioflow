"""Host-read removal with Bowtie2 and a configured human reference index."""

from pathlib import Path
import re

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from backend.config import get_config
from backend.samples import (
    FASTQ_EXTENSIONS,
    FASTQ_FILE_FILTER,
    ReadLayout,
    detect_samples,
    sample_name_for,
)
from backend.execution.record import ValidationResult
from backend.execution.stage import check_exists, check_gzip_readable
from backend.execution.stages.host_removal import HostRemovalStage
from backend.setup.manager import BOWTIE2_INDEX_PARTS, bowtie2_index_is_complete
from gui.widgets.status_badge import StatusBadge
from gui.pages.qc_tool_page import QCToolPage, tool_context
from gui import dialogs


class HostRemovalPage(QCToolPage):
    """Run Bowtie2 against GRCh38 and retain unmapped microbial reads."""

    def __init__(self):
        super().__init__(
            "Host Removal", environment_key="hostrem", section="Host removal"
        )
        self.paired_fastq_files: list[str] = []
        self.single_fastq_files: list[str] = []
        self.index_prefix: Path | None = None
        self._pending_jobs: list[tuple[str, list[str], Path]] = []
        self._completed_jobs = 0
        self._failed_jobs = 0
        self._current_job = None
        self.description.setText(
            "Remove human reads with Bowtie2. Select read layout, GRCh38 index, and an output folder."
        )

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Read layout"))
        self.layout_choice = QComboBox()
        # Paired-end first: the common case for host removal. Labels come from
        # the enum, and the enum comes back out of chosen_layout(), so the
        # choice is never recovered by comparing display text.
        for layout in (ReadLayout.PAIRED, ReadLayout.SINGLE):
            self.layout_choice.addItem(layout.label, layout)
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

        reference_card = QFrame()
        reference_card.setObjectName("card")
        reference_layout = QVBoxLayout(reference_card)
        reference_layout.setContentsMargins(12, 10, 12, 10)
        reference_layout.setSpacing(8)

        reference_header = QHBoxLayout()
        reference_title = QLabel("Human reference (GRCh38)")
        reference_title.setObjectName("componentTitle")
        reference_header.addWidget(reference_title)
        reference_header.addStretch()
        # States the resolved source plainly: managed, external, or override.
        self.index_badge = StatusBadge("CHECKING", "idle")
        reference_header.addWidget(self.index_badge)
        reference_layout.addLayout(reference_header)

        self.index_label = QLabel("GRCh38 Bowtie2 index files: not selected")
        self.index_label.setObjectName("componentDescription")
        self.index_label.setWordWrap(True)
        reference_layout.addWidget(self.index_label)

        index_button = QPushButton("Select all 6 GRCh38 index files")
        index_button.clicked.connect(self._select_index_files)
        reference_layout.addWidget(index_button)
        self.controls.addWidget(reference_card)
        self._load_configured_index()

        thread_row = QHBoxLayout()
        thread_row.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(self.default_thread_count())
        self.threads.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.threads.setTickInterval(4)
        self.threads.valueChanged.connect(self._show_thread_count)
        thread_row.addWidget(self.threads)
        self.thread_count = QLabel(self.thread_label(self.threads.value()))
        self.thread_count.setObjectName("threadCount")
        thread_row.addWidget(self.thread_count)
        self.controls.addLayout(thread_row)
        self.add_output_selector()

    def chosen_layout(self) -> ReadLayout:
        """The layout the user picked, as the enum rather than its label.

        Reading it back by comparing the combo's text meant a change to the
        label would silently turn every run single-end, with nothing on screen
        to show that anything had changed.
        """
        return self.layout_choice.currentData()

    def _change_layout(self):
        paired = self.chosen_layout() is ReadLayout.PAIRED
        self.paired_inputs.setVisible(paired)
        self.single_inputs.setVisible(not paired)

    def _select_paired_files(self):
        files = dialogs.open_files(self, "Select all paired-end FASTQ files", FASTQ_FILE_FILTER)
        if not files:
            return
        self.paired_fastq_files = files
        pairs, unmatched = self._pair_reads(files)
        self.paired_label.setText(
            f"{len(files)} files selected  •  {len(pairs)} R1/R2 pair(s) detected"
            + (f"  •  {len(unmatched)} file(s) could not be paired" if unmatched else "")
        )
        self.add_log(f"Selected {len(files)} paired-end FASTQ files; detected {len(pairs)} pair(s).")
        self.set_input_summary(f"{len(pairs)} paired sample(s) detected")
        if unmatched:
            self.add_log("Unpaired files: " + ", ".join(Path(file_name).name for file_name in unmatched))
        self._set_default_output_from_files(files)

    def _select_single_files(self):
        files = dialogs.open_files(self, "Select single-end FASTQ files", FASTQ_FILE_FILTER)
        if not files:
            return
        self.single_fastq_files = files
        self.single_label.setText(f"{len(files)} single-end FASTQ file(s) selected")
        self.set_input_summary(f"{len(files)} single-end sample(s) staged")
        self.add_log(f"Selected {len(files)} single-end FASTQ file(s).")
        self._set_default_output_from_files(files)

    def _set_default_output_from_files(self, files: list[str]):
        if self.output_directory is None:
            self.set_default_output_directory(
                Path(files[0]).resolve().parent / "bioflow_results" / "host_removed"
            )

    def _select_index_files(self):
        file_names = dialogs.open_files(
            self,
            "Select all 6 GRCh38 Bowtie2 index files",
            "Bowtie2 index (*.bt2 *.bt2l);;All files (*)",
        )
        if not file_names:
            return
        prefix = self._find_complete_index([Path(file_name) for file_name in file_names])
        if prefix is None:
            self.index_prefix = None
            self.index_badge.set_state("INCOMPLETE", "warn")
            self.index_label.setText(
                "Incomplete index selection. Select all 6 files: "
                "<prefix>.1/.2/.3/.4/.rev.1/.rev.2.bt2 (or .bt2l)."
            )
            self.add_log("Select all six matching GRCh38 Bowtie2 index files before running host removal.")
            return
        self.index_prefix = prefix
        self.index_badge.set_state("SELECTED", "info")
        self.index_badge.setToolTip(f"Chosen for this run: {self.index_prefix}")
        self.index_label.setText(f"Selected for this run / {self.index_prefix}")
        self.add_log(f"Verified complete GRCh38 Bowtie2 index: {self.index_prefix}")

    #: Badge colour per resolved reference state.
    REFERENCE_APPEARANCE = {
        "managed": "ok",
        "external": "info",
        "development": "warn",
        "incomplete": "warn",
        "missing": "idle",
    }

    def _load_configured_index(self):
        """Use whichever GRCh38 index BioFlow's resource manager resolves.

        Presentation only: the resolution itself is the resource manager's.
        """
        resolved = get_config().resolve_grch38_index()
        appearance = self.REFERENCE_APPEARANCE.get(resolved.state.value, "idle")
        self.index_badge.set_state(resolved.state.label.upper(), appearance)
        if resolved.usable:
            self.index_prefix = resolved.prefix
            self.index_badge.setToolTip(resolved.describe())
            self.index_label.setText(f"{resolved.state.label} / {resolved.prefix}")
        else:
            self.index_prefix = None
            self.index_badge.setToolTip(resolved.state.label)
            self.index_label.setText(
                f"{resolved.state.label}. Install it from Setup & Resources, "
                "or select all six index files below."
            )

    @staticmethod
    def _find_complete_index(index_files: list[Path]) -> Path | None:
        """Return a prefix only when the user selected all six matching files."""
        required_parts = BOWTIE2_INDEX_PARTS
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
        """Delegates to the resource manager's canonical index check."""
        return bowtie2_index_is_complete(prefix)

    @staticmethod
    def _sample_name(file_name: str) -> str:
        name = Path(file_name).name
        for extension in FASTQ_EXTENSIONS:
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
        """Pair R1/R2 files using the canonical detector in backend.samples.

        This page keeps its own thin wrapper only to return plain strings in the
        shape its batch loop expects; the pairing rules themselves live in one
        place so this page and the workflow engine can never disagree.
        """
        result = detect_samples([Path(name) for name in files], ReadLayout.PAIRED)
        pairs = [(str(sample.read1), str(sample.read2)) for sample in result.samples]
        unmatched = [str(path) for path in result.unassigned]
        return pairs, unmatched

    def _show_thread_count(self, value: int):
        self.thread_count.setText(f"{value:02d}")

    def run_analysis(self):
        paired = self.chosen_layout() is ReadLayout.PAIRED
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
        stage = HostRemovalStage()
        # One context for the batch: every job shares the index and the thread
        # count, and the builder reads both from here.
        context = tool_context(
            self.output_directory,
            self.threads.value(),
            host_index_prefix=Path(self.index_prefix),
        )
        source_sets = read_pairs if paired else [(file_name, "") for file_name in self.single_fastq_files]
        for read_1, read_2 in source_sets:
            sample = self._safe_output_name(self._sample_name(read_1))
            log_file = self.output_directory / f"{sample}_bowtie2.log"
            # The sample token is already sanitised, but the user's chosen output
            # folder is not, and Bowtie2 passes these paths through its own
            # shell; the stage's builder is what quotes it.
            unmatched = self.output_directory / (
                f"{sample}_nohost_R%.fastq.gz" if paired else f"{sample}_nohost.fastq.gz"
            )
            built = stage.removal_command(
                reads=[Path(read_1)] + ([Path(read_2)] if paired else []),
                unmatched=unmatched,
                log=log_file,
                context=context,
                paired=paired,
                label=sample,
            )
            command = list(built.command)
            expected = (
                [self.output_directory / f"{sample}_nohost_R{mate}.fastq.gz" for mate in (1, 2)]
                if paired
                else [self.output_directory / f"{sample}_nohost.fastq.gz"]
            )
            self._pending_jobs.append((sample, command, log_file, expected))

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
        sample, command, log_file, expected = self._pending_jobs.pop(0)
        position = self._completed_jobs + self._failed_jobs + 1
        self._current_job = (sample, log_file, expected)
        self.add_log(f"Starting sample {position}: {sample}. Log: {log_file}")
        self.start_tool(command, stderr_log=log_file)

    def _process_finished(self, exit_code, exit_status):
        super()._process_finished(exit_code, exit_status)
        if exit_code != 0:
            self._failed_jobs += 1
            self._current_job = None
            self._start_next_job()
            return
        # Exit zero is necessary but not sufficient, which is the rule the
        # pipeline stage applies to this very command: Bowtie2 can finish
        # successfully having aligned nothing and written an empty file. Counting
        # a job as succeeded on its status alone made this page report a clean
        # batch for work the workflow would have failed.
        problems = self._output_problems()
        if problems:
            self._failed_jobs += 1
            self.add_log(f"Sample finished but its output is not usable: {problems}")
            self.result_badge.set_state("FAILED", "error")
            self.result_label.setText(problems)
        else:
            self._completed_jobs += 1
        self._current_job = None
        self._start_next_job()

    def _output_problems(self) -> str:
        """Why the finished job's output is unusable, or "" when it is fine.

        Uses the pipeline stage's own checks so the two paths cannot drift into
        disagreeing about what a successful host removal looks like.
        """
        if self._current_job is None:
            return ""
        _sample, log_file, expected = self._current_job
        result = ValidationResult()
        for path in expected:
            if check_exists(result, path, minimum_bytes=32):
                check_gzip_readable(result, path)
        if HostRemovalStage.alignment_rate(log_file) is None:
            result.add(
                "Bowtie2 reported an alignment rate",
                False,
                "no summary line in the log",
            )
        return "" if result.valid else result.summary()
