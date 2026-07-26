from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QTextEdit,
)


class ResultPanel(QWidget):
    """
    Panel for displaying analysis results.
    """

    def __init__(self):
        super().__init__()

        self.build_ui()

    def build_ui(self):
        """
        Build the user interface.
        """

        layout = QVBoxLayout()

        title = QLabel("Analysis Results")
        layout.addWidget(title)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text)

        self.setLayout(layout)

    def add_result(self, text):
        """
        Add text to the result panel.
        """

        self.result_text.append(text)

    def clear_results(self):
        """
        Clear all results from the panel.
        """

        self.result_text.clear()
