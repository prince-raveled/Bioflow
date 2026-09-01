"""Run the metagenomic workflow: choose reads, confirm the layout, execute."""

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.config import get_config
from backend.execution.pipeline import PipelineEvent, PipelineExecutor, default_stages
from backend.execution.record import StageStatus
from backend.execution.stage import RunOptions
from backend.history import RunHistory
from backend.project import Project
from backend.samples import FASTQ_FILE_FILTER, ReadLayout, validate_sample
from gui import theme
from gui.widgets.stage_flow import StageFlow
from gui.widgets.status_badge import StatusBadge



LAYOUT_CHOICES = [
    ("Detect automatically", None),
    ("Single-end", ReadLayout.SINGLE),
    ("Paired-end", ReadLayout.PAIRED),
]


class PipelineWorker(QObject):
    """Run the pipeline off the GUI thread."""

    log = pyqtSignal(str)
    # Not named "event": QObject.event() is part of Qt's dispatch, and a signal
    # of that name shadows it, breaking the worker at runtime.
    stage_event = pyqtSignal(object)
    finished = pyqtSignal(bool, str)

    def __init__(self, executor: PipelineExecutor, samples, resume: bool):
        super().__init__()
        self.executor = executor
        self.samples = samples
        self.resume = resume

    def run(self) -> None:
        outcome = self.executor.run(self.samples, resume=self.resume)
        self.finished.emit(outcome.succeeded, outcome.message)

    def cancel(self) -> None:
        self.executor.cancel()


class PipelinePage(QWidget):
    """Select FASTQ input, confirm single-end or paired-end, and run the workflow."""

    def __init__(self):
        super().__init__()
        self.setObjectName("toolPage")
        self.config = get_config()
        self.selected_files: list[Path] = []
        self.project: Project | None = None
        self.output_directory: Path | None = None
        self.thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.stage_boxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(12)

        eyebrow = QLabel("Analysis")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Run workflow")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            "Select FASTQ files, confirm whether they are single-end or paired-end, "
            "then run the workflow. Completed stages are reused when you run again."
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addLayout(self._build_input_row())
        layout.addLayout(self._build_layout_row())
        layout.addWidget(self._build_sample_table())
        layout.addWidget(self._build_stage_row())
        layout.addWidget(self._build_flow_row())
        layout.addLayout(self._build_options_row())
        layout.addWidget(self._build_status_card())
        layout.addLayout(self._build_action_row())

        log_title = QLabel("Analysis log")
        log_title.setObjectName("logTitle")
        layout.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setObjectName("executionLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(160)
        layout.addWidget(self.log, 1)

        self.refresh_readiness()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_input_row(self):
        row = QVBoxLayout()
        self.input_label = QLabel("No FASTQ files selected")
        self.input_label.setWordWrap(True)
        row.addWidget(self.input_label)

        buttons = QHBoxLayout()
        browse = QPushButton("Select FASTQ files")
        browse.clicked.connect(self.select_files)
        buttons.addWidget(browse)
        self.output_button = QPushButton("Choose results folder")
        self.output_button.clicked.connect(self.select_output_directory)
        buttons.addWidget(self.output_button)
        buttons.addWidget(QLabel("Project name"))
        self.project_name = QLineEdit("bioflow-analysis")
        buttons.addWidget(self.project_name, 1)
        row.addLayout(buttons)

        self.output_label = QLabel("Results folder: chosen automatically from the input")
        self.output_label.setObjectName("componentDescription")
        self.output_label.setWordWrap(True)
        row.addWidget(self.output_label)
        return row

    def _build_layout_row(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Read layout"))
        self.layout_choice = QComboBox()
        for label, _value in LAYOUT_CHOICES:
            self.layout_choice.addItem(label)
        self.layout_choice.currentIndexChanged.connect(self._rebuild_project)
        row.addWidget(self.layout_choice)
        self.layout_note = QLabel("")
        self.layout_note.setObjectName("componentDescription")
        row.addWidget(self.layout_note, 1)
        return row

    def _build_sample_table(self):
        self.sample_table = QTreeWidget()
        self.sample_table.setObjectName("historyTable")
        self.sample_table.setHeaderLabels(["Sample", "Layout", "Read 1", "Read 2", "Input check"])
        self.sample_table.setRootIsDecorated(False)
        self.sample_table.setAlternatingRowColors(True)
        self.sample_table.setMinimumHeight(120)
        for index, width in enumerate((150, 100, 200, 200, 240)):
            self.sample_table.setColumnWidth(index, width)
        return self.sample_table

    def _build_stage_row(self):
        frame = QFrame()
        frame.setObjectName("componentRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 8, 12, 8)
        row.addWidget(QLabel("Stages"))
        for stage in default_stages():
            box = QCheckBox(stage.title.split(" (")[0])
            box.setToolTip(f"{stage.title} — environment: {stage.environment_key}")
            box.setChecked(True)
            box.stateChanged.connect(lambda _s: self._on_stage_toggled())
            self.stage_boxes[stage.key] = box
            row.addWidget(box)
        row.addStretch()
        return frame

    def _on_stage_toggled(self):
        self._sync_flow_selection()
        self.refresh_readiness()

    def _build_flow_row(self):
        """A read-only view of the pipeline and each stage's live state."""
        frame = QFrame()
        frame.setObjectName("card")
        column = QVBoxLayout(frame)
        column.setContentsMargins(14, 10, 14, 12)
        column.setSpacing(8)

        heading = QLabel("PIPELINE")
        heading.setObjectName("logTitle")
        column.addWidget(heading)

        self.stage_flow = StageFlow(default_stages())
        column.addWidget(self.stage_flow)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        column.addWidget(self.progress)
        return frame

    def _build_options_row(self):
        row = QHBoxLayout()
        row.addWidget(QLabel("Threads"))
        self.threads = QSlider(Qt.Orientation.Horizontal)
        self.threads.setRange(1, 32)
        self.threads.setValue(max(1, min(8, self.config.default_threads)))
        self.threads.valueChanged.connect(
            lambda value: self.thread_count.setText(f"{value:02d}")
        )
        row.addWidget(self.threads)
        self.thread_count = QLabel(f"{self.threads.value():02d}")
        self.thread_count.setObjectName("threadCount")
        row.addWidget(self.thread_count)

        self.protein_only = QCheckBox("HUMAnN protein-only mode")
        self.protein_only.setChecked(True)
        self.protein_only.setToolTip(
            "Uses --bypass-prescreen --bypass-nucleotide-search. Needs only UniRef50 "
            "and far less memory. Gene families and pathways are still produced, but "
            "genes are not attributed to individual species."
        )
        self.protein_only.stateChanged.connect(lambda _s: self.refresh_readiness())
        row.addWidget(self.protein_only)

        self.resume_box = QCheckBox("Reuse valid results")
        self.resume_box.setChecked(True)
        self.resume_box.setToolTip(
            "Skip stages whose inputs, commands and outputs are unchanged and still valid."
        )
        row.addWidget(self.resume_box)
        row.addStretch()
        return row

    def _build_status_card(self):
        card = QFrame()
        card.setObjectName("resultCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(14, 9, 14, 9)
        self.status_label = QLabel("Select FASTQ files to begin.")
        self.status_label.setObjectName("resultLabel")
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        self.open_results = QPushButton("Open results")
        self.open_results.setObjectName("openResultsButton")
        self.open_results.setEnabled(False)
        self.open_results.clicked.connect(self._open_results)
        row.addWidget(self.open_results)
        return card

    def _build_action_row(self):
        row = QHBoxLayout()
        self.run_button = QPushButton("Run workflow")
        self.run_button.setObjectName("runButton")
        self.run_button.setEnabled(False)
        self.run_button.clicked.connect(self.start_run)
        row.addWidget(self.run_button, 1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        row.addWidget(self.cancel_button)
        return row

    # ------------------------------------------------------------------
    # Input handling
    # ------------------------------------------------------------------
    def select_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select FASTQ files", "", FASTQ_FILE_FILTER)
        if not files:
            return
        self.selected_files = [Path(name) for name in files]
        self.input_label.setText(f"{len(self.selected_files)} file(s) selected")
        if self.output_directory is None:
            self.output_directory = self.selected_files[0].resolve().parent / "bioflow_results"
        self._rebuild_project()

    def select_output_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select results folder", str(self.output_directory or Path.home())
        )
        if directory:
            self.output_directory = Path(directory)
            self._rebuild_project()

    def chosen_layout(self) -> ReadLayout | None:
        return LAYOUT_CHOICES[self.layout_choice.currentIndex()][1]

    def selected_stages(self):
        return [stage for stage in default_stages() if self.stage_boxes[stage.key].isChecked()]

    def _sync_flow_selection(self):
        if hasattr(self, "stage_flow"):
            self.stage_flow.set_enabled_stages({s.key for s in self.selected_stages()})

    def _rebuild_project(self):
        self.sample_table.clear()
        if not self.selected_files or self.output_directory is None:
            self.project = None
            self.refresh_readiness()
            return

        options = RunOptions(
            threads=self.threads.value(),
            humann_protein_only=self.protein_only.isChecked(),
        )
        name = self.project_name.text().strip() or "bioflow-analysis"
        self.project, problems = Project.from_files(
            name,
            self.output_directory / name,
            self.selected_files,
            layout=self.chosen_layout(),
            options=options,
        )
        self.output_label.setText(f"Results folder: {self.project.root}")
        self.layout_note.setText(
            f"{len(self.project.samples)} sample(s) as {self.project.layout.label}. "
            "Change the layout above to override."
        )

        for sample in self.project.samples:
            issues = validate_sample(sample)
            item = QTreeWidgetItem([
                sample.name,
                sample.layout.label,
                sample.read1.name,
                sample.read2.name if sample.read2 else "—",
                "; ".join(issues) if issues else "valid",
            ])
            self.sample_table.addTopLevelItem(item)

        for problem in problems:
            self.add_log(f"Input problem: {problem}")
        self._sync_flow_selection()
        self.refresh_readiness(problems)

    # ------------------------------------------------------------------
    def refresh_readiness(self, problems: list[str] | None = None):
        """Say precisely why the run cannot start, rather than just disabling it."""
        if self.thread is not None:
            return
        if not self.project or not self.project.samples:
            self.status_label.setText(
                "Select FASTQ files to begin."
                if not problems else "No usable samples: " + "; ".join(problems[:2])
            )
            self.run_button.setEnabled(False)
            return

        input_problems = self.project.validate_inputs()
        if input_problems:
            self.status_label.setText("Input problems: " + "; ".join(input_problems[:2]))
            self.run_button.setEnabled(False)
            return

        stages = self.selected_stages()
        if not stages:
            self.status_label.setText("Select at least one stage.")
            self.run_button.setEnabled(False)
            return

        executor = PipelineExecutor(self.project.context(self.config), stages)
        missing = executor.missing_components()
        if missing:
            self.status_label.setText(
                f"Cannot run yet — not installed: {executor.describe_missing(missing)}. "
                f"Install it from Setup & Resources."
            )
            self.run_button.setEnabled(False)
            return

        self.status_label.setText(
            f"Ready: {len(stages)} stage(s) over {len(self.project.samples)} "
            f"{self.project.layout.label.lower()} sample(s)."
        )
        self.run_button.setEnabled(True)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def start_run(self):
        if self.thread is not None or self.project is None:
            return
        stages = self.selected_stages()
        self.project.options = RunOptions(
            threads=self.threads.value(),
            humann_protein_only=self.protein_only.isChecked(),
        )
        self.project.stage_keys = [stage.key for stage in stages]
        self.project.save()

        executor = PipelineExecutor(
            self.project.context(self.config),
            stages,
            on_log=lambda message: self.log_signal_safe(message),
            on_event=lambda event: self.event_signal_safe(event),
        )
        self.add_log(f"Project: {self.project.root}")
        self.add_log(
            f"{len(self.project.samples)} {self.project.layout.label.lower()} sample(s), "
            f"{len(stages)} stage(s), {self.threads.value()} thread(s)."
        )
        self._history_id = RunHistory.start_run(
            tool_name="Workflow",
            input_summary=(
                f"{len(self.project.samples)} {self.project.layout.label.lower()} sample(s)"
            ),
            output_directory=str(self.project.root),
            command=" → ".join(stage.key for stage in stages),
            log_path=str(self.project.workspace.logs),
        )

        self.worker = PipelineWorker(executor, self.project.samples, self.resume_box.isChecked())
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.add_log)
        self.worker.stage_event.connect(self._show_event)
        self.worker.finished.connect(self._run_finished)
        self.stage_flow.reset({stage.key for stage in stages})
        self.progress.setVisible(True)
        self._set_running(True)
        self.thread.start()

    def log_signal_safe(self, message: str):
        if self.worker:
            self.worker.log.emit(message)

    def event_signal_safe(self, event: PipelineEvent):
        if self.worker:
            self.worker.stage_event.emit(event)

    def _show_event(self, event: PipelineEvent):
        """Mirror execution state into the view. Never influences execution."""
        self.stage_flow.set_stage_status(event.stage_key, event.status)
        if event.total:
            self.progress.setMaximum(event.total)
            self.progress.setValue(event.index)
            self.progress.setFormat(f"%v of %m stages  ·  {event.stage_title}")
        if event.status is StageStatus.RUNNING:
            self.status_label.setText(
                f"[{event.index}/{event.total}] {event.stage_title} — {event.sample_name}"
            )

    def cancel_run(self):
        if self.worker:
            self.add_log("Cancelling after the current command stops...")
            self.cancel_button.setEnabled(False)
            self.worker.cancel()

    def _run_finished(self, succeeded: bool, message: str):
        self.add_log("")
        self.add_log(message)
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None
        RunHistory.finish_run(
            getattr(self, "_history_id", None), "completed" if succeeded else "failed", 0 if succeeded else 1
        )
        self.open_results.setEnabled(self.project is not None and self.project.root.exists())
        self._set_running(False)
        # Refresh first, then show the verdict, so it is not overwritten.
        self.refresh_readiness()
        self.status_label.setText(message)

    def _set_running(self, running: bool):
        self.run_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.layout_choice.setEnabled(not running)
        self.output_button.setEnabled(not running)
        for box in self.stage_boxes.values():
            box.setEnabled(not running)

    def shutdown(self):
        """Stop a running analysis cleanly when the window closes."""
        if hasattr(self, "stage_flow"):
            self.stage_flow.stop()
        if self.worker:
            self.worker.cancel()
        if self.thread:
            self.thread.quit()
            if not self.thread.wait(15000):
                self.thread.terminate()
                self.thread.wait()
            self.thread = None
        self.worker = None

    @property
    def is_running(self) -> bool:
        return self.thread is not None

    def _open_results(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        if self.project and self.project.root.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.root)))

    def add_log(self, message: str):
        self.log.append(message)
