"""In-app provisioning of BioFlow's runtime, environments, and reference data."""

from pathlib import Path

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from backend.execution.container import ContainerResolver, detect_runtime
from backend.config import ResourceState, bowtie2_index_prefix_from_file, get_config
from backend.logger import SessionLog
from backend.setup.bootstrap import human_bytes
from backend.setup.executor import PlanExecutor
from backend.setup.manager import ComponentStatus, SetupManager
from backend.setup.preflight import blocking_failures, free_bytes, run_preflight
from gui import dialogs, theme
from gui.widgets.status_badge import StatusBadge


#: Everything needed for the QC and host-removal workflow, pre-selected.
#: Pre-ticked when the page first opens: everything one complete analysis needs,
#: and nothing it does not. This release ends at taxonomic profiling, so
#: functional profiling and its ~30 GB of reference data are deliberately absent
#: - they stay installable, just not offered by default.
CORE_COMPONENTS = (
    "env:qc",
    "env:hostrem",
    "env:taxonomy",
    "db:grch38",
    "db:metaphlan_chocophlan",
)

#: Maps a resource state onto the badge colour that communicates it.
STATE_APPEARANCE = {
    "missing": "idle",
    "incomplete": "warn",
    "managed": "ok",
    "external": "info",
    "development": "warn",
}

SECTION_TITLES = {
    "runtime": "Backend runtime",
    "environment": "Analysis environments",
    "database": "Reference data",
}


class SetupWorker(QObject):
    """Run a setup plan off the GUI thread and report progress back to it."""

    output = pyqtSignal(str)
    #: Live transfer text and completion percentage, or -1.0 when unknown.
    transfer = pyqtSignal(str, float)
    step_started = pyqtSignal(str, int, int)
    finished = pyqtSignal(bool, str)

    def __init__(self, plan):
        super().__init__()
        self.executor = PlanExecutor(
            plan,
            on_step=self._announce_step,
            on_output=self.output.emit,
            on_progress=self._announce_transfer,
        )

    def _announce_transfer(self, update) -> None:
        self.transfer.emit(update.text, -1.0 if update.percent is None else update.percent)

    def _announce_step(self, step, index: int, total: int) -> None:
        self.step_started.emit(step.title, index, total)

    def run(self) -> None:
        # As on the pipeline page: whatever happens, the interface must be told
        # the work has stopped, or Install stays disabled for the session.
        try:
            succeeded, message = self.executor.run()
        except Exception as error:  # noqa: BLE001 - reported, never swallowed
            self.output.emit(f"ERROR: setup stopped unexpectedly: {error!r}")
            self.finished.emit(False, f"Setup stopped unexpectedly: {error}")
            return
        self.finished.emit(succeeded, message)

    def cancel(self) -> None:
        self.executor.cancel()


class ComponentRow(QFrame):
    """One selectable component with its status, size, and description."""

    def __init__(self, component: ComponentStatus, on_toggle):
        super().__init__()
        self.setObjectName("componentRow")
        self.key = component.key

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        self.checkbox = QCheckBox()
        self.checkbox.stateChanged.connect(lambda _state: on_toggle())
        layout.addWidget(self.checkbox)

        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = QLabel(component.title)
        self.title.setObjectName("componentTitle")
        text.addWidget(self.title)
        description = QLabel(component.description)
        description.setObjectName("componentDescription")
        description.setWordWrap(True)
        text.addWidget(description)
        layout.addLayout(text, 1)

        self.size_label = QLabel(f"~{human_bytes(component.approximate_bytes)}")
        self.size_label.setObjectName("componentSize")
        layout.addWidget(self.size_label)

        self.status_label = StatusBadge("", "idle")
        self.status_label.setMinimumWidth(104)
        layout.addWidget(self.status_label)

        self.apply(component)

    def apply(self, component: ComponentStatus) -> None:
        """Refresh this row after a status re-check."""
        self.status_label.set_state(component.state.label, STATE_APPEARANCE.get(
            component.state.value, "idle"
        ))
        self.status_label.setToolTip(component.describe_state())
        self.setToolTip(str(component.location) if component.location else "")
        if component.kind == "runtime":
            # The runtime is added automatically whenever anything else is chosen.
            self.checkbox.setChecked(component.installed)
            self.checkbox.setEnabled(False)
            self.size_label.setVisible(not component.installed)
        elif component.is_managed:
            # Only BioFlow's own copy blocks re-installation. A resource that is
            # merely resolvable externally can still be installed as managed.
            self.checkbox.setChecked(False)
            self.checkbox.setEnabled(False)
        else:
            self.checkbox.setEnabled(True)

    @property
    def selected(self) -> bool:
        return self.checkbox.isEnabled() and self.checkbox.isChecked()


class SetupPage(QWidget):
    """Show what is installed, and install the rest without leaving the app."""

    #: Emitted after a successful install so tool pages can re-check for backends.
    setup_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("toolPage")
        self.config = get_config()
        self.manager = SetupManager(self.config)
        self.rows: dict[str, ComponentRow] = {}
        self.thread: QThread | None = None
        self.worker: SetupWorker | None = None
        self.session_log: SessionLog | None = None
        #: Rows toggle checkboxes as they are built, before the summary exists.
        self._ready = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(14)

        eyebrow = QLabel("Backend")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Setup & Resources")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            "BioFlow installs its own tools and reference data here. Nothing is "
            "written outside the folders shown below, and no system Conda or "
            "administrator access is required."
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.location_label = QLabel()
        self.location_label.setObjectName("componentDescription")
        self.location_label.setWordWrap(True)
        layout.addWidget(self.location_label)

        self.preflight_card = QFrame()
        self.preflight_card.setObjectName("resultCard")
        preflight_layout = QVBoxLayout(self.preflight_card)
        preflight_layout.setContentsMargins(14, 9, 14, 9)
        self.preflight_label = QLabel("Checking this system...")
        self.preflight_label.setObjectName("resultLabel")
        self.preflight_label.setWordWrap(True)
        preflight_layout.addWidget(self.preflight_label)
        layout.addWidget(self.preflight_card)

        self.components_layout = QVBoxLayout()
        self.components_layout.setSpacing(6)
        layout.addLayout(self.components_layout)
        self._build_component_rows()

        layout.addWidget(self._build_execution_row())
        layout.addWidget(self._build_reference_row())

        self.summary_label = QLabel()
        self.summary_label.setObjectName("componentDescription")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        buttons = QHBoxLayout()
        self.install_button = QPushButton("Install selected")
        self.install_button.setObjectName("runButton")
        self.install_button.clicked.connect(self.start_setup)
        buttons.addWidget(self.install_button, 1)
        self.recheck_button = QPushButton("Re-check")
        self.recheck_button.clicked.connect(lambda: self.refresh())
        buttons.addWidget(self.recheck_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_setup)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        # Downloads redraw their progress thousands of times. They belong on a
        # line that is overwritten, not in the log, which is for what happened.
        self.transfer_bar = QProgressBar()
        self.transfer_bar.setTextVisible(True)
        self.transfer_bar.setVisible(False)
        self.transfer_bar.setRange(0, 100)
        layout.addWidget(self.transfer_bar)
        self.transfer_label = QLabel()
        self.transfer_label.setObjectName("transferStatus")
        self.transfer_label.setVisible(False)
        layout.addWidget(self.transfer_label)

        log_title = QLabel("Setup log")
        log_title.setObjectName("logTitle")
        layout.addWidget(log_title)
        self.log = QTextEdit()
        self.log.setObjectName("executionLog")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        layout.addWidget(self.log, 1)

        self._ready = True
        self.refresh(select_core=True)

    # ------------------------------------------------------------------
    # External reference configuration
    # ------------------------------------------------------------------
    def _build_reference_row(self) -> QFrame:
        """Minimal surface for pointing BioFlow at an existing GRCh38 index."""
        frame = QFrame()
        frame.setObjectName("componentRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("Existing GRCh38 index")
        title.setObjectName("componentTitle")
        text.addWidget(title)
        self.reference_label = QLabel()
        self.reference_label.setObjectName("componentDescription")
        self.reference_label.setWordWrap(True)
        text.addWidget(self.reference_label)
        row.addLayout(text, 1)

        self.choose_reference = QPushButton("Use existing index...")
        self.choose_reference.clicked.connect(self.select_external_index)
        row.addWidget(self.choose_reference)
        self.clear_reference = QPushButton("Clear")
        self.clear_reference.clicked.connect(self.clear_external_index)
        row.addWidget(self.clear_reference)
        return frame

    def _build_execution_row(self) -> QFrame:
        """Where analysis tools run: on this machine, or in a container.

        Deliberately one control. Native is the default and needs nothing
        installed beyond BioFlow itself; container execution is for people who
        want the tools frozen, and it is worth being able to choose without
        having to learn anything about containers to do it.

        "Container" rather than "Docker": the image is OCI and runs under
        Podman or Docker, and naming one of them would suggest the other will
        not work.
        """
        frame = QFrame()
        frame.setObjectName("componentRow")
        row = QHBoxLayout(frame)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(10)

        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel("Analysis environment")
        title.setObjectName("componentTitle")
        text.addWidget(title)
        self.execution_label = QLabel()
        self.execution_label.setObjectName("componentDescription")
        self.execution_label.setWordWrap(True)
        text.addWidget(self.execution_label)
        row.addLayout(text, 1)

        self.execution_choice = QComboBox()
        self.execution_choice.addItem("On this machine", "native")
        self.execution_choice.addItem("In a container", "container")
        self.execution_choice.currentIndexChanged.connect(self.select_execution_backend)
        row.addWidget(self.execution_choice)

        self.choose_image = QPushButton("Image...")
        self.choose_image.clicked.connect(self.select_container_image)
        row.addWidget(self.choose_image)
        return frame

    def _refresh_execution_row(self) -> None:
        backend = self.config.execution_backend
        index = self.execution_choice.findData(backend)
        if index >= 0 and self.execution_choice.currentIndex() != index:
            # Set without re-entering the handler, which would save on every
            # refresh and log a change nobody made.
            self.execution_choice.blockSignals(True)
            self.execution_choice.setCurrentIndex(index)
            self.execution_choice.blockSignals(False)

        runtime = detect_runtime()
        if backend == "native":
            detail = (
                "Tools run in BioFlow's own environments on this machine. "
                "Nothing else needs installing."
            )
            if runtime:
                detail += f"  •  {runtime.title()} is available if you want containers."
        elif runtime is None:
            detail = (
                "Container execution is selected, but no container runtime is "
                "installed. Install Podman, or switch back to running on this "
                "machine."
            )
        else:
            resolver = ContainerResolver(config=self.config, image=self.config.container_image)
            if resolver.image_present():
                detail = (
                    f"Tools run in {self.config.container_image} using "
                    f"{runtime.title()}.  •  Image available."
                )
            else:
                detail = (
                    f"{runtime.title()} is installed, but the image "
                    f"{self.config.container_image} is not available. Build it "
                    f"with docker/build.sh, or choose another."
                )
        # Reference data is mounted from this machine either way, so what the
        # component list below says about databases applies to both backends.
        detail += "  •  Reference databases are used from this machine in both."
        self.execution_label.setText(detail)
        self.choose_image.setEnabled(backend == "container")

    def select_execution_backend(self) -> None:
        chosen = self.execution_choice.currentData()
        if not chosen or chosen == self.config.execution_backend:
            return
        try:
            self.config.set_execution_backend(chosen)
        except ValueError as error:
            self.add_log(str(error))
            return
        if chosen == "container":
            runtime = detect_runtime()
            if runtime is None:
                self.add_log(
                    "Container execution selected, but no container runtime was "
                    "found. Install Podman, or switch back to running on this "
                    "machine."
                )
            else:
                self.add_log(
                    f"Container execution selected. {runtime.title()} will run "
                    f"{self.config.container_image}."
                )
        else:
            self.add_log("Analysis will run in BioFlow's own environments on this machine.")
        self.refresh()
        self.setup_changed.emit()

    def select_container_image(self) -> None:
        current = self.config.container_image
        chosen, accepted = QInputDialog.getText(
            self, "Analysis image", "Image reference:", text=current
        )
        if not accepted:
            return
        try:
            self.config.set_container_image(chosen)
        except ValueError as error:
            self.add_log(str(error))
            return
        self.add_log(f"Analysis image set to {self.config.container_image}.")
        self.refresh()
        self.setup_changed.emit()

    def _refresh_reference_row(self) -> None:
        resolved = self.config.resolve_grch38_index()
        external = self.config.external_grch38_index
        if resolved.state is ResourceState.DEVELOPMENT:
            detail = (
                f"A development override ({resolved.prefix}) is active for this "
                f"session. It is not saved configuration."
            )
        elif external is not None:
            detail = f"Configured: {external}"
        else:
            detail = (
                "Not configured. BioFlow uses its own managed copy, or you can "
                "point it at an index you already have."
            )
        self.reference_label.setText(f"{detail}  •  In use: {resolved.describe()}")
        self.clear_reference.setEnabled(external is not None)

    def select_external_index(self) -> None:
        """Persist an explicitly chosen external index after validating it."""
        file_name = dialogs.open_file(
            self,
            "Select any file of the GRCh38 Bowtie2 index",
            "Bowtie2 index (*.bt2 *.bt2l);;All files (*)",
            str(self.config.database_root),
        )
        if not file_name:
            return
        prefix = bowtie2_index_prefix_from_file(Path(file_name))
        if prefix is None:
            self.add_log(
                f"{Path(file_name).name} is not part of a Bowtie2 index "
                f"(expected a .bt2 or .bt2l file)."
            )
            return
        try:
            self.config.set_external_grch38_index(prefix)
        except ValueError as error:
            self.add_log(str(error))
            return
        self.add_log(f"External GRCh38 index configured and saved: {prefix}")
        self.refresh()
        self.setup_changed.emit()

    def clear_external_index(self) -> None:
        self.config.set_external_grch38_index(None)
        self.add_log("External GRCh38 index cleared; BioFlow will use its managed copy.")
        self.refresh()
        self.setup_changed.emit()

    # ------------------------------------------------------------------
    # Component list
    # ------------------------------------------------------------------
    def _build_component_rows(self) -> None:
        current_kind = None
        for component in self.manager.components():
            if component.kind != current_kind:
                current_kind = component.kind
                heading = QLabel(SECTION_TITLES.get(component.kind, component.kind).upper())
                heading.setObjectName("logTitle")
                heading.setContentsMargins(2, 10, 0, 2)
                self.components_layout.addWidget(heading)
            row = ComponentRow(component, self._update_summary)
            self.rows[component.key] = row
            self.components_layout.addWidget(row)

    def refresh(self, select_core: bool = False) -> None:
        """Re-read what is installed and update every row."""
        components = self.manager.components()
        for component in components:
            row = self.rows.get(component.key)
            if row:
                row.apply(component)
        if select_core:
            for component in components:
                row = self.rows.get(component.key)
                if row and component.key in CORE_COMPONENTS and not component.installed:
                    row.checkbox.setChecked(True)
        self.location_label.setText(
            f"Backend location: {self.config.data_root}\n"
            f"Reference data: {self.config.database_root}"
        )
        self._refresh_execution_row()
        self._refresh_reference_row()
        self._update_summary()

    def selected_keys(self) -> list[str]:
        return [key for key, row in self.rows.items() if row.selected]

    def _update_summary(self) -> None:
        if not self._ready:
            return
        keys = self.selected_keys()
        if not keys:
            self.summary_label.setText(
                "Nothing selected. Tick the components you need, or press Re-check "
                "after installing them elsewhere."
            )
            self.install_button.setEnabled(False)
            self._show_preflight(0)
            return

        plan = self.manager.build_plan(keys)
        pulled_in = len(plan.components) - len(keys)
        if pulled_in == 1:
            extra = ", 1 added automatically"
        elif pulled_in > 1:
            extra = f", {pulled_in} added automatically"
        else:
            extra = ""
        # Two different questions: how long the download takes, and whether the
        # disk can hold it while it unpacks.
        download = self.manager.estimated_download_bytes(keys)
        peak = self.manager.estimated_peak_bytes(keys)
        self.summary_label.setText(
            f"{len(plan.components)} component(s) to install{extra}  •  {len(plan)} step(s)  •  "
            f"{human_bytes(download)} to download, needs {human_bytes(peak)} free while "
            f"installing  •  {human_bytes(free_bytes(self.config.database_root))} available"
        )
        self.install_button.setEnabled(self.thread is None)
        self._show_preflight(peak)

    def _show_preflight(self, required_bytes: int) -> None:
        checks = run_preflight(
            self.config,
            required_bytes,
            required_memory_bytes=self.manager.required_memory_bytes(self.selected_keys()),
        )
        self.preflight_label.setText(
            "   ".join(
                f"[{'OK' if check.passed else 'PROBLEM'}] {check.name}: {check.detail}"
                for check in checks
            )
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def start_setup(self) -> None:
        if self.thread is not None:
            self.add_log("Setup is already running.")
            return
        keys = self.selected_keys()
        if not keys:
            self.add_log("Select at least one component to install.")
            return

        required = self.manager.estimated_peak_bytes(keys)
        failures = blocking_failures(
            run_preflight(
                self.config,
                required,
                use_network_cache=False,
                required_memory_bytes=self.manager.required_memory_bytes(keys),
            )
        )
        if failures:
            for failure in failures:
                self.add_log(f"Cannot start setup — {failure.name}: {failure.detail}")
            return

        plan = self.manager.build_plan(keys)
        if plan.is_empty():
            self.add_log("Everything selected is already installed.")
            return

        self.session_log = SessionLog(self.config.log_directory, "setup")
        self.add_log(f"Setup log: {self.session_log.path}")
        self.add_log(
            f"Installing {len(plan.components)} component(s) in {len(plan)} step(s): "
            + ", ".join(plan.components)
        )

        self.worker = SetupWorker(plan)
        self.thread = QThread(self)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.output.connect(self.add_log)
        self.worker.transfer.connect(self._show_transfer)
        self.worker.step_started.connect(self._announce_step)
        self.worker.finished.connect(self._setup_finished)
        self._set_running(True)
        self.thread.start()

    def cancel_setup(self) -> None:
        if self.worker:
            self.add_log("Cancelling after the current command stops...")
            self.cancel_button.setEnabled(False)
            self.worker.cancel()

    @property
    def is_running(self) -> bool:
        """True while a plan is installing, so the window can guard its close."""
        return self.thread is not None

    def shutdown(self) -> None:
        """Stop an in-flight install cleanly instead of orphaning its commands.

        A large reference download can run for hours, so closing the window has
        to terminate the child process rather than leave it running detached.
        """
        if self.worker:
            self.worker.cancel()
        if self.thread:
            self.thread.quit()
            if not self.thread.wait(15000):
                self.thread.terminate()
                self.thread.wait()
            self.thread = None
        self.worker = None
        if self.session_log:
            self.session_log.close()
            self.session_log = None

    def _announce_step(self, title: str, index: int, total: int) -> None:
        self.add_log("")
        self.add_log(f"[{index}/{total}] {title}")
        self.progress.setMaximum(total)
        self.progress.setValue(index - 1)
        self.progress.setFormat(f"%v of %m  ·  {title}")

    def _setup_finished(self, succeeded: bool, message: str) -> None:
        self.add_log("")
        self.add_log(message)
        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None
        if self.session_log:
            self.session_log.close()
            self.session_log = None
        self._set_running(False)
        if succeeded:
            self.setup_changed.emit()

    def _set_running(self, running: bool) -> None:
        """Lock the selection while a plan is installing, and unlock it after."""
        self.install_button.setEnabled(not running)
        self.recheck_button.setEnabled(not running)
        self.cancel_button.setEnabled(running)
        self.progress.setVisible(running)
        if not running:
            self.transfer_bar.setVisible(False)
            self.transfer_label.setVisible(False)
        if not running:
            self.progress.reset()
        if running:
            for row in self.rows.values():
                row.checkbox.setEnabled(False)
        else:
            # refresh() restores each row's correct enabled state from its status.
            self.refresh()

    def _show_transfer(self, text: str, percent: float) -> None:
        """Replace the live transfer line rather than appending to the log."""
        self.transfer_label.setText(text)
        self.transfer_label.setVisible(True)
        if percent < 0.0:
            self.transfer_bar.setRange(0, 0)  # Busy indicator; no total known.
        else:
            self.transfer_bar.setRange(0, 100)
            self.transfer_bar.setValue(int(percent))
        self.transfer_bar.setVisible(True)
        # The session log keeps the full detail the panel deliberately drops,
        # so a failed install can still be diagnosed after the fact.
        if self.session_log:
            self.session_log.write(text)

    def add_log(self, message: str) -> None:
        self.log.append(message)
        if self.session_log and message:
            self.session_log.write(message)
