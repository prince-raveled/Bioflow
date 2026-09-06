"""File dialogs must work where BioFlow runs, not only on a full desktop.

Qt delegates file dialogs to a desktop portal. Where none answers - WSLg, a
minimal window manager, a container - the call can return nothing or hang, and
the user sees a button that appears to do nothing. Copying the application into
WSL produced exactly that: file and folder selection "was trash".
"""

from pathlib import Path
import os
import unittest
from unittest import mock

import support  # noqa: F401  (puts app/ on the path)

try:  # pragma: no cover - exercised by the skip itself
    from PyQt6.QtWidgets import QApplication, QFileDialog
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class DialogBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui import dialogs

        self.dialogs = dialogs
        previous = os.environ.pop("BIOFLOW_NATIVE_DIALOGS", None)
        if previous is not None:
            self.addCleanup(os.environ.__setitem__, "BIOFLOW_NATIVE_DIALOGS", previous)

    def test_wsl_never_uses_the_native_dialog(self):
        with mock.patch.object(self.dialogs, "running_under_wsl", return_value=True):
            self.assertFalse(self.dialogs.use_native_dialogs())

    def test_a_machine_with_no_desktop_session_uses_qts_own_dialog(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_CURRENT_DESKTOP", None)
            os.environ.pop("DESKTOP_SESSION", None)
            with mock.patch.object(self.dialogs, "running_under_wsl", return_value=False):
                self.assertFalse(self.dialogs.use_native_dialogs())

    def test_a_desktop_without_a_file_portal_uses_qts_own_dialog(self):
        # gnome-keyring answers on the bus but draws no file chooser, so the
        # presence of *a* portal is not evidence that a dialog will appear.
        with mock.patch.object(self.dialogs, "running_under_wsl", return_value=False), \
             mock.patch.object(self.dialogs, "_portal_provides_a_file_chooser", return_value=False), \
             mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}):
            self.assertFalse(self.dialogs.use_native_dialogs())

    def test_a_full_desktop_keeps_its_native_dialog(self):
        with mock.patch.object(self.dialogs, "running_under_wsl", return_value=False), \
             mock.patch.object(self.dialogs, "_portal_provides_a_file_chooser", return_value=True), \
             mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": "GNOME"}):
            self.assertTrue(self.dialogs.use_native_dialogs())

    def test_the_user_can_force_the_choice_either_way(self):
        with mock.patch.dict(os.environ, {"BIOFLOW_NATIVE_DIALOGS": "0"}):
            self.assertFalse(self.dialogs.use_native_dialogs())
        with mock.patch.dict(os.environ, {"BIOFLOW_NATIVE_DIALOGS": "1"}), \
             mock.patch.object(self.dialogs, "running_under_wsl", return_value=True):
            self.assertTrue(
                self.dialogs.use_native_dialogs(),
                "an override must win even where detection says otherwise",
            )

    def test_the_non_native_option_is_actually_set(self):
        with mock.patch.object(self.dialogs, "use_native_dialogs", return_value=False):
            self.assertEqual(
                self.dialogs._options(), QFileDialog.Option.DontUseNativeDialog
            )

    def test_folder_selection_asks_for_folders(self):
        # Without ShowDirsOnly the non-native chooser lists files that cannot be
        # picked, which is what made folder selection feel broken.
        with mock.patch.object(self.dialogs, "use_native_dialogs", return_value=False):
            with mock.patch.object(QFileDialog, "getExistingDirectory", return_value="") as call:
                self.dialogs.existing_directory(None, "Pick")
            options = call.call_args[0][3]
            self.assertTrue(options & QFileDialog.Option.ShowDirsOnly)
            self.assertTrue(options & QFileDialog.Option.DontUseNativeDialog)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class EveryDialogGoesThroughTheHelperTests(unittest.TestCase):
    """One decision point, so a new page cannot reintroduce the problem."""

    def test_no_page_calls_qfiledialog_directly(self):
        pages = Path(__file__).resolve().parent.parent / "app" / "gui" / "pages"
        offenders = [
            path.name
            for path in pages.glob("*.py")
            if "QFileDialog." in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(
            offenders, [], "these pages bypass the dialog backend selection"
        )
