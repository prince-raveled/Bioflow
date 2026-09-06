"""The author's contact details, carried as sequence rather than as a label.

The point of encoding them is that the strand on screen is the real thing: if
decoding did not return the address exactly, the sequence would be decoration
pretending to be data. These tests are what keep that claim true.
"""

import unittest

import support  # noqa: F401  (puts app/ on the path)

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

from gui import author  # noqa: E402


class EncodingTests(unittest.TestCase):
    def test_a_strand_decodes_to_exactly_what_was_encoded(self):
        for text in ("a", "BioFlow", author.AUTHOR_EMAIL, author.AUTHOR_LINKEDIN):
            with self.subTest(text=text):
                self.assertEqual(author.decode(author.encode(text)), text)

    def test_four_bases_carry_one_character(self):
        self.assertEqual(len(author.encode("ACGT")), 16)
        self.assertEqual(len(author.encode("")), 0)

    def test_only_the_four_bases_are_produced(self):
        strand = author.contact_sequence()
        self.assertEqual(set(strand) - set(author.BASES), set())

    def test_non_ascii_survives_the_round_trip(self):
        # Encoding works on bytes, so a multi-byte character must come back whole.
        self.assertEqual(author.decode(author.encode("Kumar — ünïcode")), "Kumar — ünïcode")

    def test_a_truncated_strand_is_refused(self):
        with self.assertRaises(ValueError):
            author.decode("ACG")

    def test_something_that_is_not_a_sequence_is_refused(self):
        with self.assertRaises(ValueError):
            author.decode("ACGX")

    def test_whitespace_between_lines_is_tolerated(self):
        strand = author.contact_sequence()
        self.assertEqual(
            author.decode("\n".join(author.wrapped(strand))), author.decode(strand)
        )


class ContactDetailTests(unittest.TestCase):
    def test_the_strand_carries_both_the_email_and_the_profile(self):
        email, profile = author.decode_contact(author.contact_sequence())
        self.assertEqual(email, author.AUTHOR_EMAIL)
        self.assertEqual(profile, author.AUTHOR_LINKEDIN)

    def test_the_profile_link_carries_no_tracking_parameters(self):
        # The shared link identifies how it was sent; the profile does not.
        self.assertNotIn("utm_", author.AUTHOR_LINKEDIN)
        self.assertNotIn("?", author.AUTHOR_LINKEDIN)
        self.assertTrue(author.AUTHOR_LINKEDIN.startswith("https://www.linkedin.com/in/"))

    def test_the_email_is_written_once(self):
        from pathlib import Path

        app = Path(__file__).resolve().parent.parent / "app"
        holders = [
            str(path.relative_to(app))
            for path in app.rglob("*.py")
            if author.AUTHOR_EMAIL in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(holders, ["gui/author.py"], "the address is written twice")


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class SignatureWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def _widget(self):
        from gui.widgets.author_signature import AuthorSignature

        widget = AuthorSignature()
        self.addCleanup(widget.stop)
        return widget

    def _sequence_fully(self, widget):
        widget.expand()
        for _ in range(200):
            widget._read_further()

    def test_it_starts_collapsed(self):
        widget = self._widget()
        self.assertIn("sequence the author", widget.prompt.text())
        self.assertFalse(widget.strand.isVisible())

    def test_sequencing_reveals_working_links(self):
        widget = self._widget()
        self._sequence_fully(widget)
        markup = widget.contact.text()
        self.assertIn(f"mailto:{author.AUTHOR_EMAIL}", markup)
        self.assertIn(author.AUTHOR_LINKEDIN, markup)

    def test_the_reveal_stops_once_the_strand_is_read(self):
        widget = self._widget()
        self._sequence_fully(widget)
        self.assertFalse(widget._timer.isActive(), "the timer runs on after the read")

    def test_it_can_be_closed_again(self):
        widget = self._widget()
        self._sequence_fully(widget)
        widget.toggle()
        self.assertIn("sequence the author", widget.prompt.text())
        self.assertFalse(widget._timer.isActive())

    def test_it_reaches_the_sidebar(self):
        from gui.widgets.sidebar import SidebarPanel

        # Held on the instance: letting it fall out of scope destroys the Qt
        # object before the cleanup that stops its timer can run.
        self.panel = SidebarPanel()
        self.addCleanup(lambda: self.panel.signature.stop())
        self.assertIsNotNone(self.panel.signature)
        self.assertIn("sequence the author", self.panel.signature.prompt.text())
