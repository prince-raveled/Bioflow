from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTextEdit,
)


class LogPanel(QWidget):
    """
    Panel for displaying logs and status messages.
    """

    def __init__(self):
        super().__init__()

        self.build_ui()

    def build_ui(self):
        """
        Build the user interface.
        """

        layout = QVBoxLayout()

        title = QLabel("Logs")
        layout.addWidget(title)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

        self.setLayout(layout)

    def add_log(self, message):
        """
        Add a log message to the panel.
        """

        self.log_text.append(message)

    def clear_logs(self):
        """
        Clear all logs from the panel.
        """

        self.log_text.clear()
