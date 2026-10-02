"""Day 1: verify wire contracts, transport recovery, and observable loop behavior.

Use standard-library mocks at the HTTP/model boundary so offline tests cannot
spend API quota. Live transcripts are separate evidence, never mocked claims.
"""

import contextlib
import copy
import io
import json
import os
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chiikawa import provider
from chiikawa.loop import run_loop
from demos.day1_dice import RollDice, TASK, before_tool, on_event


def answer(text="", calls=None):
    """Build a neutral provider response for deterministic loop scenarios."""
    return {"text": text, "tool_calls": calls or [], "usage": {"input": 1, "output": 2}}


def call(name="roll_dice", args=None):
    """Build a signed model tool call, preserving explicitly supplied arguments."""
    return {"name": name, "args": {"count": "3"} if args is None else args,
            "signature": "opaque-signature"}


class ProviderTests(unittest.TestCase):
    """Check authentication, exact wire translation, and bounded HTTP retries."""

    def test_key_priority_fallback_and_missing(self):
        """Prefer Chiikawa's key and fail clearly without either environment key."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CHIIKAWA_API_KEY or GEMINI_API_KEY"):
                provider.api_key()
            os.environ["GEMINI_API_KEY"] = "fallback"
            self.assertEqual(provider.api_key(), "fallback")
            os.environ["CHIIKAWA_API_KEY"] = "preferred"
            self.assertEqual(provider.api_key(), "preferred")

    def test_wire_round_trip(self):
        """Keep signatures on function calls and wrap tool output as user content."""
        messages = [{"role": "user", "text": "roll"},
                    {"role": "assistant", "text": "Rolling", "tool_calls": [call()]},
                    {"role": "tool", "name": "roll_dice", "text": "[4, 4, 4]"}]
        original = copy.deepcopy(messages)
        self.assertEqual(provider._to_wire(messages), [
            {"role": "user", "parts": [{"text": "roll"}]},
            {"role": "model", "parts": [{"text": "Rolling"}, {
                "functionCall": {"name": "roll_dice", "args": {"count": "3"}},
                "thoughtSignature": "opaque-signature"}]},
            {"role": "user", "parts": [{"functionResponse": {
                "name": "roll_dice", "response": {"result": "[4, 4, 4]"}}}]},
        ])
        self.assertEqual(messages, original)
        unsigned = {"role": "assistant", "text": "", "tool_calls": [
            {"name": "noop", "args": {}, "signature": None}]}
        self.assertEqual(provider._to_wire([unsigned])[0]["parts"], [
            {"functionCall": {"name": "noop", "args": {}}}])

    def test_complete_request_and_response(self):
        """Hide thought text while retaining tool signatures and token usage."""
        response = {"candidates": [{"content": {"parts": [
            {"text": "private", "thought": True}, {"text": "Hello "},
            {"text": "world"}, {"functionCall": {"name": "roll_dice", "args": {"count": "3"}},
                                   "thoughtSignature": "opaque-signature"}]} }],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 7}}
        with patch.object(provider, "_post", return_value=response) as post:
            result = provider.complete("model", "system", [{"role": "user", "text": "hi"}],
                                       [RollDice.spec])
        self.assertEqual(result, {"text": "Hello world", "tool_calls": [call()],
                                  "usage": {"input": 12, "output": 7}})
        self.assertEqual(post.call_args.args, (provider.API_ROOT + "/model:generateContent", {
            "systemInstruction": {"parts": [{"text": "system"}]},
            "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 65536},
            "tools": [{"functionDeclarations": [RollDice.spec["schema"]]}],
        }))

    def test_no_tools_and_missing_usage(self):
        """Omit declarations for text-only calls and default absent usage to zero."""
        with patch.object(provider, "_post", return_value={"candidates": [
                {"content": {"parts": [{"text": "coffee"}]}}]}) as post:
            result = provider.complete("model", "system", [], [])
        self.assertNotIn("tools", post.call_args.args[1])
        self.assertEqual(result, {"text": "coffee", "tool_calls": [],
                                  "usage": {"input": 0, "output": 0}})

    def test_no_candidates_is_clear_error(self):
        """Avoid silently treating a blocked or empty provider response as success."""
        with patch.object(provider, "_post", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "no candidates"):
                provider.complete("model", "system", [], [])

    def test_transient_retries_and_transport_contract(self):
        """Retry all specified HTTP/network failures with the expected backoff."""
        for failure in (429, 500, 502, 503, "network", "timeout"):
            with self.subTest(failure=failure):
                if isinstance(failure, int):
                    error = urllib.error.HTTPError("https://example.com", failure, "bad", {},
                                                   io.BytesIO(b"temporary"))
                else:
                    error = (TimeoutError("slow") if failure == "timeout"
                             else urllib.error.URLError("offline"))
                with patch.object(provider, "api_key", return_value="test-key"), \
                     patch.object(provider.time, "sleep") as sleep, \
                     patch.object(provider.urllib.request, "urlopen", side_effect=[
                         error, io.BytesIO(b'{"ok": true}')]) as opening:
                    self.assertEqual(provider._post("https://example.com", {"hello": "world"}),
                                     {"ok": True})
                sleep.assert_called_once_with(2)
                request = opening.call_args.args[0]
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.get_header("Content-type"), "application/json")
                self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
                self.assertEqual(json.loads(request.data), {"hello": "world"})
                self.assertEqual(opening.call_args.kwargs, {"timeout": 600})

    def test_nonretryable_error_truncation(self):
        """Surface status and exactly the first 400 error-body characters."""
        error = urllib.error.HTTPError("url", 403, "denied", {}, io.BytesIO(b"x" * 450))
        with patch.object(provider, "api_key", return_value="key"), \
             patch.object(provider.urllib.request, "urlopen", side_effect=error) as opening, \
             patch.object(provider.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as caught:
                provider._post("https://example.com", {})
        self.assertEqual(str(caught.exception), "Gemini HTTP 403: " + "x" * 400)
        self.assertEqual(opening.call_count, 1)
        sleep.assert_not_called()

    def test_retry_exhaustion(self):
        """Make six attempts with five sleeps, then surface the last failure."""
        with patch.object(provider, "api_key", return_value="key"), \
             patch.object(provider.urllib.request, "urlopen", side_effect=TimeoutError()) as opening, \
             patch.object(provider.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                provider._post("https://example.com", {})
        self.assertEqual(opening.call_count, 6)
        self.assertEqual([entry.args[0] for entry in sleep.call_args_list], [2, 4, 8, 16, 32])


class LoopTests(unittest.TestCase):
    """Exercise actual tools and history mutations with scripted model replies."""

    def test_dice_transcript_and_events(self):
        """Show user, assistant call, tool result, and final answer in order."""
        messages = [{"role": "user", "text": TASK}]
        events = []
        stream = io.StringIO()

        def observe(kind, payload):
            """Capture event order while exercising the demo's transcript printer."""
            events.append(kind)
            on_event(kind, payload)

        with patch("chiikawa.loop.provider.complete", side_effect=[answer(calls=[call()]),
                   answer("The total is 12, which beats 10.")]) as complete, \
             patch("demos.day1_dice.random.randint", side_effect=[3, 4, 5]), \
             contextlib.redirect_stdout(stream):
            print(f"user: {TASK}")
            result = run_loop("model", "system", messages, {"roll_dice": RollDice()},
                              observe, before_tool)
        self.assertEqual(result, "The total is 12, which beats 10.")
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "tool", "assistant"])
        self.assertEqual(messages[2]["text"], "[3, 4, 5]")
        self.assertEqual(events, ["assistant", "tool_start", "tool_end", "assistant"])
        self.assertEqual(complete.call_args.args[3], [RollDice.spec])
        transcript = stream.getvalue()
        labels = ["user:", "assistant tool call:", "tool result", "assistant:"]
        self.assertEqual(sorted(transcript.index(label) for label in labels),
                         [transcript.index(label) for label in labels])

    def test_text_only_coffee_prompt(self):
        """An answer without tool calls returns immediately with tools available."""
        messages = [{"role": "user", "text": "Build a landing page for a coffee shop"}]
        tool = SimpleNamespace(spec=RollDice.spec, run=Mock())
        with patch("chiikawa.loop.provider.complete", return_value=answer("<html>Coffee</html>")):
            self.assertEqual(run_loop("model", "system", messages, {"roll_dice": tool},
                                      Mock(), Mock()), "<html>Coffee</html>")
        tool.run.assert_not_called()
        self.assertEqual(messages[-1]["tool_calls"], [])

    def test_block_unknown_and_tool_exception(self):
        """Return exact error strings and never execute a blocked tool."""
        for reason, name, expected in [
            ("denied", "roll_dice", "BLOCKED: denied"),
            ("", "roll_dice", "BLOCKED: "),
            (None, "missing", "ERROR: unknown tool missing"),
            (None, "roll_dice", "ERROR: ValueError: invalid count"),
        ]:
            with self.subTest(expected=expected):
                tool = SimpleNamespace(spec=RollDice.spec, run=Mock(side_effect=ValueError("invalid count")))
                messages, events = [], Mock()
                with patch("chiikawa.loop.provider.complete", side_effect=[
                        answer(calls=[call(name)]), answer("recovered")]):
                    run_loop("model", "system", messages, {"roll_dice": tool},
                             events, lambda _: reason)
                self.assertEqual(messages[1]["text"], expected)
                self.assertEqual([event.args[0] for event in events.call_args_list],
                                 ["assistant", "tool_start", "tool_end", "assistant"])
                self.assertEqual(tool.run.call_count, int(reason is None and name == "roll_dice"))

    def test_sequential_calls_compaction_and_wrapup(self):
        """Keep list identity, compact before every call, and disable wrap-up tools."""
        messages = [{"role": "user", "text": "old"}]
        identity = id(messages)
        snapshots = []
        tool = SimpleNamespace(spec=RollDice.spec, run=Mock(side_effect=["one", "two"]))

        def compact(history):
            """Capture pre-compaction history and retain only its newest message."""
            snapshots.append(copy.deepcopy(history))
            return history[-1:]

        with patch("chiikawa.loop.provider.complete", side_effect=[
                answer(calls=[call(args={"count": "1"}), call(args={"count": "2"})]),
                answer("done")]) as complete:
            result = run_loop("model", "system", messages, {"roll_dice": tool}, Mock(),
                              before_tool, max_turns=1, before_turn=compact)
        self.assertEqual(id(messages), identity)
        self.assertEqual(result, "done")
        self.assertEqual([entry.kwargs for entry in tool.run.call_args_list],
                         [{"count": "1"}, {"count": "2"}])
        self.assertEqual([m["text"] for m in snapshots[1] if m["role"] == "tool"], ["one", "two"])
        self.assertEqual(messages[0], {"role": "user", "text": "Turn limit reached; wrap up now."})
        self.assertEqual(complete.call_args.args[3], [])
        self.assertEqual(len(snapshots), 2)

    def test_zero_turn_budget(self):
        """A zero budget still records and returns one final tool-free answer."""
        messages = []
        with patch("chiikawa.loop.provider.complete", return_value=answer("done")) as complete:
            self.assertEqual(run_loop("model", "system", messages, {}, Mock(), before_tool,
                                      max_turns=0), "done")
        complete.assert_called_once_with("model", "system", messages, [])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
