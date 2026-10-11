"""Opt-in --env-file loading: allowlisted, non-overriding, quote-tolerant, and never printed."""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chiikawa import cli, envfile, provider

URL = "https://example-resource.openai.azure.com"
SECRET = "sentinel-secret-value-123"


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.path = self.root / "settings.env"

    def write(self, text):
        self.path.write_text(text)

    def test_parse_handles_quotes_export_comments_and_blanks(self):
        values = envfile.parse(f"# note\n\nexport A_ONE=\"{URL}\"\nA_TWO='x y'\nA_THREE = plain # trailing\nnot a line\nA_FOUR=\"a # b\"\n")
        self.assertEqual(values, {"A_ONE": URL, "A_TWO": "x y", "A_THREE": "plain", "A_FOUR": "a # b"})

    def test_load_is_allowlisted_nonoverriding_and_returns_names_only(self):
        self.write(f'AZURE_OPENAI_ENDPOINT="{URL}"\nAZURE_OPENAI_API_KEY={SECRET}\nPATH=/evil\nHOME=/evil\n'
                   "OPENROUTER_API_KEY=\nCHIIKAWA_MODEL=from-file\n")
        os.environ["CHIIKAWA_MODEL"] = "from-env"
        loaded = envfile.load(self.path)
        self.assertEqual(loaded, ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"])
        self.assertEqual(os.environ["CHIIKAWA_MODEL"], "from-env")
        self.assertNotIn("PATH", os.environ)
        self.assertNotIn("OPENROUTER_API_KEY", os.environ)
        self.assertEqual(provider.api_root(), URL + "/openai/v1")

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            envfile.load(self.root / "absent.env")

    def test_cli_flag_loads_before_the_provider_is_used_and_prints_no_values(self):
        self.write(f'AZURE_OPENAI_ENDPOINT="{URL}"\nAZURE_OPENAI_API_KEY={SECRET}\n')
        seen = {}

        def complete(*args, **kwargs):
            seen["root"] = provider.api_root()
            seen["key"] = os.environ.get("AZURE_OPENAI_API_KEY")
            return {"text": "ok", "tool_calls": []}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
             patch.object(provider, "complete", side_effect=complete):
            status = cli.main(["-d", str(self.root / "work"), "--env-file", str(self.path), "-p", "hello"])
        self.assertEqual(status, 0)
        self.assertEqual((seen["root"], seen["key"]), (URL + "/openai/v1", SECRET))
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())

    def test_cli_unreadable_file_is_a_usage_error_without_the_path_contents(self):
        with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as caught:
            cli.main(["-d", str(self.root), "--env-file", str(self.root / "absent.env"), "-p", "x"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("cannot read --env-file", err.getvalue())

    def test_not_loaded_without_the_flag(self):
        (self.root / ".env").write_text(f'AZURE_OPENAI_ENDPOINT="{URL}"\n')
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        with self.assertRaises(RuntimeError):
            provider.api_root()


if __name__ == "__main__":
    unittest.main()
