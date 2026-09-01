"""Execute one stage command and capture everything about it."""

from collections.abc import Callable
from pathlib import Path
import selectors
import subprocess
import threading

from backend.execution.environment import EnvironmentResolver, MissingBackend
from backend.execution.record import CommandRecord, now
from backend.execution.stage import StageCommand


LogCallback = Callable[[str], None]
TAIL_CHARACTERS = 4000


class CommandCancelled(Exception):
    """Raised when the user stops a running analysis."""


class CommandRunner:
    """Run a stage's command inside BioFlow's managed environment.

    stdout and stderr are captured to separate files so a stage's evidence
    survives the session, and both are streamed to the interface as they arrive.
    """

    def __init__(
        self,
        resolver: EnvironmentResolver | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.resolver = resolver or EnvironmentResolver()
        self.cancel_event = cancel_event or threading.Event()
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        self.cancel_event.set()
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    # ------------------------------------------------------------------
    def run(
        self,
        stage_command: StageCommand,
        stdout_path: Path,
        stderr_path: Path,
        log: LogCallback,
    ) -> CommandRecord:
        """Execute the command, returning a full record of what happened."""
        stdout_target = stage_command.stdout_to or stdout_path
        stderr_target = stage_command.stderr_to or stderr_path
        for path in (stdout_target, stderr_target):
            path.parent.mkdir(parents=True, exist_ok=True)

        try:
            program, arguments = self.resolver.resolve(
                stage_command.environment_key, list(stage_command.command)
            )
        except MissingBackend as error:
            record = CommandRecord(
                description=stage_command.description,
                program=stage_command.command[0],
                arguments=list(stage_command.command[1:]),
                environment=stage_command.environment_key,
                started_at=now(),
                finished_at=now(),
                exit_code=None,
                stderr_tail=str(error),
            )
            log(f"ERROR: {error}")
            return record

        record = CommandRecord(
            description=stage_command.description,
            program=program,
            arguments=arguments,
            environment=self.resolver.config.environment_name(stage_command.environment_key),
            started_at=now(),
            stdout_path=str(stdout_target),
            stderr_path=str(stderr_target),
        )
        log(f"$ {record.command_line}")

        if self.cancel_event.is_set():
            raise CommandCancelled

        stderr_tail: list[str] = []
        with stdout_target.open("w", encoding="utf-8") as out_file, \
                stderr_target.open("w", encoding="utf-8") as err_file:
            process = subprocess.Popen(
                [program, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            with self._lock:
                self._process = process
            try:
                self._pump(process, out_file, err_file, stderr_tail, log)
            finally:
                record.exit_code = process.wait()
                # A long pipeline runs many commands; leaked pipes accumulate.
                for stream in (process.stdout, process.stderr):
                    if stream and not stream.closed:
                        stream.close()
                with self._lock:
                    self._process = None

        record.finished_at = now()
        record.stderr_tail = "\n".join(stderr_tail)[-TAIL_CHARACTERS:]
        if self.cancel_event.is_set():
            raise CommandCancelled
        if not record.succeeded:
            log(f"Command failed with exit code {record.exit_code}.")
        return record

    # ------------------------------------------------------------------
    def _pump(self, process, out_file, err_file, stderr_tail: list[str], log: LogCallback) -> None:
        """Forward both streams without letting either pipe fill up and block."""
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, ("stdout", out_file))
        selector.register(process.stderr, selectors.EVENT_READ, ("stderr", err_file))
        open_streams = 2
        try:
            while open_streams:
                for key, _mask in selector.select(timeout=0.3):
                    stream_name, target = key.data
                    line = key.fileobj.readline()
                    if not line:
                        selector.unregister(key.fileobj)
                        open_streams -= 1
                        continue
                    target.write(line)
                    target.flush()
                    message = line.rstrip()
                    if not message:
                        continue
                    if stream_name == "stderr":
                        stderr_tail.append(message)
                        if len(stderr_tail) > 200:
                            del stderr_tail[:100]
                    log(message)
                if self.cancel_event.is_set():
                    process.terminate()
                    break
        finally:
            selector.close()
