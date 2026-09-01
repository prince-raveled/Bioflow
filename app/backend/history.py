"""Persistent local run history for BioFlow projects and analysis modules."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from backend.config import get_config


class RunHistory:
    """Store run metadata locally without requiring a server or cloud account."""

    @staticmethod
    def database_path() -> Path:
        """Resolved through the central configuration so it stays relocatable."""
        return get_config().history_database

    @classmethod
    @contextmanager
    def _connection(cls):
        """Yield a connection that is committed *and closed*.

        sqlite3's own context manager commits on exit but leaves the connection
        open, which leaks a handle per run.
        """
        connection = cls._open()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @classmethod
    def _open(cls) -> sqlite3.Connection:
        database = cls.database_path()
        database.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input_summary TEXT,
                output_directory TEXT,
                command TEXT,
                log_path TEXT,
                exit_code INTEGER
            )
            """
        )
        return connection

    @classmethod
    def start_run(
        cls,
        tool_name: str,
        input_summary: str,
        output_directory: str | None,
        command: str,
        log_path: str | None,
    ) -> int:
        with cls._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (started_at, tool_name, status, input_summary, output_directory, command, log_path)
                VALUES (?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    cls._now(), tool_name, input_summary, output_directory, command, log_path,
                ),
            )
            return int(cursor.lastrowid)

    @classmethod
    def finish_run(cls, run_id: int | None, status: str, exit_code: int | None):
        if run_id is None:
            return
        with cls._connection() as connection:
            connection.execute(
                "UPDATE runs SET finished_at = ?, status = ?, exit_code = ? WHERE id = ?",
                (cls._now(), status, exit_code, run_id),
            )

    @classmethod
    def recent_runs(cls, limit: int = 100) -> list[dict]:
        with cls._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
