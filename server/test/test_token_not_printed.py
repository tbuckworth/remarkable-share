"""The server must never print its auth token: under systemd, stdout goes to the journal.

Each test runs the real `__main__` block in a throwaway HOME, with uvicorn.run patched
out, so no server starts and the real ~/.config/web2pdf/config.json is never read.
"""

import contextlib
import io
import json
import os
import runpy
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import uvicorn

SERVER_PATH = Path(__file__).parents[1] / "web2pdf_server.py"
FAKE_TOKEN = "fake" * 8  # 32 chars, the length the server generates


class StartupOutputTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.config_file = self.home / ".config" / "web2pdf" / "config.json"

    def start_server(self) -> str:
        """Run the server's __main__ block; return everything it printed."""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}), \
                mock.patch.object(uvicorn, "run") as run, \
                contextlib.redirect_stdout(out):
            runpy.run_path(str(SERVER_PATH), run_name="__main__")
        run.assert_called_once()
        return out.getvalue()

    def write_config(self, value: str):
        self.config_file.parent.mkdir(parents=True)
        self.config_file.write_text(json.dumps({"token": value}))

    def test_first_run_prints_where_the_token_is_not_the_token(self):
        printed = self.start_server()
        generated = json.loads(self.config_file.read_text())["token"]
        self.assertEqual(len(generated), 32)
        self.assertNotIn(generated, printed)
        self.assertNotIn(generated[:5], printed)
        self.assertIn(str(self.config_file), printed)
        self.assertIn("Generated a new auth token", printed)

    def test_first_run_creates_config_mode_600(self):
        self.start_server()
        self.assertEqual(stat.S_IMODE(self.config_file.stat().st_mode), 0o600)

    def test_later_start_prints_only_a_masked_token(self):
        self.write_config(FAKE_TOKEN)
        printed = self.start_server()
        self.assertNotIn(FAKE_TOKEN, printed)
        self.assertNotIn(FAKE_TOKEN[:5], printed)
        self.assertIn("fake… (32 chars)", printed)
        self.assertIn(str(self.config_file), printed)
        self.assertNotIn("Generated", printed)

    def test_short_token_shows_no_characters(self):
        self.write_config("QQQQQQ")
        printed = self.start_server()
        self.assertNotIn("Q", printed)
        self.assertIn("(6 chars)", printed)


if __name__ == "__main__":
    unittest.main()
