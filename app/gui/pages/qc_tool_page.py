"""Reusable Qt components for BioFlow quality-control tools."""

from pathlib import Path
from datetime import datetime
import os
import shutil
import shlex

from PyQt6.QtCore import QProcess
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFrame, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from backend.history import RunHistory


class QCToolPage(QWidget):
    """Base page that runs one command without blocking the Qt interface."""

    def __init__(self, tool_name: str, environment_name: str = "bioflow-qc"):
        super().__init__()
        self.tool_name = tool_name
        self.environment_name = environment_name
        self.output_directory: Path | None = None
        self.output_selected_by_user = False
        self.process: QProcess | None = None
        self._execution_log_handle = None
        self._history_run_id: int | None = None
        self._history_input_summary = "No input summary recorded"
        self.setObjectName("toolPage")

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(34, 30, 34, 30)
        self.layout.setSpacing(14)

        eyebrow = QLabel("Quality control")
        eyebrow.setObjectName("eyebrow")
        self.layout.addWidget(eyebrow)

        title = QLabel(tool_name)
        title.setObjectName("pageTitle")
        self.layout.addWidget(title)

        self.description = QLabel()
        self.description.setObjectName("pageDescription")
        self.description.setWordWrap(True)
        self.layout.addWidget(self.description)

        self.controls = QVBoxLayout()
        self.layout.addLayout(self.controls)

        self.run_button = QPushButton(f"Run {tool_name}")
        self.run_button.setObjectName("runButton")
        self.run_button.clicked.connect(self.run_analysis)
        self.layout.addWidget(self.run_button)

        self.result_card = QFrame()
        self.result_card.setObjectName("resultCard")
        result_layout = QHBoxLayout(self.result_card)
        result_layout.setContentsMargins(14, 9, 14, 9)
        self.result_label = QLabel("No completed run yet")
        self.result_label.setObjectName("resultLabel")
        result_layout.addWidget(self.result_label, 1)
        self.open_results_button = QPushButton("Open output")
        self.open_results_button.setObjectName("openResultsButton")
        self.open_results_button.setEnabled(False)
        self.open_results_button.clicked.connect(self._open_results_directory)
        result_layout.addWidget(self.open_results_button)
        self.layout.addWidget(self.result_card)

        log_title = QLabel("Run log")
        log_title.setObjectName("logTitle")
        self.layout.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setObjectName("executionLog")
        self.log.setReadOnly(True)
        self.layout.addWidget(self.log)

    def run_analysis(self):
        """Implemented by each tool page after it validates its inputs."""
        raise NotImplementedError

    def add_output_selector(self):
        """Add an optional destination chooser below a tool's input controls."""
        self.output_label = QLabel("Output folder: chosen automatically after selecting input")
        self.output_label.setWordWrap(True)
        self.controls.addWidget(self.output_label)

        self.output_button = QPushButton("Choose output folder")
        self.output_button.clicked.connect(self.select_output_directory)
        self.controls.addWidget(self.output_button)

    def set_default_output_directory(self, directory: Path):
        """Use a sensible default without overwriting a user's own choice."""
        if not self.output_selected_by_user:
            self.output_directory = directory
        self._show_output_directory()

    def select_output_directory(self):
        initial_directory = str(self.output_directory or Path.cwd())
        directory = QFileDialog.getExistingDirectory(
            self, "Select output folder", initial_directory
        )
        if directory:
            self.output_directory = Path(directory)
            self.output_selected_by_user = True
            self._show_output_directory()
            self.add_log(f"Output folder selected: {self.output_directory}")

    def _show_output_directory(self):
        if hasattr(self, "output_label"):
            self.output_label.setText(
                f"Output folder: {self.output_directory}"
                if self.output_directory else "Output folder: not selected"
            )

    def set_input_summary(self, summary: str):
        """Show a concise sample-queue message without changing execution logic."""
        self._history_input_summary = summary
        self.result_label.setText(summary)

    def _open_results_directory(self):
        if self.output_directory and self.output_directory.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_directory)))

    def start_tool(self, command: list[str], stderr_log: Path | None = None):
        """Run a tool directly or in its configured Conda/Micromamba environment."""
        if self.process is not None:
            self.add_log(f"{self.tool_name} is already running.")
            return

        executable = shutil.which(command[0])
        if executable:
            program, arguments = executable, command[1:]
        elif micromamba := (os.environ.get("BIOFLOW_MICROMAMBA") or shutil.which("micromamba")):
            program = micromamba
            arguments = ["run"]
            if root_prefix := os.environ.get("BIOFLOW_MAMBA_ROOT_PREFIX"):
                arguments.extend(["-r", root_prefix])
            arguments.extend(["-n", self.environment_name, *command])
        elif shutil.which("conda"):
            program = "conda"
            arguments = ["run", "--no-capture-output", "-n", self.environment_name, *command]
        else:
            self.add_log(
                f"Cannot start {self.tool_name}: install {command[0]} on PATH "
                f"or create the environment '{self.environment_name}'."
            )
            return

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.errorOccurred.connect(self._process_error)
        self.process.finished.connect(self._process_finished)

        log_path = stderr_log or self._default_log_path()
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._execution_log_handle = log_path.open("w", encoding="utf-8")
        self._history_run_id = RunHistory.start_run(
            tool_name=self.tool_name,
            input_summary=self._history_input_summary,
            output_directory=str(self.output_directory) if self.output_directory else None,
            command=shlex.join([program, *arguments]),
            log_path=str(log_path) if log_path else None,
        )

        self._set_running(True)
        self.add_log(f"Starting: {program} {' '.join(arguments)}")
        if self.output_directory:
            self.add_log(f"Writing results to: {self.output_directory}")
        self.process.start(program, arguments)

    def _read_stdout(self):
        if self.process:
            self.add_log(bytes(self.process.readAllStandardOutput()).decode(errors="replace").rstrip())

    def _read_stderr(self):
        if self.process:
            output = bytes(self.process.readAllStandardError()).decode(errors="replace")
            self.add_log(output.rstrip())

    def _process_error(self, error):
        if self.process:
            self.add_log(f"Process error: {self.process.errorString()}")
            if error == QProcess.ProcessError.FailedToStart:
                RunHistory.finish_run(self._history_run_id, "failed", None)
                self._close_execution_log()
                self.process = None
                self._set_running(False)

    def _process_finished(self, exit_code, _exit_status):
        self._read_stdout()
        self._read_stderr()
        if exit_code == 0:
            self.add_log(f"{self.tool_name} finished successfully. Results: {self.output_directory}")
            self.result_label.setText(f"Completed — {self.output_directory}")
            self.open_results_button.setEnabled(True)
        else:
            self.add_log(f"{self.tool_name} failed with exit code {exit_code}. See the log above.")
        RunHistory.finish_run(
            self._history_run_id, "completed" if exit_code == 0 else "failed", exit_code
        )
        self._history_run_id = None
        self._close_execution_log()
        self.process = None
        self._set_running(False)

    def _default_log_path(self) -> Path | None:
        if self.output_directory is None:
            return None
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_tool_name = "".join(
            character.lower() if character.isalnum() else "_" for character in self.tool_name
        ).strip("_")
        return self.output_directory / "logs" / f"{timestamp}_{safe_tool_name}.log"

    def _close_execution_log(self):
        if self._execution_log_handle:
            self._execution_log_handle.close()
            self._execution_log_handle = None

    def _set_running(self, running: bool):
        self.run_button.setDisabled(running)
        self._set_layout_enabled(self.controls, not running)

    @staticmethod
    def _set_layout_enabled(layout, enabled: bool):
        """Enable/disable widgets nested in the page's control layouts."""
        for index in range(layout.count()):
            item = layout.itemAt(index)
            if item.widget():
                item.widget().setEnabled(enabled)
            elif item.layout():
                QCToolPage._set_layout_enabled(item.layout(), enabled)

    def add_log(self, message: str):
        if message:
            self.log.append(message)
            if self._execution_log_handle:
                self._execution_log_handle.write(f"{message}\n")
                self._execution_log_handle.flush()
