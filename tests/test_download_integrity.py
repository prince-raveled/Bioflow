"""What happens to a download that does not finish.

A transfer cut short ends the read loop exactly as a completed one does. Without
a length check the partial file was renamed to the final name and treated as
installed data from then on, which is the worst outcome available: the failure
surfaces later, somewhere else, as corrupt reference data.
"""

from pathlib import Path
import http.server
import socketserver
import tempfile
import threading
import unittest

import support  # noqa: F401  (puts app/ on the path)

from backend.setup import bootstrap  # noqa: E402


PAYLOAD = bytes(range(256)) * 2000  # 512,000 bytes, checkable byte for byte


def _handler(state):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - http.server's required spelling
            header = self.headers.get("Range")
            start = 0
            if header and header.startswith("bytes="):
                start = int(header.split("=")[1].split("-")[0])
            body = PAYLOAD[start:]
            if start:
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
            else:
                self.send_response(200)
            if state.get("no_length"):
                self.send_header("Connection", "close")
            else:
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if state.get("truncate") and not start:
                self.wfile.write(body[: state.get("cut", 100_000)])
                self.close_connection = True
            else:
                self.wfile.write(body)

        def log_message(self, *args):
            pass

    return Handler


class DownloadIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.state = {"truncate": False}
        self.server = socketserver.TCPServer(("127.0.0.1", 0), _handler(self.state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        # shutdown() stops the accept loop; server_close() releases the socket.
        # Only doing the first leaks a descriptor per test.
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/file"
        self._temporary = tempfile.TemporaryDirectory(prefix="bioflow-download-")
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.destination = self.root / "reference.bin"
        self.partial = self.destination.with_name(self.destination.name + ".part")
        self.messages: list[str] = []

    def _download(self):
        bootstrap.download_file(self.url, self.destination, self.messages.append)

    # ------------------------------------------------------------------
    def test_a_complete_download_is_promoted(self):
        self._download()
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
        self.assertFalse(self.partial.exists())

    def test_a_short_download_is_refused(self):
        self.state["truncate"] = True
        with self.assertRaises(RuntimeError) as caught:
            self._download()
        self.assertIn("ended early", str(caught.exception))
        self.assertFalse(
            self.destination.exists(),
            "a truncated transfer must never appear under the final name",
        )

    def test_a_short_download_keeps_what_it_got(self):
        self.state["truncate"] = True
        with self.assertRaises(RuntimeError):
            self._download()
        self.assertTrue(self.partial.is_file())
        self.assertEqual(self.partial.stat().st_size, 100_000)

    def test_the_next_attempt_resumes_rather_than_restarting(self):
        self.state["truncate"] = True
        with self.assertRaises(RuntimeError):
            self._download()
        self.state["truncate"] = False
        self._download()
        self.assertEqual(
            self.destination.read_bytes(),
            PAYLOAD,
            "the resumed file does not match the source byte for byte",
        )
        self.assertTrue(any("Resuming" in message for message in self.messages))
        self.assertFalse(self.partial.exists())

    def test_a_resumed_download_is_not_silently_doubled(self):
        # Appending to the partial file at the wrong offset would produce a file
        # that is too long and passes a naive size check.
        self.state["truncate"] = True
        with self.assertRaises(RuntimeError):
            self._download()
        self.state["truncate"] = False
        self._download()
        self.assertEqual(self.destination.stat().st_size, len(PAYLOAD))

    def test_a_server_that_declares_no_length_is_still_accepted(self):
        # Nothing can be verified in that case, so the transfer must not be
        # rejected outright; the check only applies to a declared length.
        self.state["no_length"] = True
        self._download()
        self.assertEqual(self.destination.read_bytes(), PAYLOAD)
