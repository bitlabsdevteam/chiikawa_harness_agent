"""Verify token displays against real context transformations and API usage fields."""

import contextlib
import copy
import io
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chiikawa import Harness, cli, context, provider, session
from chiikawa.terminal import TerminalDisplay


def api_reply(usage=None):
    """Supply a complete Responses payload with optional independently known usage."""
    reply = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": "Done."}]}]}
    if usage is not None:
        reply["usage"] = usage
    return reply


class MetricsTests(unittest.TestCase):
    def setUp(self):
        """Use a fresh workspace and separate activity and answer streams."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_cli_reports_api_counts_and_real_configured_limits(self):
        """Bind the displayed cap to the same value used by the actual API request."""
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"), \
             patch.object(provider, "MAX_OUTPUT_TOKENS", 4096), \
             patch.object(provider, "_post", return_value=api_reply({"input_tokens": 2345, "output_tokens": 123})) as post:
            status = cli.main(["-d", str(self.root), "-p", "hello", "--context-threshold", "12000"])
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "Done.\n")
        self.assertEqual(post.call_args.args[1]["max_output_tokens"], 4096)
        self.assertIn("compact above 12,000", stderr.getvalue())
        self.assertIn("Response tokens (API): input 2,345 · output 123 / 4,096 limit", stderr.getvalue())
        expected = math.ceil(context.estimate_tokens([{"role": "user", "text": "hello", "profile": "standard"}]))
        self.assertIn(f"Context history (est.): ~{expected} tokens", stderr.getvalue())
        self.assertNotIn("\033", stderr.getvalue())

    def test_missing_usage_and_reported_zero_remain_distinct(self):
        """Never invent a zero-token response when the endpoint omits usage."""
        for usage, expected in ((None, "input not reported · output not reported"),
                                ({"input_tokens": 0, "output_tokens": 0}, "input 0 · output 0"),
                                ({"output_tokens": 7}, "input not reported · output 7")):
            output = io.StringIO()
            display = TerminalDisplay(output, io.StringIO())
            harness = Harness(self.root, activity=True, on_event=display, persist=False)
            with patch.object(provider, "api_root", return_value="https://example.com/openai/v1"), \
                 patch.object(provider, "_post", return_value=api_reply(usage)):
                harness.run("hello")
            self.assertIn(expected, output.getvalue())

    def test_compaction_reports_before_after_and_its_own_output_usage(self):
        """Run actual compaction and ensure the final meter measures the reduced view."""
        events = []
        harness = Harness(self.root, budget_tokens=1000, activity=True,
                          on_event=lambda kind, payload: events.append((kind, copy.deepcopy(payload))))
        harness.messages = [{"role": "user" if i % 2 == 0 else "assistant", "text": "x" * 4000}
                            for i in range(12)]
        original = copy.deepcopy(harness.messages)
        seen = []

        def complete(model, system, messages, tools, **options):
            """Return distinct usage for summarization and the normal model response."""
            seen.append((system, copy.deepcopy(messages)))
            if system == context.SUMMARY_SYSTEM:
                return {"text": "Previous work summarized.", "tool_calls": [], "usage": {"input": 7000, "output": 80}}
            return {"text": "Done.", "tool_calls": [], "usage": {"input": 6100, "output": 42}}

        with patch.object(provider, "complete", side_effect=complete):
            harness.run("continue")
        old = original + [{"role": "user", "text": "continue", "profile": "standard"}]
        initial = next(payload for kind, payload in events if kind == "context")
        self.assertEqual(initial["estimated_tokens"], context.estimate_tokens(old))
        self.assertEqual(initial["threshold"], 1000)
        changes = [payload for kind, payload in events if kind == "compaction"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["tokens_before"], context.estimate_tokens(old))
        self.assertEqual(changes[0]["tokens_after"], context.estimate_tokens(seen[1][1]))
        self.assertLess(changes[0]["tokens_after"], changes[0]["tokens_before"])
        usages = [payload for kind, payload in events if kind == "usage"]
        self.assertEqual([(item["source"], item["output"]) for item in usages],
                         [("compaction", 80), ("response", 42)])
        final = [payload for kind, payload in events if kind == "context"][-1]
        self.assertEqual(final["estimated_tokens"], context.estimate_tokens(harness.messages))
        persisted = session._read(harness.session_path)
        self.assertEqual(len(persisted), len(old) + 1)
        self.assertFalse(any("usage" in item or "estimated_tokens" in item for item in persisted))
        restored = Harness(self.root)
        self.assertTrue(restored.resume(harness.session_path))
        self.assertEqual(context.context_status(restored.messages, 1000)["estimated_tokens"],
                         context.estimate_tokens(persisted))
        rendered = io.StringIO()
        display = TerminalDisplay(rendered, io.StringIO())
        for kind, payload in events:
            display(kind, payload)
        self.assertIn("Compacted context:", rendered.getvalue())
        self.assertIn("Compaction tokens (API): input 7,000 · output 80", rendered.getvalue())

    def test_threshold_guard_and_failed_compaction_are_not_misrepresented(self):
        """The threshold is strict; short histories defer, and failures never show success."""
        history = [{"role": "user", "text": "content"}] * 8
        exact = context.estimate_tokens(history)
        self.assertFalse(context.context_status(history, exact)["can_compact"])
        self.assertTrue(context.context_status(history, exact - 1)["can_compact"])
        self.assertFalse(context.context_status(history[:7], 0)["can_compact"])
        output = io.StringIO()
        display = TerminalDisplay(output, io.StringIO())
        display("context", context.context_status(history[:7], 0))
        self.assertIn("waiting for more history", output.getvalue())
        harness = Harness(self.root, budget_tokens=0, activity=True, on_event=display, persist=False)
        harness.messages = copy.deepcopy(history)
        with patch.object(provider, "complete", side_effect=RuntimeError("summary failed")):
            with self.assertRaisesRegex(RuntimeError, "summary failed"):
                harness.run("continue")
        self.assertIn("Compacting context", output.getvalue())
        self.assertNotIn("Compacted context:", output.getvalue())
        self.assertNotIn("Response tokens", output.getvalue())
        self.assertEqual(harness.messages[:-1], history)
        self.assertIsNone(display.worker)

    def test_cli_rejects_negative_threshold_before_creating_harness(self):
        """Bad threshold input cannot create a session or contact a model."""
        with patch.object(cli, "Harness") as factory, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                cli.main(["--context-threshold", "-1"])
        self.assertEqual(result.exception.code, 2)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
