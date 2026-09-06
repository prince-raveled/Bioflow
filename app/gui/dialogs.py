"""File selection that works on every desktop BioFlow runs on.

Qt asks a desktop portal to draw file dialogs, and delegates to GTK or KDE when
one answers. Where no portal answers - WSL's WSLg, a minimal window manager, a
container with no desktop installed - the request can return nothing or hang,
which reads to the user as a broken button rather than a missing dependency.

Qt's own dialog needs nothing beyond Qt, so it is used wherever the portal
cannot be relied on. Every file dialog in the application goes through here, so
the decision is made once rather than at ten call sites.
"""

from __future__ import annotations

from pathlib import Path
import os

from PyQt6.QtWidgets import QFileDialog

#: Portal backends that can actually draw a file chooser. gnome-keyring, for
#: instance, answers on the bus without providing one.
_FILE_CHOOSER_PORTALS = ("gtk", "gnome", "kde", "lxqt", "xapp", "wlr")

_PORTAL_DIRECTORIES = (
    "/usr/share/xdg-desktop-portal/portals",
    "/usr/local/share/xdg-desktop-portal/portals",
)


def running_under_wsl() -> bool:
    """True on the Windows Subsystem for Linux."""
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _portal_provides_a_file_chooser() -> bool:
    for directory in _PORTAL_DIRECTORIES:
        folder = Path(directory)
        if not folder.is_dir():
            continue
        for entry in folder.iterdir():
            if entry.stem.lower() in _FILE_CHOOSER_PORTALS:
                return True
    return False


def use_native_dialogs() -> bool:
    """Whether the desktop can be trusted to draw the file dialog.

    BIOFLOW_NATIVE_DIALOGS forces the answer either way, so a user whose desktop
    is misdetected is never stuck with the wrong one.
    """
    override = os.environ.get("BIOFLOW_NATIVE_DIALOGS")
    if override is not None:
        return override not in ("", "0", "false")
    if running_under_wsl():
        # WSLg routes portal calls through a compatibility layer that frequently
        # has no file chooser behind it.
        return False
    if not os.environ.get("XDG_CURRENT_DESKTOP") and not os.environ.get("DESKTOP_SESSION"):
        return False
    return _portal_provides_a_file_chooser()


def _options() -> QFileDialog.Option:
    if use_native_dialogs():
        return QFileDialog.Option(0)
    return QFileDialog.Option.DontUseNativeDialog


def open_files(parent, caption: str, file_filter: str = "", directory: str = "") -> list[str]:
    """Ask for one or more existing files."""
    names, _ = QFileDialog.getOpenFileNames(
        parent, caption, directory, file_filter, options=_options()
    )
    return names


def open_file(parent, caption: str, file_filter: str = "", directory: str = "") -> str:
    """Ask for a single existing file. Returns "" when cancelled."""
    name, _ = QFileDialog.getOpenFileName(
        parent, caption, directory, file_filter, options=_options()
    )
    return name


def existing_directory(parent, caption: str, directory: str = "") -> str:
    """Ask for an existing directory. Returns "" when cancelled."""
    return QFileDialog.getExistingDirectory(
        parent,
        caption,
        directory,
        # ShowDirsOnly is what makes the non-native dialog usable as a folder
        # picker; without it the list is dominated by files that cannot be chosen.
        QFileDialog.Option.ShowDirsOnly | _options(),
    )
