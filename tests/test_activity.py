"""Exercise terminal activity through real provider translation, loop, and file tools."""

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from chiikawa import cli, provider
from chiikawa.terminal import TerminalDisplay
from chiikawa.tools import core_tools


def response(text="", calls=(), summary=""):
    output = [{"type": "reasoning", "encrypted_content": "OPAQUE-NEVER-DISPLAY",
               "content": [{"type": "text", "text": "PRIVATE-NEVER-DISPLAY"}],
               "summary": [{"type": "summary_text", "text": summary}] if summary else []}]
    if text:
        output.append({"type": "message", "content": [{"type": "output_text", "text": text}]})
    for index, (name, args) in enumerate(calls):
        output.append({"type": "function_call", "name": name, "call_id": f"{name}-{index}",
                       "arguments": json.dumps(args)})
    return {"status": "completed", "output": output}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="chiikawa-activity-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_cli(self, replies, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"), \
             patch.object(provider, "_post_stream", side_effect=replies) as post:
            status = cli.main(["-d", str(self.root), "-p", "work on greeting", *options])
        return status, stdout.getvalue(), stderr.getvalue(), post

    def test_real_read_edit_write_and_command_activity(self):
        (self.root / "hello.py").write_text('print("old")\n')
        status, stdout, activity, post = self.run_cli([
            response("I'll inspect the greeting before updating it.", [
                ("read_file", {"path": "hello.py"})], "Check the current greeting and verify the change."),
            response(calls=[("edit_file", {"path": "hello.py", "old": '"old"', "new": '"new"'}),
                            ("write_file", {"path": "notes.txt", "content": "updated\n"})]),
            response(calls=[("bash", {"command": "printf 'verification passed\\n'"})]),
            response("Updated and verified.")])
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "Updated and verified.\n")
        self.assertEqual((self.root / "hello.py").read_text(), 'print("new")\n')
        self.assertEqual((self.root / "notes.txt").read_text(), "updated\n")
        for expected in ("Thinking...", "Reasoning summary", "Check the current greeting", "Progress",
                         "Read hello.py [read_file]", "1 line", "Edit hello.py [edit_file]",
                         '-print("old")', '+print("new")', "updated +1 -1", "created +1 -0",
                         "Run shell command [bash]", "verification passed", "exit 0"):
            self.assertIn(expected, activity)
        for hidden in ("OPAQUE-NEVER-DISPLAY", "PRIVATE-NEVER-DISPLAY", "\033"):
            self.assertNotIn(hidden, stdout + activity)
        self.assertTrue(all(call.args[1]["reasoning"] == {"effort": "medium", "summary": "auto"}
                            for call in post.call_args_list))

    def test_blocked_write_has_no_diff_and_does_not_expose_content(self):
        status, _, activity, _ = self.run_cli([
            response(calls=[("write_file", {"path": "denied.txt", "content": "UNWRITTEN-CONTENT"})]),
            response("Write denied.")], "--mode", "read-only")
        self.assertEqual(status, 0)
        self.assertFalse((self.root / "denied.txt").exists())
        self.assertIn("Blocked: Write denied.txt", activity)
        self.assertNotIn("UNWRITTEN-CONTENT", activity)
        self.assertNotIn("created +", activity)

    def test_safe_mode_approval_stays_on_activity_stream(self):
        with patch("builtins.input", return_value="y"):
            status, stdout, activity, _ = self.run_cli([
                response(calls=[("write_file", {"path": "approved.txt", "content": "approved\n"})]),
                response("Saved.")], "--mode", "safe")
        self.assertEqual(status, 0)
        self.assertEqual(stdout, "Saved.\n")
        self.assertIn("approve write_file? [y/a/N]", activity)
        self.assertIn("created +1 -0", activity)
        self.assertEqual((self.root / "approved.txt").read_text(), "approved\n")

    def test_nonzero_command_with_output_and_failed_edit_are_not_successes(self):
        (self.root / "hello.py").write_text("original")
        status, _, activity, _ = self.run_cli([
            response(calls=[("bash", {"command": "printf 'test failed\\n'; exit 7"}),
                            ("edit_file", {"path": "hello.py", "old": "missing", "new": "never written"})]),
            response("Reported errors.")])
        self.assertEqual(status, 0)
        self.assertIn("Failed: Run shell command · exit 7", activity)
        self.assertIn("test failed", activity)
        self.assertIn("Failed: Edit hello.py", activity)
        self.assertNotIn("+never written", activity)
        self.assertEqual((self.root / "hello.py").read_text(), "original")

    def test_summary_opt_out_and_provider_failure_cleanup(self):
        status, _, activity, post = self.run_cli([response("Done", summary="Do not show")], "--no-reasoning")
        self.assertEqual(status, 0)
        self.assertNotIn("reasoning", post.call_args.args[1])
        self.assertNotIn("Do not show", activity)
        status, _, activity, _ = self.run_cli([RuntimeError("provider unavailable")])
        self.assertEqual(status, 1)
        self.assertIn("Model request stopped", activity)
        self.assertIn("provider unavailable", activity)
        self.assertNotIn("Model response received", activity)

    def test_overwrite_diff_uses_actual_previous_file_and_bounds_large_diff(self):
        tools = {tool.name: tool for tool in core_tools(self.root)}
        (self.root / "notes.txt").write_text("old\n")
        result = tools["write_file"].run(path="notes.txt", content="new\n")
        self.assertIn("-old", result.details["diff"])
        self.assertIn("+new", result.details["diff"])
        self.assertEqual(result.details["operation"], "updated")
        result = tools["write_file"].run(path="large.txt", content="x" * 150_000)
        self.assertIn("diff_note", result.details)
        self.assertEqual((self.root / "large.txt").stat().st_size, 150_000)

    def test_output_controls_are_escaped_and_previews_are_bounded(self):
        stream = io.StringIO()
        display = TerminalDisplay(stream, stream)
        display("tool_start", {"name": "read_file", "args": {"path": "bad\033[2J.txt"}})
        display("tool_end", {"name": "read_file", "text": "\033[2J" + "x\n" * 10000})
        display("assistant", {"text": "answer\033]52;secret\a"})
        output = stream.getvalue()
        self.assertNotIn("\033", output)
        self.assertNotIn("\a", output)
        self.assertIn("preview truncated", output)
        self.assertLess(len(output), 2000)

    def test_spinner_stops_and_no_color_is_honored(self):
        stream = io.StringIO()
        with patch.object(stream, "isatty", return_value=True), \
             patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": "1"}):
            display = TerminalDisplay(stream, stream)
            self.addCleanup(display.close)
            display("model_start", {"model": "test"})
            time.sleep(0.15)
            display("model_end", {"ok": True})
            self.assertIsNone(display.worker)
            self.assertIn("Thinking...", stream.getvalue())
            display("reasoning", {"text": "Summary"})
            self.assertNotIn("\033[1;35m", stream.getvalue())
            before = stream.getvalue()
            time.sleep(0.15)
            self.assertEqual(stream.getvalue(), before)


if __name__ == "__main__":
    unittest.main()
