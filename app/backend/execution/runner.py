"""Execute one stage command and capture everything about it."""

from collections.abc import Callable
from pathlib import Path
import codecs
import os
import re
import selectors
import signal
import subprocess
import threading

from backend.execution.environment import EnvironmentResolver, MissingBackend
from backend.execution.record import CommandRecord, now
from backend.execution.stage import StageCommand


LogCallback = Callable[[str], None]
TAIL_CHARACTERS = 4000

#: Lines tools emit that look like failures but are not, and which alarm a
#: reader who is not a command-line user. Bowtie2 prints its dispatch notice on
#: every invocation, even on CPUs that support the wider instruction set; it
#: falls back to the baseline build, so results are identical.
HARMLESS_TOOL_NOISE = (
    "[WARNING] Failed to launch x86-64-v3 version, staying with default",
)



def describe_exit(exit_code: int | None) -> str:
    """Explain a failing exit status in terms a user can act on.

    A process killed by a signal reports a negative status and prints nothing,
    so the bare number is the only evidence left. That is most acute for an
    out-of-memory kill: the aligner vanishes mid-run with no error of its own,
    which reads as an unexplained crash rather than a machine that is too small.
    """
    if exit_code is None:
        return "Command failed before an exit status was available."
    if exit_code >= 0:
        return f"Command failed with exit code {exit_code}."
    try:
        name = signal.Signals(-exit_code).name
    except ValueError:
        name = f"signal {-exit_code}"
    if -exit_code == signal.SIGKILL:
        return (
            f"Command was killed ({name}). This is almost always the kernel "
            f"reclaiming memory: alignment against a large index needs several "
            f"gigabytes of free RAM. Close other applications and try again, or "
            f"run it on a machine with more memory."
        )
    return f"Command was killed by {name}."

def is_harmless_tool_noise(line: str) -> bool:
    """True for known-benign tool chatter that should not reach the run log.

    Filtered from what the user is shown only. The full text still reaches the
    stdout/stderr files on disk, so nothing is lost for diagnosis.
    """
    return line.strip() in HARMLESS_TOOL_NOISE



#: Read size for a tool's output. Large enough that a chatty aligner is not
#: read a syscall at a time, small enough to reach the interface promptly.
READ_CHUNK_BYTES = 65536

#: A single run of bytes with no terminator is flushed to the log once it grows
#: past this, so a tool that never emits one still produces visible output and
#: cannot grow the buffer without limit.
MAXIMUM_BUFFERED_CHARACTERS = 16384

#: Both terminators. A carriage return is how a tool redraws a progress line,
#: and it delimits a message just as a newline does.
_LINE_BREAK = re.compile(r"[\r\n]")


class _StreamReader:
    """Decodes one output stream and cuts it into messages.

    Decoding is incremental because a chunk boundary can fall in the middle of a
    multi-byte character; decoding each chunk independently would corrupt it.
    """

    def __init__(self, name: str, target):
        self.name = name
        self._target = target
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""

    def feed(self, chunk: bytes) -> list[str]:
        text = self._decoder.decode(chunk)
        if not text:
            return []
        # Every byte reaches the capture file, terminators and all, so the file
        # on disk stays a faithful copy of what the tool wrote.
        self._target.write(text)
        self._target.flush()
        self._buffer += text
        pieces = _LINE_BREAK.split(self._buffer)
        self._buffer = pieces.pop()
        messages = [piece.rstrip() for piece in pieces]
        if len(self._buffer) > MAXIMUM_BUFFERED_CHARACTERS:
            messages.append(self._buffer.rstrip())
            self._buffer = ""
        return [message for message in messages if message]

    def finish(self) -> list[str]:
        """Flush what is left once the stream closes."""
        text = self._decoder.decode(b"", final=True)
        if text:
            self._target.write(text)
            self._target.flush()
            self._buffer += text
        remaining = self._buffer.strip()
        self._buffer = ""
        return [remaining] if remaining else []


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
        # A container backend runs the tool inside a container, and stopping the
        # client that started it is not always enough: a client killed outright
        # leaves the container running with nothing attached to it. Resolvers
        # that can strand a container that way expose this and clean up after
        # themselves. The native resolver has nothing to clean, and does not.
        cleanup = getattr(self.resolver, "cleanup_containers", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:  # noqa: BLE001 - cancelling must never raise
                pass

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
                # Binary pipes, decoded here. Text mode would mean readline(),
                # which blocks until a line terminator arrives; see _pump.
                bufsize=0,
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
            log(describe_exit(record.exit_code))
        return record

    # ------------------------------------------------------------------
    def _pump(self, process, out_file, err_file, stderr_tail: list[str], log: LogCallback) -> None:
        """Forward both streams without letting either pipe fill up and block.

        Reads fixed-size chunks rather than lines. Reading a line means blocking
        until a line terminator arrives, and a tool that writes a large amount
        to one stream without one deadlocks the pair: this reader waits for a
        newline that will not come until the tool writes more, while the tool
        blocks writing to the other stream whose pipe buffer has filled. That is
        an unrecoverable hang with no output to explain it, so the terminator is
        treated as something to look for in the data, never as something to wait
        for. The setup executor reads its pseudo-terminal the same way.
        """
        selector = selectors.DefaultSelector()
        readers = {
            process.stdout: _StreamReader("stdout", out_file),
            process.stderr: _StreamReader("stderr", err_file),
        }
        for stream, reader in readers.items():
            selector.register(stream, selectors.EVENT_READ, reader)
        open_streams = len(readers)
        try:
            while open_streams:
                for key, _mask in selector.select(timeout=0.3):
                    reader = key.data
                    try:
                        chunk = os.read(key.fileobj.fileno(), READ_CHUNK_BYTES)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        for message in reader.finish():
                            self._deliver(reader.name, message, stderr_tail, log)
                        selector.unregister(key.fileobj)
                        open_streams -= 1
                        continue
                    for message in reader.feed(chunk):
                        self._deliver(reader.name, message, stderr_tail, log)
                if self.cancel_event.is_set():
                    process.terminate()
                    break
        finally:
            selector.close()

    @staticmethod
    def _deliver(stream_name: str, message: str, stderr_tail: list[str], log: LogCallback) -> None:
        if not message:
            return
        if stream_name == "stderr":
            stderr_tail.append(message)
            if len(stderr_tail) > 200:
                del stderr_tail[:100]
        # The captured files keep every byte; only the on-screen log drops
        # known-benign chatter.
        if not is_harmless_tool_noise(message):
            log(message)
