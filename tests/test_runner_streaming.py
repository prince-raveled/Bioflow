"""How a stage's output is read, and why it is read in chunks.

Reading a line at a time deadlocked the pair of streams: the reader blocked
waiting for a terminator while the tool blocked writing to the other stream
whose pipe buffer had filled, and neither could proceed. The run hung with no
output to explain it and no timeout to end it.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.execution.runner import CommandRunner  # noqa: E402
from backend.execution.stage import StageCommand  # noqa: E402


class _DirectResolver:
    """Runs the command as written, bypassing the managed environment.

    These tests are about stream handling, not tool resolution, so they use
    small Python programs whose output is known exactly.
    """

    class _Config:
        def environment_name(self, key):
            return "test"

    config = _Config()

    def resolve(self, environment_key, command):
        return command[0], command[1:]


class StreamPumpTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-pump-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _program(self, body: str) -> Path:
        path = self.root / "tool.py"
        path.write_text(body, encoding="utf-8")
        return path

    def _run(self, program: Path, timeout: float = 60.0):
        runner = CommandRunner(resolver=_DirectResolver())
        command = StageCommand("probe", [sys.executable, str(program)], "test")
        messages: list[str] = []
        outcome: dict = {}
        done = threading.Event()

        def go():
            try:
                outcome["record"] = runner.run(
                    command, self.root / "out.log", self.root / "err.log", messages.append
                )
            except Exception as error:  # noqa: BLE001 - reported by the assertion
                outcome["error"] = error
            done.set()

        threading.Thread(target=go, daemon=True).start()
        finished = done.wait(timeout=timeout)
        self.assertTrue(finished, "the runner did not return: the streams deadlocked")
        self.assertNotIn("error", outcome, str(outcome.get("error")))
        return outcome["record"], messages

    # ------------------------------------------------------------------
    def test_output_without_a_terminator_does_not_deadlock(self):
        program = self._program(
            "import sys\n"
            "sys.stdout.write('A' * 900_000)\n"
            "sys.stdout.flush()\n"
            "for i in range(3000):\n"
            "    sys.stderr.write('stderr %d ' % i + 'y' * 300 + '\\n')\n"
            "sys.stderr.flush()\n"
            "sys.stdout.write('\\nEND\\n')\n"
        )
        record, messages = self._run(program)
        self.assertEqual(record.exit_code, 0)
        self.assertTrue(messages, "nothing reached the interface")

    def test_a_carriage_return_delimits_a_message(self):
        program = self._program(
            "import sys\n"
            "for i in range(50):\n"
            "    sys.stdout.write('\\rprogress %d' % i)\n"
            "sys.stdout.write('\\ndone\\n')\n"
        )
        record, messages = self._run(program)
        self.assertEqual(record.exit_code, 0)
        self.assertGreater(len(messages), 10, "carriage-return redraws were not split")
        self.assertIn("done", messages)

    def test_an_unterminated_run_is_still_shown_before_it_ends(self):
        # A tool that never writes a terminator must not stay invisible.
        program = self._program("import sys\nsys.stdout.write('Z' * 60_000)\n")
        _record, messages = self._run(program)
        self.assertTrue(
            any(message.startswith("Z") for message in messages),
            "a long unterminated run produced no interface output at all",
        )

    def test_the_capture_files_are_byte_for_byte_what_the_tool_wrote(self):
        program = self._program(
            "import sys\n"
            "text = ('α β γ — 日本語 — ünïcödé ' * 40 + '\\n')\n"
            "for _ in range(60):\n"
            "    sys.stdout.buffer.write(text.encode('utf-8'))\n"
            "sys.stderr.buffer.write('末尾\\n'.encode('utf-8'))\n"
        )
        self._run(program)
        truth = subprocess.run([sys.executable, str(program)], capture_output=True)
        self.assertEqual(
            (self.root / "out.log").read_bytes(),
            truth.stdout,
            "multi-byte characters split across a read boundary were corrupted",
        )
        self.assertEqual((self.root / "err.log").read_bytes(), truth.stderr)

    def test_a_failing_command_keeps_its_exit_code(self):
        program = self._program("import sys\nsys.stderr.write('bad\\n')\nsys.exit(3)\n")
        record, messages = self._run(program)
        self.assertEqual(record.exit_code, 3)
        self.assertIn("bad", record.stderr_tail)
        self.assertTrue(any("exit code 3" in message for message in messages))

    def test_harmless_chatter_is_hidden_but_still_captured(self):
        from backend.execution.runner import HARMLESS_TOOL_NOISE

        noise = HARMLESS_TOOL_NOISE[0]
        program = self._program(
            f"import sys\nsys.stderr.write({noise!r} + '\\n')\n"
            "sys.stderr.write('a real message\\n')\n"
        )
        _record, messages = self._run(program)
        self.assertNotIn(noise, messages, "benign chatter reached the interface")
        self.assertIn("a real message", messages)
        self.assertIn(
            noise,
            (self.root / "err.log").read_text(encoding="utf-8"),
            "the capture file must keep everything, including filtered lines",
        )

    def test_cancelling_a_process_that_ignores_termination_still_returns(self):
        from backend.execution.runner import CommandCancelled

        program = self._program(
            "import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "sys.stderr.write('started\\n'); sys.stderr.flush()\n"
            "time.sleep(600)\n"
        )
        runner = CommandRunner(resolver=_DirectResolver())
        command = StageCommand("probe", [sys.executable, str(program)], "test")
        outcome: dict = {}
        done = threading.Event()

        def go():
            try:
                runner.run(command, self.root / "o.log", self.root / "e.log", lambda m: None)
            except CommandCancelled:
                outcome["cancelled"] = True
            except Exception as error:  # noqa: BLE001
                outcome["error"] = error
            done.set()

        threading.Thread(target=go, daemon=True).start()
        time.sleep(2)
        runner.cancel()
        self.assertTrue(done.wait(timeout=45), "cancellation never returned")
        self.assertTrue(outcome.get("cancelled"), str(outcome))
