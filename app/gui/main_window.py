"""BioFlow's desktop interface."""

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from backend.setup.manager import SetupManager
from backend.execution.pipeline import default_stages
from gui import theme
from gui.navigation import check_pages_match
from gui.pages.fastp_page import FastPPage
from gui.pages.fastqc_page import FastQCPage
from gui.pages.host_removal_page import HostRemovalPage
from gui.pages.history_page import HistoryPage
from gui.pages.metaphlan_page import MetaPhlAnPage
from gui.pages.multiqc_page import MultiQCPage
from gui.pages.pipeline_page import PipelinePage
from gui.pages.setup_page import SetupPage
from gui.widgets.sidebar import SidebarPanel
from gui.widgets.workflow_ribbon import WorkflowRibbon
from gui.widgets.status_badge import StatusBadge
from gui.widgets.video_backdrop import BackgroundVideo


class MainWindow(QWidget):
    """The BioFlow workbench: navigation, backend status, and every page."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("BioFlow — Metagenomics")
        self.resize(1180, 760)
        self.setMinimumSize(880, 560)
        self.setStyleSheet(theme.STYLESHEET)

        # The animated background is a child of the window itself, kept behind
        # every other widget and resized with it. It is transparent to input.
        self.background = BackgroundVideo(parent=self)
        self.background.ready.connect(self._note_background)
        self.background.lower()
        self.background_playing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(16)
        root.addWidget(self._make_header())
        root.addWidget(self._make_status_strip())

        content = QHBoxLayout()
        content.setSpacing(16)
        self.sidebar_panel = SidebarPanel()
        self.sidebar_panel.setMinimumWidth(212)
        self.sidebar_panel.setMaximumWidth(236)
        self.sidebar = self.sidebar_panel.tree
        self.sidebar.itemClicked.connect(self.show_page)
        content.addWidget(self.sidebar_panel)

        self.pages = QStackedWidget()
        setup_page = SetupPage()
        host_removal_page = HostRemovalPage()
        # A finished install makes new tools and references available immediately.
        setup_page.setup_changed.connect(host_removal_page._load_configured_index)
        pipeline_page = PipelinePage()
        metaphlan_page = MetaPhlAnPage()
        setup_page.setup_changed.connect(pipeline_page.refresh_readiness)
        # Installing the marker database makes profiling possible without a
        # restart, so the page re-reads its state when setup finishes.
        setup_page.setup_changed.connect(metaphlan_page.refresh_database_state)
        setup_page.setup_changed.connect(self.refresh_status_strip)
        self.page_by_name = {
            "Setup & Resources": setup_page,
            "Run workflow": pipeline_page,
            "FastQC": FastQCPage(),
            "fastp": FastPPage(),
            "MultiQC": MultiQCPage(),
            "Host Removal": host_removal_page,
            "MetaPhlAn": metaphlan_page,
            "Run History": HistoryPage(),
        }
        # Fails here rather than as a navigation entry that does nothing.
        check_pages_match(list(self.page_by_name))
        self.page_containers = {}
        for name, page in self.page_by_name.items():
            container = QScrollArea()
            container.setWidgetResizable(True)
            container.setWidget(page)
            self.page_containers[name] = container
            self.pages.addWidget(container)
        content.addWidget(self.pages, 1)
        root.addLayout(content, 1)
        self._open_starting_page()
        self.refresh_status_strip()
        self.background.setGeometry(self.rect())
        self.background.lower()

    def _open_starting_page(self):
        """Start on Setup until the core quality-control backend is installed."""
        manager = SetupManager()
        first_page = (
            "Run workflow"
            if manager.micromamba_installed() and manager.environment_installed("qc")
            else "Setup & Resources"
        )
        self.sidebar.select(first_page)
        self.pages.setCurrentWidget(self.page_containers[first_page])

    def _make_header(self) -> QWidget:
        """Brand, workflow summary, and the looping DNA visual."""
        header = QFrame()
        header.setObjectName("appHeader")
        header.setFixedHeight(96)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(22, 14, 14, 14)
        layout.setSpacing(18)

        text = QVBoxLayout()
        text.setSpacing(2)
        wordmark = QHBoxLayout()
        wordmark.setSpacing(0)
        bio = QLabel("Bio")
        bio.setObjectName("brandMark")
        wordmark.addWidget(bio)
        flow = QLabel("Flow")
        flow.setObjectName("brandFlow")
        wordmark.addWidget(flow)
        wordmark.addStretch()
        text.addLayout(wordmark)
        tagline = QLabel("METAGENOMIC ANALYSIS WORKBENCH")
        tagline.setObjectName("brandTag")
        text.addWidget(tagline)
        layout.addLayout(text)

        layout.addStretch()
        layout.addWidget(self._make_pipeline_summary())
        return header

    def _make_pipeline_summary(self) -> QWidget:
        """A compact reminder of the workflow this application runs.

        Derived from the stages this release actually offers, never a list of
        its own: a hard-coded strip kept showing "Function" after functional
        profiling was withheld, contradicting the pipeline row on the same
        screen. Repeats collapse, so the two FastQC passes read as one "QC".
        """
        names: list[str] = []
        for stage in default_stages():
            label = stage.short_title
            if label and label not in names:
                names.append(label)
        return WorkflowRibbon(names)

    def _note_background(self, playing: bool) -> None:
        """Record whether the animated background is running, for diagnostics."""
        self.background_playing = playing

    def resizeEvent(self, event):
        """Keep the background covering the whole window at any size."""
        super().resizeEvent(event)
        if getattr(self, "background", None) is not None:
            self.background.setGeometry(self.rect())
            self.background.lower()

    def _make_status_strip(self) -> QWidget:
        """A live, at-a-glance summary of what the backend can currently do."""
        strip = QFrame()
        strip.setObjectName("card")
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(16, 9, 16, 9)
        layout.setSpacing(16)

        self.readiness_badge = StatusBadge("CHECKING", "idle")
        layout.addWidget(self.readiness_badge)
        self.readiness_label = QLabel("Checking installed backends...")
        self.readiness_label.setObjectName("resultLabel")
        layout.addWidget(self.readiness_label, 1)

        self.reference_badge = StatusBadge("GRCh38", "idle")
        layout.addWidget(self.reference_badge)
        return strip

    def refresh_status_strip(self) -> None:
        """Re-read backend state. Cheap: filesystem checks only, no subprocesses."""
        manager = SetupManager()
        components = manager.components()
        environments = [c for c in components if c.kind == "environment"]
        ready = [c for c in environments if c.installed]

        if not manager.micromamba_installed():
            self.readiness_badge.set_state("SETUP NEEDED", "warn")
            self.readiness_label.setText(
                "No analysis backend installed yet — open Setup & Resources to begin."
            )
        elif len(ready) == len(environments):
            self.readiness_badge.set_state("READY", "ok")
            self.readiness_label.setText(
                f"All {len(environments)} analysis environments installed."
            )
        else:
            self.readiness_badge.set_state("PARTIAL", "info")
            self.readiness_label.setText(
                f"{len(ready)} of {len(environments)} analysis environments installed: "
                + ", ".join(c.title.replace(" tools", "") for c in ready)
            )

        reference = [c for c in components if c.key == "db:grch38"][0]
        kind = {"managed": "ok", "external": "info", "development": "warn"}.get(
            reference.state.value, "idle"
        )
        self.reference_badge.set_state(f"GRCh38 · {reference.state.label.upper()}", kind)
        self.reference_badge.setToolTip(reference.describe_state())

    def closeEvent(self, event):
        """Confirm before abandoning a running install, then stop it cleanly."""
        pipeline_page = self.page_by_name["Run workflow"]
        if pipeline_page.is_running:
            answer = QMessageBox.question(
                self,
                "An analysis is still running",
                "BioFlow is still running the workflow. Closing now stops it.\n\n"
                "Completed stages are recorded, so you can resume later from "
                "Run workflow.\n\nClose anyway?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Close,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        setup_page = self.page_by_name["Setup & Resources"]
        if setup_page.is_running:
            answer = QMessageBox.question(
                self,
                "Setup is still running",
                "BioFlow is still installing backends. Closing now stops the "
                "install.\n\nAlready-downloaded packages are kept, so you can "
                "resume later from Setup & Resources.\n\nClose anyway?",
                QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Close,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
        # Every page that can own a running process, not just these two: a tool
        # started from one of the standalone pages would otherwise outlive the
        # window, with its log file still open.
        for page in self.page_by_name.values():
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception as error:  # noqa: BLE001 - closing must not fail
                    print(f"BioFlow: error shutting down {type(page).__name__}: {error}")
        if getattr(self, "background", None) is not None:
            self.background.stop()
        # The sidebar's reveal timer, stopped for the same reason as the video.
        signature = getattr(self.sidebar_panel, "signature", None)
        if signature is not None:
            signature.stop()
        event.accept()

    def show_page(self, item, _column):
        container = self.page_containers.get(item.text(0))
        if container:
            self._fade_to(container)
            page = self.page_by_name[item.text(0)]
            if isinstance(page, HistoryPage):
                page.refresh()
            self.refresh_status_strip()

    def _fade_to(self, container) -> None:
        """Cross-fade to a page. Purely visual; nothing waits on it."""
        self.pages.setCurrentWidget(container)
        effect = QGraphicsOpacityEffect(container)
        container.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(160)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # Drop the effect afterwards so it never costs anything at rest.
        animation.finished.connect(lambda: container.setGraphicsEffect(None))
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
