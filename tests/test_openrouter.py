"""Exercise provider isolation, actual tool dispatch, and durable native replay."""

import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from chiikawa import Harness, cli, openrouter, provider, providers, session


def response(text="done", calls=None, **fields):
    message = {"role": "assistant", "content": text, **fields}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": message}],
            "usage": {"prompt_tokens": 25, "completion_tokens": 12}}


def call(identifier="call_1", name="write_file", **args):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": json.dumps(args or {"path": "a.txt", "content": "hello"})}}


class OpenRouterTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def complete(self, value, **kwargs):
        with patch.object(openrouter, "_post", return_value=value):
            return openrouter.complete(openrouter.DEFAULT_MODEL, "system", [], [], **kwargs)

    def test_explicit_and_environment_provider_selection(self):
        os.environ.update(OPENROUTER_API_KEY="router", CHIIKAWA_MODEL="deployment")
        agent = Harness(self.root)
        self.assertIs(agent.backend, provider)
        self.assertEqual(agent.model, "deployment")
        self.assertEqual(agent.max_output_tokens, 65536)
        os.environ["CHIIKAWA_PROVIDER"] = "openrouter"
        agent = Harness(self.root)
        self.assertIs(agent.backend, openrouter)
        self.assertEqual(agent.model, "openai/gpt-5.4")
        self.assertEqual(agent.max_output_tokens, 16384)
        self.assertIs(providers.select("foundry"), provider)
        os.environ["OPENROUTER_MODEL"] = "anthropic/claude-sonnet-4.6"
        self.assertEqual(Harness(self.root).model, os.environ["OPENROUTER_MODEL"])
        with self.assertRaisesRegex(ValueError, "provider"):
            providers.select("other")

    def test_http_contract_and_credentials_do_not_cross(self):
        os.environ.update(OPENROUTER_API_KEY="router-only", AZURE_OPENAI_API_KEY="foundry-only",
                          AZURE_OPENAI_ENDPOINT="https://example.openai.azure.com")
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response()).encode())) as send:
            result = openrouter.complete(openrouter.DEFAULT_MODEL, "system", [{"role": "user", "text": "hi"}],
                                         [Harness(self.root).tools["read_file"].spec], reasoning_summary=True,
                                         max_output_tokens=2222)
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, openrouter.API_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer router-only")
        self.assertIsNone(request.get_header("Api-key"))
        self.assertNotIn(b"foundry-only", request.data)
        body = json.loads(request.data)
        self.assertEqual(body["messages"], [{"role": "system", "content": "system"}, {"role": "user", "content": "hi"}])
        self.assertEqual(body["tools"][0]["function"]["name"], "read_file")
        self.assertEqual(body["max_tokens"], 2222)
        self.assertEqual(body["reasoning"], {"effort": "medium", "exclude": False})
        self.assertEqual(result["usage"], {"input": 25, "output": 12})
        foundry_response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "ok"}]}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(foundry_response).encode())) as send:
            provider.complete("deployment", "system", [], [])
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Api-key"), "foundry-only")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(json.loads(request.data)["max_output_tokens"], 65536)

    def test_no_key_fallback_or_network_for_invalid_model(self):
        os.environ.update(CHIIKAWA_API_KEY="shared", AZURE_OPENAI_API_KEY="azure")
        with patch("urllib.request.urlopen") as send:
            with self.assertRaisesRegex(RuntimeError, "OPENROUTER_API_KEY"):
                openrouter.complete(openrouter.DEFAULT_MODEL, "", [], [])
            with self.assertRaisesRegex(ValueError, "qualified"):
                openrouter.complete("gpt-6-astra", "", [], [])
            send.assert_not_called()

    def test_retry_and_error_labels(self):
        os.environ["OPENROUTER_API_KEY"] = "router"
        for code in (429, 500, 504):
            with self.subTest(code=code), patch("chiikawa.provider.time.sleep") as sleep:
                error = urllib.error.HTTPError(openrouter.API_URL, code, "retry", {}, io.BytesIO(b"retry"))
                with patch("urllib.request.urlopen", side_effect=[error, io.BytesIO(json.dumps(response()).encode())]) as send:
                    self.assertEqual(openrouter.complete(openrouter.DEFAULT_MODEL, "", [], [])["text"], "done")
                self.assertEqual(send.call_count, 2)
                sleep.assert_called_once_with(2)
        error = urllib.error.HTTPError(openrouter.API_URL, 401, "bad key", {}, io.BytesIO(b"unauthorized"))
        with patch("urllib.request.urlopen", side_effect=error), patch("chiikawa.provider.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "OpenRouter HTTP 401"):
                openrouter.complete(openrouter.DEFAULT_MODEL, "", [], [])
            sleep.assert_not_called()

    def test_real_tools_native_replay_and_public_summary(self):
        details = [{"type": "reasoning.text", "text": "PRIVATE RAW"},
                   {"type": "reasoning.encrypted", "data": "PRIVATE ENCRYPTED"},
                   {"type": "reasoning.summary", "summary": "I will write two files."}]
        first = response(None, [call("a"), call("b", path="b.txt", content="world")], reasoning_details=details)
        bodies, events = [], []
        replies = iter([first, response("written")])
        def send(body):
            bodies.append(copy.deepcopy(body))
            return next(replies)
        with patch.object(openrouter, "_post", side_effect=send), patch.object(provider, "complete") as foundry:
            agent = Harness(self.root, provider="openrouter", activity=True,
                            on_event=lambda kind, data: events.append((kind, data)))
            self.assertEqual(agent.run("write files"), "written")
            foundry.assert_not_called()
        self.assertEqual((self.root / "a.txt").read_text(), "hello")
        self.assertEqual((self.root / "b.txt").read_text(), "world")
        wire = bodies[1]["messages"]
        self.assertEqual(wire[2]["reasoning_details"], details)
        self.assertEqual([m["tool_call_id"] for m in wire if m["role"] == "tool"], ["a", "b"])
        self.assertEqual([data["text"] for kind, data in events if kind == "reasoning"], ["I will write two files."])
        self.assertEqual(session.load(agent.session_path)[1]["openrouter_message"]["reasoning_details"], details)
        self.assertEqual([data["max_output_tokens"] for kind, data in events if kind == "usage"], [16384, 16384])

    def test_private_reasoning_not_exposed_as_summary(self):
        result = self.complete(response("answer", reasoning="raw", reasoning_details=[{"type": "reasoning.text", "text": "raw"}]))
        self.assertNotIn("reasoning_summary", result)
        self.assertEqual(result["openrouter_message"]["reasoning"], "raw")

    def test_malformed_or_incomplete_responses_never_execute_tools(self):
        invalid = [response(None, [call(), call()]), response(None, [call()]), response(None, [call()]),
                   response(None, [call()]), response(None), response([None]), {"error": {"message": "unavailable"}}]
        invalid[1]["choices"][0]["finish_reason"] = "length"
        invalid[2]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "[]"
        invalid[3]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
        for value in invalid:
            with self.subTest(value=value), patch.object(openrouter, "_post", return_value=value):
                with self.assertRaises(RuntimeError):
                    Harness(self.root, provider="openrouter", persist=False).run("write")
                self.assertFalse((self.root / "a.txt").exists())

    def test_missing_usage_and_refusal(self):
        value = response("", refusal="I cannot do that.")
        del value["usage"]
        result = self.complete(value)
        self.assertEqual(result["text"], "I cannot do that.")
        self.assertEqual(result["openrouter_message"]["content"], result["text"])
        self.assertEqual(result["usage"], {"input": None, "output": None})

    def test_resume_repairs_native_calls_and_restores_model(self):
        path = session.new_session(self.root)
        model = "anthropic/claude-sonnet-4.6"
        session.append(path, {"role": "user", "text": "write", "provider": "openrouter", "model": model})
        result = self.complete(response(None, [call()], reasoning_details=[{"type": "reasoning.encrypted", "data": "opaque"}]))
        session.append(path, {"role": "assistant", **result})
        agent = Harness(self.root, provider="openrouter")
        self.assertTrue(agent.resume(path))
        self.assertEqual(agent.model, model)
        wire = openrouter._to_wire(agent.messages)
        self.assertEqual(wire[-1], {"role": "tool", "tool_call_id": "call_1", "content": session.INTERRUPTED})
        self.assertEqual(wire[-2]["reasoning_details"], result["openrouter_message"]["reasoning_details"])
        self.assertEqual(len(session._read(path)), 3)
        self.assertFalse((self.root / "a.txt").exists())

    def test_provider_and_model_mismatch_leave_journal_untouched(self):
        for saved, chosen in (("foundry", "openrouter"), ("openrouter", "foundry"), ("openrouter", "openrouter")):
            path = session.new_session(self.root)
            message = {"role": "user", "text": "pending"}
            if saved == "openrouter":
                message.update(provider=saved, model="anthropic/claude-sonnet-4.6")
            session.append(path, message)
            before = path.read_bytes()
            agent = Harness(self.root, provider=chosen, model="openai/gpt-5.4")
            with self.assertRaisesRegex(RuntimeError, "session uses"):
                agent.resume(path)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(agent.messages, [])
        with self.assertRaises(ValueError):
            openrouter._to_wire([{"role": "assistant", "provider_output": []}])
        with self.assertRaises(ValueError):
            provider._to_wire([{"role": "assistant", "openrouter_message": {}}])

    def test_compaction_and_wrapup_route_to_openrouter(self):
        agent = Harness(self.root, provider="openrouter", max_turns=0, budget_tokens=1,
                        max_output_tokens=999, activity=True, persist=False)
        agent.messages = [{"role": "user" if i % 2 == 0 else "assistant", "text": "long " * 30} for i in range(8)]
        with patch.object(openrouter, "_post", side_effect=[response("summary"), response("wrapped")]) as send, patch.object(provider, "complete") as foundry:
            self.assertEqual(agent.run("continue"), "wrapped")
        foundry.assert_not_called()
        self.assertEqual(send.call_count, 2)
        for args in send.call_args_list:
            body = args.args[0]
            self.assertEqual(body["model"], "openai/gpt-5.4")
            self.assertEqual(body["max_tokens"], 999)
            self.assertNotIn("tools", body)
        self.assertIn("compacted", agent.messages[0]["text"])

    def test_child_inherits_provider_model_and_output_limit(self):
        agent = Harness(self.root, provider="openrouter", model="anthropic/claude-sonnet-4.6", max_output_tokens=777)
        with patch.object(openrouter, "_post", return_value=response("child done")) as send, patch.object(provider, "complete") as foundry:
            self.assertEqual(agent.tools["spawn_agent"].run(task="inspect"), "child done")
        foundry.assert_not_called()
        self.assertEqual(send.call_args.args[0]["model"], agent.model)
        self.assertEqual(send.call_args.args[0]["max_tokens"], 777)
        self.assertFalse((self.root / ".chiikawa").exists())

    def test_output_limit_validation(self):
        for value in (0, -1, "5"):
            with self.assertRaises(ValueError):
                Harness(self.root, max_output_tokens=value)

    def test_cli_routing_limits_and_private_terminal_state(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        answer = response("answer", reasoning="PRIVATE RAW", reasoning_details=[
            {"type": "reasoning.encrypted", "data": "PRIVATE ENCRYPTED"},
            {"type": "reasoning.summary", "summary": "Public summary"}])
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch.object(openrouter, "_post", return_value=answer) as send:
            status = cli.main(["--provider", "openrouter", "-d", str(self.root), "-p", "hello",
                               "--max-output-tokens", "2048"])
        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "answer\n")
        self.assertIn("Public summary", stderr.getvalue())
        self.assertIn("2,048", stderr.getvalue())
        self.assertNotIn("PRIVATE", stdout.getvalue() + stderr.getvalue())
        self.assertEqual(send.call_args.args[0]["max_tokens"], 2048)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
             patch.object(openrouter, "_post", return_value=response()) as send:
            self.assertEqual(cli.main(["--provider", "openrouter", "-d", str(self.root),
                                       "-p", "hi", "--no-reasoning"]), 0)
        self.assertNotIn("reasoning", send.call_args.args[0])

    def test_cli_banner_and_invalid_cap(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
             patch("builtins.input", side_effect=EOFError):
            self.assertEqual(cli.main(["--provider", "openrouter", "-d", str(self.root)]), 0)
        self.assertIn("provider: openrouter", stdout.getvalue())
        self.assertIn("16,384", stderr.getvalue())
        with contextlib.redirect_stderr(io.StringIO()), patch.object(cli, "Harness") as factory:
            with self.assertRaises(SystemExit) as error:
                cli.main(["--max-output-tokens", "0"])
        self.assertEqual(error.exception.code, 2)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
