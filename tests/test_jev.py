"""Jev decision layer: client behavior, tighten-only advice, request-time views, and /jev.

No test touches the network or a real key; the HTTP opener and clock are injected.
"""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from chiikawa import Harness, Policy, cli, jev, loop, provider
from chiikawa.commands import Commands

SENTINEL = "jev-sentinel-key-123"
DENIED = "advisor denied this call"


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def answers(**named):
    return {"answers": named, "usage": {"input_tokens": 11, "output_tokens": 2}}


def http_error(code):
    return urllib.error.HTTPError("https://jevmodel.org", code, "x", {}, None)


class ClientTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {jev.KEY_ENV: SENTINEL}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.requests, self.script = [], []

    def opener(self, request, timeout=None):
        self.requests.append(request)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return Response(step)

    def client(self, **options):
        return jev.JevClient(opener=self.opener, sleep=lambda seconds: None, **options)

    def test_request_shape_idempotency_and_usage(self):
        self.script = [answers(a={"type": "noul", "noul": 0.2})]
        client = self.client()
        result = client.decide("state", {"a": {"type": "noul", "instructions": "q"}})
        self.assertEqual(result["a"]["noul"], 0.2)
        request = self.requests[0]
        self.assertEqual(request.full_url, jev.ENDPOINT)
        self.assertEqual(request.get_header("Authorization"), f"Bearer {SENTINEL}")
        self.assertTrue(request.get_header("Idempotency-key"))
        body = json.loads(request.data)
        self.assertEqual((body["model"], body["state"]), ("jev-latest", "state"))
        summary = client.meter.summary()
        self.assertEqual((summary["calls"], summary["input_tokens"]), (1, 11))

    def test_retries_reuse_one_idempotency_key(self):
        self.script = [http_error(429), http_error(502), answers(a={"type": "noul", "noul": 0})]
        self.client().decide("s", {"a": {"type": "noul", "instructions": "q"}})
        keys = {request.get_header("Idempotency-key") for request in self.requests}
        self.assertEqual((len(self.requests), len(keys)), (3, 1))

    def test_errors_never_leak_the_key_and_do_not_retry_auth(self):
        self.script = [http_error(401)]
        with self.assertRaises(jev.JevError) as caught:
            self.client().decide("s", {"a": {"type": "noul", "instructions": "q"}})
        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertEqual(len(self.requests), 1)

    def test_missing_key_limits_and_malformed_responses(self):
        question = {"a": {"type": "noul", "instructions": "q"}}
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(jev.JevError):
            self.client().decide("s", question)
        with self.assertRaises(jev.JevError):
            self.client().decide("x" * (jev.MAX_STATE_CHARS + 1), question)
        with self.assertRaises(jev.JevError):
            self.client().decide("s", {str(i): question["a"] for i in range(9)})
        self.script = [{"nonsense": True}]
        with self.assertRaises(jev.JevError):
            self.client().decide("s", question)
        self.assertEqual(self.requests and len(self.requests), 1)

    def test_breaker_pauses_after_repeated_failures(self):
        now = [0.0]
        client = self.client(retries=0)
        client._clock = lambda: now[0]
        question = {"a": {"type": "noul", "instructions": "q"}}
        self.script = [http_error(502)] * jev.BREAKER_FAILURES
        for _ in range(jev.BREAKER_FAILURES):
            with self.assertRaises(jev.JevError):
                client.decide("s", question)
        sent = len(self.requests)
        with self.assertRaises(jev.JevError):
            client.decide("s", question)
        self.assertEqual(len(self.requests), sent)
        now[0] += jev.BREAKER_SECONDS + 1
        self.script = [answers(a={"type": "noul", "noul": 0})]
        client.decide("s", question)

    def test_import_and_construction_do_not_read_key_or_network(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(jev.JevSettings().mode, "off")
            jev.JevClient()


class AdviceTests(unittest.TestCase):
    def policy(self, verdict, mode="yolo", approver=None):
        calls = []
        def advisor(call):
            calls.append(call["name"])
            if isinstance(verdict, Exception):
                raise verdict
            return verdict
        policy = Policy(mode, approver, advisor=advisor)
        policy.consulted = calls
        return policy

    bash = {"name": "bash", "args": {"command": "echo hi"}}

    def test_denylist_precedes_advisor_and_allow_never_loosens(self):
        policy = self.policy("allow", mode="safe")
        self.assertEqual(policy.check({"name": "bash", "args": {"command": "sudo ls"}}),
                         "dangerous bash command denied by policy")
        self.assertEqual(policy.consulted, [])
        self.assertEqual(policy.check(self.bash), "safe mode requires approval for bash")

    def test_deny_blocks_even_in_yolo_and_ask_forces_approval(self):
        self.assertEqual(self.policy("deny").check(self.bash), DENIED)
        prompts = []
        def approve(call, reason):
            prompts.append(reason)
            return True
        policy = self.policy("ask", approver=approve)
        self.assertIsNone(policy.check(self.bash))
        self.assertEqual(prompts, ["advisor requests approval for bash"])
        self.assertEqual(self.policy("ask", approver=lambda c, r: False).check(self.bash),
                         "advisor requests approval for bash")

    def test_failures_and_unknown_answers_mean_no_advice(self):
        for verdict in (RuntimeError("down"), None, "maybe", True):
            self.assertIsNone(self.policy(verdict).check(self.bash))

    def test_read_tools_are_not_consulted_and_read_only_stays_strict(self):
        policy = self.policy("deny", mode="safe")
        self.assertIsNone(policy.check({"name": "read_file", "args": {"path": "a"}}))
        self.assertEqual(policy.consulted, [])
        read_only = self.policy("ask", mode="read-only", approver=lambda c, r: True)
        self.assertEqual(read_only.check(self.bash), "read-only mode permits only read_file, list_files, and grep")

    def test_settings_map_typed_answers_to_verdicts(self):
        def handler(state, questions):
            return self.reply
        settings = jev.JevSettings("permissions", client=jev.FakeJev(handler))
        cases = [(("allow", 0.1), "allow"), (("ask", 0.1), "ask"), (("deny", 0.0), "deny"),
                 (("allow", 0.9), "ask")]
        for (choice, risk), expected in cases:
            self.reply = {"decision": {"type": "choice", "choice": choice}, "secrets": {"type": "noul", "noul": risk}}
            self.assertEqual(settings.advise_call(self.bash), expected)
        self.reply = {"decision": {}}
        self.assertIsNone(settings.advise_call(self.bash))
        settings.set_mode("off")
        self.assertIsNone(settings.advise_call(self.bash))

    def test_secrets_are_masked_before_sending(self):
        seen = []
        settings = jev.JevSettings("permissions", client=jev.FakeJev(lambda s, q: seen.append(s) or {}))
        settings.advise_call({"name": "bash", "args": {"command": "API_KEY=abc123 curl -H 'Bearer tokentokentoken' x"}})
        self.assertNotIn("abc123", seen[0])
        self.assertNotIn("tokentokentoken", seen[0])


class MaskingTests(unittest.TestCase):
    def test_known_token_shapes_without_key_names_are_masked(self):
        secrets = ["ghp_sentinelBareToken0123456789abcdefABCD", "AKIAABCDEFGHIJKLMNOP", "xoxb-1234567890-abcdefghij",
                   "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZW50aW5lbCJ9.c2lnbmF0dXJlMTIzNDU2", "sk-sentinel-1234567890abcdef",
                   "github_pat_11ABCDEFG0123456789_abcdefghijklmnop"]
        for secret in secrets:
            self.assertNotIn(secret, jev.redact(f"result: {secret} end"), secret)
        self.assertEqual(jev.redact("pytest -q tests/test_x.py"), "pytest -q tests/test_x.py")

    def test_sample_canaries(self):
        path = Path(__file__).resolve().parent.parent / "demos/fixtures/jev_gold/samples/5_leak_canaries_sample.json"
        for canary in json.loads(path.read_text())["canaries"]:
            sent = "" if jev.PROTECTED.search(canary["text"]) else jev.redact(canary["text"])
            for secret in canary["must_not_appear"]:
                self.assertNotIn(secret, sent, canary["id"])


class ViewTests(unittest.TestCase):
    def messages(self, count=4):
        history = [{"role": "user", "text": "fix the failing test in parser"}]
        for index in range(count):
            history += [{"role": "assistant", "text": "", "tool_calls": [{"name": "grep", "args": {}, "call_id": f"c{index}"}],
                         "provider_output": [{"opaque": index}]},
                        {"role": "tool", "name": "grep", "call_id": f"c{index}", "text": f"line {index}\n" * 400}]
        return history + [{"role": "user", "text": "recent"}] * 5

    def settings(self, level, calls=None):
        def handler(state, questions):
            if calls is not None:
                calls.append(list(questions))
            return {label: {"type": "choice", "choice": level(label)} for label in questions}
        return jev.JevSettings("context", client=jev.FakeJev(handler))

    def test_projection_keeps_structure_and_original(self):
        history = self.messages()
        original = copy.deepcopy(history)
        view = self.settings(lambda label: "hide").view(history)
        self.assertEqual(history, original)
        self.assertEqual(len(view), len(history))
        self.assertTrue(view[2]["text"].startswith("[hidden by Jev visibility"))
        self.assertEqual({k: v for k, v in view[2].items() if k != "text"}, {k: v for k, v in history[2].items() if k != "text"})
        self.assertEqual([m.get("provider_output") for m in view], [m.get("provider_output") for m in history])
        self.assertEqual(view[-5:], history[-5:])

    def test_levels_cache_and_batching(self):
        calls = []
        settings = self.settings(lambda label: "short", calls)
        history = self.messages(count=10)
        first = settings.view(history)
        tools = [m for m in first if m.get("role") == "tool"]
        self.assertEqual(len(tools), 10)
        self.assertTrue(all("omitted by Jev visibility" in m["text"] for m in tools[:-1]))
        self.assertEqual(tools[-1], history[-6])  # inside the recent window, so untouched
        self.assertTrue(all(len(batch) <= jev.MAX_QUESTIONS for batch in calls))
        count = len(calls)
        settings.view(history)
        self.assertEqual(len(calls), count)
        history[-1] = {"role": "user", "text": "a different request"}
        settings.view(history)
        self.assertGreater(len(calls), count)

    def test_full_small_protected_and_failure_chunks_are_unchanged_and_unsent(self):
        sent = []
        history = self.messages()
        history[2]["text"] = "API_KEY=abc\n" * 200 + "\n.env\n"
        settings = jev.JevSettings("context", client=jev.FakeJev(
            lambda state, questions: sent.append(state) or {label: {"choice": "hide"} for label in questions}))
        view = settings.view(history)
        self.assertEqual(view[2], history[2])
        self.assertFalse(any("API_KEY" in state for state in sent))

        def broken(state, questions):
            raise jev.JevError("down")
        self.assertIs(jev.JevSettings("context", client=jev.FakeJev(broken)).view(history), history)
        self.assertIs(jev.JevSettings("off", client=jev.FakeJev(broken)).view(history), history)

    def test_loop_sends_projection_without_rewriting_history(self):
        history = [{"role": "user", "text": "task"}] + self.messages()[1:]
        sent = []
        class Backend:
            MAX_OUTPUT_TOKENS = 10
            def complete(self, model, system, messages, tools, **options):
                sent.append(messages)
                return {"text": "done", "tool_calls": []}
        settings = self.settings(lambda label: "hide")
        loop.run_loop("m", "s", history, {}, lambda *a: None, lambda call: None, backend=Backend(), view=settings.view)
        self.assertTrue(sent[0][2]["text"].startswith("[hidden"))
        self.assertTrue(history[2]["text"].startswith("line"))


class VisibilityTests(unittest.TestCase):
    """Every Jev API call is announced, then reported, like a tool call."""

    def settings(self, handler, mode="permissions"):
        settings = jev.JevSettings(mode, client=jev.FakeJev(handler))
        self.events = []
        settings.observer = lambda kind, payload: self.events.append((kind, payload))
        return settings

    def test_permission_call_emits_start_then_end_with_masked_payload(self):
        settings = self.settings(lambda s, q: {"decision": {"type": "choice", "choice": "ask"},
                                               "secrets": {"type": "noul", "noul": 0.82}})
        settings.advise_call({"name": "bash", "args": {"command": "API_KEY=hunter2hunter2 curl x"}})
        kinds = [kind for kind, _ in self.events]
        self.assertEqual(kinds, ["jev_start", "jev_end"])
        start, end = self.events[0][1], self.events[1][1]
        self.assertEqual((start["purpose"], start["id"] == end["id"]), ("permissions", True))
        self.assertIn("approval check for bash", start["detail"])
        self.assertNotIn("hunter2", start["sent"])
        self.assertIn("[redacted]", start["sent"])
        self.assertTrue(end["ok"])
        self.assertIn("advice: ask", end["summary"])
        self.assertIn("secrets=82%", end["summary"])
        self.assertIsInstance(end["input_tokens"], int)

    def test_failure_is_reported_and_still_falls_back(self):
        def broken(state, questions):
            raise jev.JevError("Jev request failed (HTTP 502).")
        settings = self.settings(broken)
        self.assertIsNone(settings.advise_call({"name": "bash", "args": {"command": "ls"}}))
        end = self.events[-1][1]
        self.assertEqual((self.events[-1][0], end["ok"]), ("jev_end", False))
        self.assertIn("HTTP 502", end["error"])

    def test_context_batches_are_announced_with_level_counts(self):
        settings = self.settings(lambda s, q: {label: {"type": "choice", "choice": "hide"} for label in q}, "context")
        history = ViewTests().messages(count=4)
        settings.view(history)
        starts = [p for k, p in self.events if k == "jev_start"]
        ends = [p for k, p in self.events if k == "jev_end"]
        self.assertEqual((len(starts), len(ends)), (1, 1))
        self.assertEqual(starts[0]["questions"], 3)
        self.assertIn("hide 3", ends[0]["summary"])
        count = len(self.events)
        settings.view(history)  # cached levels: no new API call, so nothing new is announced
        self.assertEqual(len(self.events), count)

    def test_display_errors_never_change_the_decision(self):
        settings = jev.JevSettings("permissions", client=jev.FakeJev(
            lambda s, q: {"decision": {"choice": "deny"}, "secrets": {"noul": 0.1}}))
        settings.observer = lambda kind, payload: 1 / 0
        self.assertEqual(settings.advise_call({"name": "bash", "args": {"command": "ls"}}), "deny")

    def test_harness_routes_events_to_its_display_and_plain_display_prints_them(self):
        from chiikawa.terminal import TerminalDisplay
        stream = io.StringIO()
        display = TerminalDisplay(stream=stream)
        settings = jev.JevSettings("permissions", client=jev.FakeJev(
            lambda s, q: {"decision": {"choice": "ask"}, "secrets": {"noul": 0.2}}))
        with tempfile.TemporaryDirectory() as root:
            harness = Harness(root, on_event=display, jev=settings, persist=False)
            self.assertEqual(harness.policy.check({"name": "bash", "args": {"command": "ls"}}),
                             "advisor requests approval for bash")
        text = stream.getvalue()
        self.assertIn("Calling Jev model", text)
        self.assertIn("offline stand-in", text)
        self.assertIn("approval check for bash", text)
        self.assertIn("Done: Jev answered", text)
        self.assertIn("[jev]", text)

    def test_transcript_display_prints_a_finished_jev_row(self):
        from chiikawa.transcript import TranscriptDisplay
        stream = io.StringIO()
        display = TranscriptDisplay(stream=stream, answer_stream=io.StringIO())
        display("jev_start", {"id": "a", "purpose": "permissions", "detail": "approval check for bash",
                              "host": "jevmodel.org", "model": "jev-latest", "questions": 2, "chars": 40, "sent": "x"})
        display("jev_end", {"id": "a", "purpose": "permissions", "detail": "approval check for bash", "ok": True,
                            "elapsed": 0.4, "input_tokens": 120, "output_tokens": 3, "summary": "advice: ask"})
        text = stream.getvalue()
        self.assertIn("Call Jev model [jevmodel.org]", text)
        self.assertIn("advice: ask · 120 input tokens", text)
        self.assertEqual(display.entries[-1]["call"]["name"], "jev")


class CommandTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.commands = Commands(Harness(self.root), lambda selected, model, **options: Harness(
            self.root, provider=selected, model=model, **options))

    def test_menu_completion_and_help(self):
        self.assertIn('/jev', [item[0] for item in self.commands.candidates('/j')])
        self.assertEqual([item[0] for item in self.commands.candidates('/jev ')],
                         ['/jev off', '/jev permissions', '/jev context', '/jev on'])
        self.assertIn('off (current)', [item[1] for item in self.commands.candidates('/jev o')])
        self.commands.handle('/help')
        self.assertIn('/jev [off|permissions|context|on]', self.output.getvalue())

    def test_on_without_key_stays_off_and_never_calls_out(self):
        with patch.object(jev.urllib.request, 'urlopen') as network:
            self.commands.handle('/jev on')
            self.commands.handle('/jev nonsense')
            self.commands.handle('/status')
        network.assert_not_called()
        self.assertEqual(self.commands.harness.jev.mode, 'off')
        self.assertIn('Jev stays off', self.output.getvalue())
        self.assertIn('Usage: /jev', self.output.getvalue())
        self.assertIn('Jev: off', self.output.getvalue())

    def test_choice_persists_across_new_and_applies_to_policy(self):
        os.environ[jev.KEY_ENV] = SENTINEL
        self.commands.handle('/jev permissions')
        self.assertEqual(self.commands.harness.jev.mode, 'permissions')
        self.assertIn(f'{jev.KEY_ENV}, environment only', self.output.getvalue())
        self.commands.handle('/new')
        self.assertEqual(self.commands.harness.jev.mode, 'permissions')
        self.assertIs(self.commands.harness.policy.advisor.__self__, self.commands.harness.jev)
        self.commands.handle('/jev off')
        self.assertEqual(self.commands.harness.jev.mode, 'off')

    def test_enterprise_profile_refuses(self):
        os.environ[jev.KEY_ENV] = SENTINEL
        with patch.object(Harness, 'profile', 'enterprise'):
            self.commands.handle('/jev on')
        self.assertEqual(self.commands.harness.jev.mode, 'off')
        self.assertIn('enterprise profile keeps it off', self.output.getvalue())

    def test_cli_flag_requires_key_and_selects_mode(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['-d', str(self.root), '--jev', 'on'])
        os.environ[jev.KEY_ENV] = SENTINEL
        with patch('builtins.input', side_effect=['/status', '/exit']), contextlib.redirect_stderr(io.StringIO()), \
             patch.object(provider, 'complete') as model:
            self.assertEqual(cli.main(['-d', str(self.root), '--jev', 'context']), 0)
        model.assert_not_called()
        self.assertIn('Jev: context', self.output.getvalue())


if __name__ == '__main__':
    unittest.main()
