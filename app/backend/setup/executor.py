"""Run a setup plan step by step, streaming output and honouring cancellation."""

from collections.abc import Callable
import codecs
import fcntl
import os
import pty
import re
import select
import struct
import subprocess
import termios
import threading
import time

from backend.setup.plan import ActionStep, CommandStep, SetupPlan, SetupStep
from backend.setup.progress import (
    Phase,
    ProgressUpdate,
    condense,
    detect_phase,
    parse_progress,
)


StepCallback = Callable[[SetupStep, int, int], None]
OutputCallback = Callable[[str], None]
ProgressCallback = Callable[[ProgressUpdate], None]

#: Progress indicators redraw constantly; forward one at most this often.
TRANSIENT_INTERVAL_SECONDS = 0.5
#: Unpacking a large archive, or building an index, prints nothing for
#: minutes. Say so, rather than leaving the user looking at a window that
#: appears to have hung.
SILENCE_NOTICE_SECONDS = 30.0
SILENCE_REPEAT_SECONDS = 60.0
#: Keep progress bars narrow enough to read in the log panel.
TERMINAL_COLUMNS = 100
MAXIMUM_LINE_LENGTH = 300

#: Unterminated output is flushed once it passes this, so a tool that never ends
#: a line still shows progress and cannot grow the buffer without limit.
MAXIMUM_BUFFERED_CHARACTERS = 16384

_LINE_BREAK = re.compile(r"(\r\n|\r|\n)")
_ANSI = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")


def strip_terminal_codes(text: str) -> str:
    """Remove the colour and cursor escapes a pseudo-terminal makes tools emit."""
    return _ANSI.sub("", text).replace("\x08", "")


class PlanExecutor:
    """Execute a plan in the calling thread so a GUI can host it in a worker."""

    def __init__(
        self,
        plan: SetupPlan,
        on_step: StepCallback | None = None,
        on_output: OutputCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ):
        self.plan = plan
        self._on_step = on_step
        self._on_output = on_output
        self._on_progress = on_progress
        self._cancelled = threading.Event()
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()
        self._last_transient = 0.0
        self._phase: Phase | None = None
        self._last_output = 0.0
        self._last_notice = 0.0
        self._saw_output = False

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """Ask the plan to stop after terminating any running command."""
        self._cancelled.set()
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # ------------------------------------------------------------------
    def run(self) -> tuple[bool, str]:
        """Run every step in order. Returns (succeeded, closing message)."""
        total = len(self.plan)
        for index, step in enumerate(self.plan.steps, start=1):
            if self._cancelled.is_set():
                return False, "Setup cancelled before it finished."
            if self._on_step:
                self._on_step(step, index, total)
            try:
                self._run_step(step)
            except _StepCancelled:
                return False, "Setup cancelled."
            except Exception as error:  # noqa: BLE001 - surfaced to the user verbatim
                self._log(f"ERROR: {error}")
                return False, (
                    f"Step failed: {step.title}. Downloaded packages are cached, so "
                    f"pressing Install again resumes rather than starting over."
                )
        return True, f"Setup finished: {', '.join(self.plan.components) or 'nothing to do'}."

    # ------------------------------------------------------------------
    def _run_step(self, step: SetupStep) -> None:
        if isinstance(step, ActionStep):
            step.action(self._log)
            return

        attempts = step.retries + 1
        for attempt in range(1, attempts + 1):
            try:
                self._run_command(step)
                return
            except _StepCancelled:
                raise
            except RuntimeError as error:
                if attempt == attempts:
                    raise
                self._log(
                    f"Attempt {attempt} of {attempts} failed ({error}). Retrying; "
                    f"already-downloaded packages are reused."
                )

    def _run_command(self, step: CommandStep) -> None:
        # Each step reports its own phases from scratch, so a second download
        # is announced rather than swallowed by the previous step's state.
        self._phase = None
        self._saw_output = False
        self._log(f"$ {step.described_command()}")
        # A pseudo-terminal keeps package managers in interactive mode, so their
        # download progress reaches the log instead of arriving only at the end.
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, TERMINAL_COLUMNS, 0, 0))
        try:
            process = subprocess.Popen(
                [step.program, *step.arguments],
                cwd=str(step.working_directory) if step.working_directory else None,
                env={**os.environ, **step.environment} if step.environment else None,
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=slave,
                close_fds=True,
            )
        finally:
            os.close(slave)

        with self._process_lock:
            self._process = process
        try:
            self._stream(master, process)
        finally:
            os.close(master)
            exit_code = process.wait()
            with self._process_lock:
                self._process = None

        if self._cancelled.is_set():
            raise _StepCancelled
        if exit_code != 0:
            if step.tolerate_failure:
                self._log(f"Continuing after a non-fatal failure (exit code {exit_code}).")
                return
            raise RuntimeError(f"{step.program} exited with code {exit_code}")

    def _stream(self, master: int, process: subprocess.Popen) -> None:
        """Forward the child's output, splitting on newlines and carriage returns.

        Decoding is incremental. A read returns whatever bytes are available,
        which can end in the middle of a multi-byte character - and the bar
        glyphs package managers draw progress with are three bytes each - so
        decoding every chunk on its own turned a straddled character into a
        replacement mark and left the log visibly damaged.
        """
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._last_output = time.monotonic()
        self._last_notice = self._last_output
        while True:
            readable, _, _ = select.select([master], [], [], 0.4)
            self._report_silence()
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break  # The child closed the terminal; it has exited.
                if not data:
                    break
                buffer += decoder.decode(data)
                parts = _LINE_BREAK.split(buffer)
                buffer = parts.pop()
                while len(parts) >= 2:
                    text, delimiter = parts.pop(0), parts.pop(0)
                    self._emit(text, transient=delimiter == "\r")
                if len(buffer) > MAXIMUM_BUFFERED_CHARACTERS:
                    # A tool that writes without ever ending a line would
                    # otherwise grow this without limit and show nothing.
                    self._emit(buffer, transient=False)
                    buffer = ""
            elif process.poll() is not None:
                break
        buffer += decoder.decode(b"", final=True)
        if buffer.strip():
            self._emit(buffer, transient=False)

    def _emit(self, text: str, transient: bool) -> None:
        """Route a line of output to the log or to the progress indicator."""
        message = strip_terminal_codes(text).rstrip()
        if not message:
            return
        if len(message) > MAXIMUM_LINE_LENGTH:
            message = message[:MAXIMUM_LINE_LENGTH] + "..."
        self._last_output = time.monotonic()

        self._saw_output = True
        update = parse_progress(message)
        if update is None and not transient:
            self._announce_phase(message)
            self._log(message)
            return

        # Progress redraws in the thousands for a large download. Send them to
        # a dedicated indicator when one exists so the log keeps only the lines
        # that say what happened; otherwise thin them out into the log.
        now = time.monotonic()
        if now - self._last_transient < TRANSIENT_INTERVAL_SECONDS:
            return
        self._last_transient = now
        self._last_notice = now
        if self._on_progress and update is not None:
            self._on_progress(update)
            return
        # No indicator to route to, so this update has to share the log. Send
        # the reading rather than the drawing of it.
        self._log(condense(message) if update is not None else message)

    def _announce_phase(self, message: str) -> None:
        """Name each new phase once, so the sequence of work is visible."""
        phase = detect_phase(message)
        if phase is not None and phase is not self._phase:
            self._phase = phase
            self._log(f"-- {phase.value} --")

    def _report_silence(self) -> None:
        """Say that a quiet tool is still working, and for how long.

        Reported for any stall once a command has spoken at all: unpacking and
        index building are both entirely silent, and either can run for long
        enough that a user reasonably concludes the install has died.
        """
        if not self._saw_output:
            return
        now = time.monotonic()
        if now - self._last_output < SILENCE_NOTICE_SECONDS:
            return
        if now - self._last_notice < SILENCE_REPEAT_SECONDS:
            return
        self._last_notice = now
        waiting = int(now - self._last_output)
        self._log(
            f"Still working after {waiting // 60}m {waiting % 60:02d}s with no output. "
            f"Unpacking and index building are silent; leave it running."
        )

    def _log(self, message: str) -> None:
        if message and self._on_output:
            self._on_output(message)


class _StepCancelled(Exception):
    """Raised internally when a running command is stopped by the user."""
