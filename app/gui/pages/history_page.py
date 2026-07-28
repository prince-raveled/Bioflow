"""GUI for reviewing completed, failed, and running BioFlow analyses."""

from pathlib import Path

from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from backend.history import RunHistory


class HistoryPage(QWidget):
    """Show locally persisted execution records from every BioFlow module."""

    def __init__(self):
        super().__init__()
        self.setObjectName("toolPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(14)

        eyebrow = QLabel("Workspace")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        title = QLabel("Run history")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            "Every analysis is saved locally with its status, output folder, command, and log file."
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        actions = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        actions.addWidget(refresh)
        self.open_output = QPushButton("Open output")
        self.open_output.clicked.connect(self._open_selected_output)
        actions.addWidget(self.open_output)
        self.open_log = QPushButton("Open log")
        self.open_log.clicked.connect(self._open_selected_log)
        actions.addWidget(self.open_log)
        actions.addStretch()
        layout.addLayout(actions)

        self.table = QTreeWidget()
        self.table.setObjectName("historyTable")
        self.table.setHeaderLabels(["When", "Module", "Status", "Input", "Output"])
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 240)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self):
        self.table.clear()
        for run in RunHistory.recent_runs():
            item = QTreeWidgetItem([
                run["started_at"],
                run["tool_name"],
                run["status"].title(),
                run["input_summary"] or "—",
                run["output_directory"] or "—",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, run)
            self.table.addTopLevelItem(item)
        self._update_action_state()

    def _selected_run(self) -> dict | None:
        items = self.table.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _update_action_state(self):
        run = self._selected_run()
        self.open_output.setEnabled(bool(run and run.get("output_directory")))
        self.open_log.setEnabled(bool(run and run.get("log_path")))

    def _open_selected_output(self):
        run = self._selected_run()
        if run and run.get("output_directory"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(run["output_directory"]))))

    def _open_selected_log(self):
        run = self._selected_run()
        if run and run.get("log_path"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(run["log_path"]))))
