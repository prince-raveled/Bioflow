"""The author's contact details, kept in the alphabet this application reads.

A metagenomics workbench that spends its life decoding sequence can introduce
the person who wrote it the same way. The strand shown here is not decoration:
it is the email address and profile link packed two bits to a base, and pressing
it runs the decode for real and shows what came back.
"""

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from gui import author, theme


class AuthorSignature(QFrame):
    """A collapsed FASTA record that decodes itself when pressed."""

    #: How many bases arrive per tick while the strand is being read.
    BASES_PER_TICK = 14
    TICK_MILLISECONDS = 26

    decoded = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("authorSignature")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            "The contact details of BioFlow's author, packed two bits per base. "
            "Press to sequence it."
        )

        self._sequence = author.contact_sequence()
        self._revealed = 0
        self._open = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 7)
        layout.setSpacing(3)

        self.prompt = QLabel("⌁  sequence the author")
        # Wraps rather than clipping, so a narrower sidebar cannot cut the
        # record's own name in half.
        self.prompt.setWordWrap(True)
        # Cream, not sage: the sidebar ground is itself sage, and the theme's
        # sage tones are meant for the cream pages. Cream on sage is 4.58:1,
        # which is the ratio the navigation labels beside this already clear.
        self.prompt.setStyleSheet(
            f"background: transparent; color: {theme.CREAM_TEXT};"
            f" font-size: 10px; font-weight: bold; letter-spacing: 1px;"
        )
        layout.addWidget(self.prompt)

        self.strand = QLabel("")
        self.strand.setWordWrap(True)
        self.strand.setFont(QFont("monospace", 7))
        # Quieter than the decoded text: the strand is there to be recognised
        # as sequence, not read base by base.
        self.strand.setStyleSheet(
            "background: transparent; color: rgba(255, 249, 238, 0.72);"
            " font-size: 8px; letter-spacing: 0.5px;"
        )
        self.strand.setVisible(False)
        layout.addWidget(self.strand)

        self.contact = QLabel("")
        self.contact.setOpenExternalLinks(False)
        self.contact.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.contact.linkActivated.connect(self._open_link)
        self.contact.setWordWrap(True)
        self.contact.setStyleSheet(
            f"background: transparent; color: {theme.CREAM_TEXT}; font-size: 10px;"
        )
        self.contact.setVisible(False)
        layout.addWidget(self.contact)

        self._timer = QTimer(self)
        self._timer.setInterval(self.TICK_MILLISECONDS)
        self._timer.timeout.connect(self._read_further)

        self.setStyleSheet(
            f"QFrame#authorSignature {{ background: transparent;"
            f" border-top: 1px solid rgba(255, 249, 238, 0.22); }}"
        )

    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        self.toggle()
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle(self) -> None:
        """Open the record and read it, or close it again."""
        if self._open:
            self.collapse()
        else:
            self.expand()

    def expand(self) -> None:
        self._open = True
        self._revealed = 0
        self.prompt.setText(author.FASTA_HEADER)
        self.strand.setVisible(True)
        self.strand.setText("")
        self.contact.setVisible(False)
        self._timer.start()

    def collapse(self) -> None:
        self._timer.stop()
        self._open = False
        self.prompt.setText("⌁  sequence the author")
        self.strand.setVisible(False)
        self.contact.setVisible(False)

    # ------------------------------------------------------------------
    def _read_further(self) -> None:
        """Reveal the strand a few bases at a time, the way a read arrives."""
        self._revealed = min(len(self._sequence), self._revealed + self.BASES_PER_TICK)
        self.strand.setText(self._sequence[: self._revealed])
        if self._revealed >= len(self._sequence):
            self._timer.stop()
            self._show_contact()

    def _show_contact(self) -> None:
        """Decode the strand that was just read and show what it held."""
        email, profile = author.decode_contact(self._sequence)
        # Only the first two lines of the strand are kept on screen: the point
        # has been made, and the sidebar is narrow.
        head = author.wrapped(self._sequence, width=30)[0]
        self.strand.setText(head + " …")
        self.contact.setText(
            f'<a style="color:{theme.CREAM_TEXT};text-decoration:underline;"'
f' href="mailto:{email}">{email}</a>'
            f'<br><a style="color:{theme.CREAM_TEXT};text-decoration:underline;"'
f' href="{profile}">LinkedIn  ↗</a>'
        )
        self.contact.setVisible(True)
        self.decoded.emit()

    @staticmethod
    def _open_link(target: str) -> None:
        QDesktopServices.openUrl(QUrl(target))

    def stop(self) -> None:
        """Stop the reveal timer, for window shutdown."""
        self._timer.stop()
