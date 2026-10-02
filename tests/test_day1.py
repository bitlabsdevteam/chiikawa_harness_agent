"""Day 1: verify wire contracts, transport recovery, and observable loop behavior.

Use standard-library mocks at the HTTP/model boundary so offline tests cannot
spend API quota. Live transcripts are separate evidence, never mocked claims.
"""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock, patch

from chiikawa import provider
from chiikawa.loop import run_loop
from demos.day1_dice import RollDice, TASK, before_tool, load_credentials, on_event


def answer(text="", calls=None):
    """Build a neutral provider response for deterministic loop scenarios."""
    return {"text": text, "tool_calls": calls or [], "usage": {"input": 1, "output": 2}}


def call(name="roll_dice", args=None):
    """Build a correlated model tool call, preserving explicitly supplied arguments."""
    return {"name": name, "args": {"count": "3"} if args is None else args,
            "call_id": "call_dice"}


class ProviderTests(unittest.TestCase):
    """Check authentication, exact wire translation, and bounded HTTP retries."""

    def test_key_priority_fallback_and_missing(self):
        """Prefer Chiikawa's key and fail clearly without either environment key."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "CHIIKAWA_API_KEY or AZURE_OPENAI_API_KEY"):
                provider.api_key()
            os.environ["AZURE_OPENAI_API_KEY"] = "fallback"
            self.assertEqual(provider.api_key(), "fallback")
            os.environ["CHIIKAWA_API_KEY"] = "preferred"
            self.assertEqual(provider.api_key(), "preferred")

    def test_endpoint_normalization(self):
        """Accept resource and v1 endpoints without duplicating the API path."""
        for endpoint in ("https://example.openai.azure.com", "https://example.openai.azure.com/",
                         "https://example.openai.azure.com/openai/v1/"):
            with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": endpoint}):
                self.assertEqual(provider.api_root(), "https://example.openai.azure.com/openai/v1")
        for endpoint in ("", "http://example.com", "https://example.com/api/projects/test",
                         "https://example.com?key=secret", "https://user:pass@example.com"):
            with patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": endpoint}):
                with self.assertRaisesRegex(RuntimeError, "AZURE_OPENAI_ENDPOINT"):
                    provider.api_root()

    def test_wire_round_trip(self):
        """Preserve reasoning, call IDs, and output order without duplicating text."""
        output = [{"type": "reasoning", "id": "rs_1", "summary": [],
                   "encrypted_content": "opaque"},
                  {"type": "function_call", "id": "fc_1", "call_id": "call_dice",
                   "name": "roll_dice", "arguments": '{"count":"3"}'}]
        messages = [{"role": "user", "text": "roll"},
                    {"role": "assistant", "text": "", "tool_calls": [call()],
                     "provider_output": output},
                    {"role": "tool", "name": "roll_dice", "text": "[4, 4, 4]",
                     "call_id": "call_dice"}]
        original = copy.deepcopy(messages)
        self.assertEqual(provider._to_wire(messages), [
            {"role": "user", "content": "roll"}, *output,
            {"type": "function_call_output", "call_id": "call_dice", "output": "[4, 4, 4]"},
        ])
        self.assertEqual(messages, original)
        manual = {"role": "assistant", "text": "Rolling", "tool_calls": [call()]}
        self.assertEqual(provider._to_wire([manual]), [
            {"role": "assistant", "content": "Rolling"},
            {"type": "function_call", "call_id": "call_dice", "name": "roll_dice",
             "arguments": json.dumps({"count": "3"})}])

    def test_complete_request_and_response(self):
        """Use Responses, filter opaque reasoning, and retain output for replay."""
        output = [{"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"},
                  {"type": "message", "role": "assistant", "content": [
                      {"type": "output_text", "text": "Hello "},
                      {"type": "output_text", "text": "world"}]},
                  {"type": "function_call", "name": "roll_dice", "call_id": "call_dice",
                   "arguments": '{"count":"3"}'}]
        response = {"status": "completed", "output": output,
                    "usage": {"input_tokens": 12, "output_tokens": 7}}
        with patch.object(provider, "_post", return_value=response) as post, \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"):
            result = provider.complete("gpt-6-astra", "system", [{"role": "user", "text": "hi"}],
                                       [RollDice.spec])
        self.assertEqual(result, {"text": "Hello world", "tool_calls": [call()],
                                  "usage": {"input": 12, "output": 7}, "provider_output": output})
        self.assertEqual(post.call_args.args, ("https://example.com/openai/v1/responses", {
            "model": "gpt-6-astra", "instructions": "system",
            "input": [{"role": "user", "content": "hi"}],
            "max_output_tokens": 65536, "store": False,
            "include": ["reasoning.encrypted_content"],
            "tools": [{"type": "function", **RollDice.spec["schema"], "strict": False}],
        }))

    def test_no_tools_and_missing_usage(self):
        """Omit declarations for text-only calls and default absent usage to zero."""
        output = [{"type": "message", "content": [{"type": "output_text", "text": "coffee"}]}]
        with patch.object(provider, "_post", return_value={"status": "completed", "output": output}) as post, \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"):
            result = provider.complete("model", "system", [], [])
        self.assertNotIn("tools", post.call_args.args[1])
        self.assertEqual(result, {"text": "coffee", "tool_calls": [], "provider_output": output,
                                  "usage": {"input": 0, "output": 0}})

    def test_incomplete_failed_or_empty_response(self):
        """Do not execute partial calls or report empty responses as success."""
        for response in ({}, {"status": "completed", "output": []},
                         {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
                         {"status": "failed", "error": {"message": "failure"}}):
            with patch.object(provider, "_post", return_value=response), \
                 patch.object(provider, "api_root", return_value="https://example.com/openai/v1"):
                with self.assertRaisesRegex(RuntimeError, "Foundry"):
                    provider.complete("model", "system", [], [])

    def test_refusal_is_visible(self):
        """Surface a refusal as assistant text rather than silently dropping it."""
        output = [{"type": "message", "content": [{"type": "refusal", "refusal": "Cannot help."}]}]
        with patch.object(provider, "_post", return_value={"status": "completed", "output": output}), \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"):
            self.assertEqual(provider.complete("model", "system", [], [])["text"], "Cannot help.")

    def test_non_object_arguments_rejected(self):
        """A function call must supply a keyword argument object."""
        output = [{"type": "function_call", "name": "roll_dice", "call_id": "call_dice",
                   "arguments": '[]'}]
        with patch.object(provider, "_post", return_value={"status": "completed", "output": output}), \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"):
            with self.assertRaisesRegex(RuntimeError, "JSON object"):
                provider.complete("model", "system", [], [])

    def test_transient_retries_and_transport_contract(self):
        """Retry all specified HTTP/network failures with the expected backoff."""
        for failure in (429, 500, 502, 503, "network", "timeout", "reset"):
            with self.subTest(failure=failure):
                if isinstance(failure, int):
                    error = urllib.error.HTTPError("https://example.com", failure, "bad", {},
                                                   io.BytesIO(b"temporary"))
                else:
                    error = (TimeoutError("slow") if failure == "timeout" else
                             ConnectionResetError("peer reset") if failure == "reset" else
                             urllib.error.URLError("offline"))
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
                self.assertEqual(request.get_header("Api-key"), "test-key")
                self.assertEqual(json.loads(request.data), {"hello": "world"})
                self.assertEqual(opening.call_args.kwargs, {"timeout": 600})

    def test_connection_reset_while_reading_response_retries(self):
        """A peer can reset an established connection before its JSON body arrives."""
        broken = io.BytesIO()
        broken.read = Mock(side_effect=ConnectionResetError("peer reset"))
        with patch.object(provider, "api_key", return_value="test-key"), \
             patch.object(provider.time, "sleep") as sleep, \
             patch.object(provider.urllib.request, "urlopen", side_effect=[
                 broken, io.BytesIO(b'{"ok": true}')]) as opening:
            self.assertEqual(provider._post("https://example.com", {}), {"ok": True})
        self.assertEqual(opening.call_count, 2)
        self.assertTrue(broken.closed)
        sleep.assert_called_once_with(2)

    def test_nonretryable_error_truncation(self):
        """Surface status and exactly the first 400 error-body characters."""
        error = urllib.error.HTTPError("url", 403, "denied", {}, io.BytesIO(b"x" * 450))
        with patch.object(provider, "api_key", return_value="key"), \
             patch.object(provider.urllib.request, "urlopen", side_effect=error) as opening, \
             patch.object(provider.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as caught:
                provider._post("https://example.com", {})
        self.assertEqual(str(caught.exception), "Foundry HTTP 403: " + "x" * 400)
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

    def test_foundry_reasoning_and_duplicate_tool_names_round_trip(self):
        """Carry raw output through the real loop and match repeated tools by ID."""
        output = [{"type": "reasoning", "id": "rs_test", "summary": [],
                   "encrypted_content": "opaque"},
                  {"type": "function_call", "id": "fc_a", "call_id": "call_a",
                   "name": "roll_dice", "arguments": '{"count":"1"}'},
                  {"type": "function_call", "id": "fc_b", "call_id": "call_b",
                   "name": "roll_dice", "arguments": '{"count":"2"}'}]
        final = [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Done."}]}]
        messages = [{"role": "user", "text": "Roll twice"}]
        with patch.object(provider, "_post", side_effect=[
                {"status": "completed", "output": output},
                {"status": "completed", "output": final}]) as post, \
             patch.object(provider, "api_root", return_value="https://example.com/openai/v1"), \
             patch("demos.day1_dice.random.randint", side_effect=[1, 2, 3]):
            result = run_loop("gpt-6-astra", "system", messages, {"roll_dice": RollDice()},
                              Mock(), before_tool)
        self.assertEqual(result, "Done.")
        self.assertEqual(post.call_args.args[1]["input"], [
            {"role": "user", "content": "Roll twice"}, *output,
            {"type": "function_call_output", "call_id": "call_a", "output": "[1]"},
            {"type": "function_call_output", "call_id": "call_b", "output": "[2, 3]"},
        ])
        self.assertEqual(messages[-1]["provider_output"], final)

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


class CredentialTests(unittest.TestCase):
    """Keep local test configuration explicit and treat file contents as data."""

    def test_explicit_file_overrides_environment_without_printing(self):
        """Use the selected file's key even when an unrelated environment key exists."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md") as config, \
             patch.dict(os.environ, {"CHIIKAWA_API_KEY": "old"}, clear=True), \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            config.write("endpoint=https://example.com/openai/v1\nmodel=gpt-6-astra\nAPI_KEY=test=value\n")
            config.flush()
            self.assertEqual(load_credentials(config.name), "gpt-6-astra")
            self.assertEqual(provider.api_key(), "test=value")
            self.assertEqual(provider.api_root(), "https://example.com/openai/v1")
            self.assertEqual(stdout.getvalue(), "")

    def test_incomplete_file_does_not_change_environment(self):
        """Validate all fields before applying any configuration changes."""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".md") as config, \
             patch.dict(os.environ, {}, clear=True):
            config.write("API_KEY=test\n")
            config.flush()
            with self.assertRaisesRegex(RuntimeError, "must define"):
                load_credentials(config.name)
            self.assertNotIn("CHIIKAWA_API_KEY", os.environ)


if __name__ == "__main__":
    unittest.main()
