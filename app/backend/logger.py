"""Append-only log files for long-running BioFlow sessions."""

from datetime import datetime
from pathlib import Path


class SessionLog:
    """Mirror a run's on-screen log to a timestamped file on disk."""

    def __init__(self, directory: Path, prefix: str):
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_prefix = "".join(
            character.lower() if character.isalnum() else "_" for character in prefix
        ).strip("_") or "bioflow"
        self.path = directory / f"{timestamp}_{safe_prefix}.log"
        self._handle = self.path.open("w", encoding="utf-8")

    def write(self, message: str) -> None:
        if self._handle and not self._handle.closed:
            self._handle.write(f"{message}\n")
            self._handle.flush()

    def close(self) -> None:
        if self._handle and not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "SessionLog":
        return self

    def __exit__(self, *_exception) -> None:
        self.close()
