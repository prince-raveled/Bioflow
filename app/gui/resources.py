"""Locating BioFlow's bundled GUI assets.

Assets resolve relative to this module, never from the working directory and
never from an absolute path, so the application behaves identically from a
source checkout, an installed copy under /opt, or a one-file bundle. Nothing
here is downloaded at run time.
"""

from pathlib import Path
import sys


#: Video container formats the background will accept, in preference order.
VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")

#: The application package root: <repo>/app, wherever that happens to live.
APP_ROOT = Path(__file__).resolve().parent.parent

#: Directories searched for the background clip, in order. Repository-relative
#: by construction, so a clone at any location resolves the same asset.
VIDEO_DIRECTORIES = ("Video", "gui/assets/video", "assets/video")


def _bundle_root() -> Path | None:
    """Application root when running from a PyInstaller-style one-file bundle."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def _search_roots() -> list[Path]:
    roots = []
    bundle = _bundle_root()
    if bundle is not None:
        roots.append(bundle)
    roots.append(APP_ROOT)
    return roots


def background_video_path() -> Path | None:
    """The bundled background clip, discovered by extension rather than by name.

    The filename is not hard-coded: the first video file found in the first
    populated asset directory wins, so replacing the clip is a drop-in change.
    Returns None when no clip ships with this installation, which callers must
    treat as normal.
    """
    for root in _search_roots():
        for directory in VIDEO_DIRECTORIES:
            folder = root / directory
            if not folder.is_dir():
                continue
            candidates = sorted(
                path
                for path in folder.iterdir()
                if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES
            )
            if candidates:
                return candidates[0]
    return None

