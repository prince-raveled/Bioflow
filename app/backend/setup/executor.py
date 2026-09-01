"""Run a setup plan step by step, streaming output and honouring cancellation."""

from collections.abc import Callable
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


StepCallback = Callable[[SetupStep, int, int], None]
OutputCallback = Callable[[str], None]

#: Progress bars redraw with a carriage return; show at most this often.
TRANSIENT_INTERVAL_SECONDS = 0.5
#: Keep progress bars narrow enough to read in the log panel.
TERMINAL_COLUMNS = 100
MAXIMUM_LINE_LENGTH = 300

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
    ):
        self.plan = plan
        self._on_step = on_step
        self._on_output = on_output
        self._cancelled = threading.Event()
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()
        self._last_transient = 0.0

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
        self._log(f"$ {step.described_command()}")
        # A pseudo-terminal keeps package managers in interactive mode, so their
        # download progress reaches the log instead of arriving only at the end.
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, TERMINAL_COLUMNS, 0, 0))
        try:
            process = subprocess.Popen(
                [step.program, *step.arguments],
                cwd=str(step.working_directory) if step.working_directory else None,
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
        """Forward the child's output, splitting on newlines and carriage returns."""
        buffer = ""
        while True:
            readable, _, _ = select.select([master], [], [], 0.4)
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break  # The child closed the terminal; it has exited.
                if not data:
                    break
                buffer += data.decode("utf-8", errors="replace")
                parts = _LINE_BREAK.split(buffer)
                buffer = parts.pop()
                while len(parts) >= 2:
                    text, delimiter = parts.pop(0), parts.pop(0)
                    self._emit(text, transient=delimiter == "\r")
            elif process.poll() is not None:
                break
        if buffer.strip():
            self._emit(buffer, transient=False)

    def _emit(self, text: str, transient: bool) -> None:
        """Log a line, thinning out redrawn progress bars so they do not flood."""
        message = strip_terminal_codes(text).rstrip()
        if not message:
            return
        if len(message) > MAXIMUM_LINE_LENGTH:
            message = message[:MAXIMUM_LINE_LENGTH] + "..."
        if transient:
            now = time.monotonic()
            if now - self._last_transient < TRANSIENT_INTERVAL_SECONDS:
                return
            self._last_transient = now
        self._log(message)

    def _log(self, message: str) -> None:
        if message and self._on_output:
            self._on_output(message)


class _StepCancelled(Exception):
    """Raised internally when a running command is stopped by the user."""
