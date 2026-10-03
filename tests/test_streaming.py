"""Foundry SSE, incremental terminal delivery, safe completion, and managed IPC."""

import contextlib
import io
import json
import os
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chiikawa import Harness, Policy, provider
from chiikawa.enterprise_client import EnterpriseClient
from chiikawa.enterprise_service import _Handler, _Server
from chiikawa.enterprise_gateway import ProviderGateway
from chiikawa.enterprise_transport import ApprovedHTTPS
from chiikawa.enterprise_policy import EnterprisePolicy
from test_enterprise_management import configuration
from chiikawa.terminal import TerminalDisplay
from chiikawa.transcript import TranscriptDisplay
import test_commands


def event(kind, **values):
    return ("event: " + kind + "\r\ndata: " + json.dumps({"type": kind, **values}, ensure_ascii=False) + "\r\n\r\n").encode()


def completed(text="Hello 世界", tools=()):
    return {"status": "completed", "output": [
        {"type": "reasoning", "encrypted_content": "PRIVATE", "summary": []},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]},
        *tools], "usage": {"input_tokens": 5, "output_tokens": 4}}


def response(data):
    stream = io.BytesIO(data)
    stream.headers = Message()
    stream.headers["Content-Type"] = "text/event-stream; charset=utf-8"
    return stream


class StreamingTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"AZURE_OPENAI_ENDPOINT": "https://foundry.example.test", "CHIIKAWA_API_KEY": "test-only"})
        env.start()
        self.addCleanup(env.stop)

    def test_deltas_completion_usage_and_opaque_replay(self):
        data = b": heartbeat\r\n\r\n" + event("response.output_text.delta", delta="Hello ")
        data += event("response.reasoning.delta", delta="HIDDEN")
        data += event("response.function_call_arguments.delta", delta="HIDDEN-ARGS")
        data += event("response.output_text.delta", delta="世界")
        data += event("response.completed", response=completed())
        chunks = []
        stream = response(data)
        with patch.object(provider.urllib.request, "urlopen", return_value=stream) as send:
            result = provider.complete("test-model", "system", [], [], on_delta=chunks.append)
        self.assertEqual(chunks, ["Hello ", "世界"])
        self.assertEqual(result["text"], "".join(chunks))
        self.assertEqual(result["usage"], {"input": 5, "output": 4})
        self.assertEqual(result["provider_output"][0]["encrypted_content"], "PRIVATE")
        self.assertTrue(stream.closed)
        self.assertTrue(json.loads(send.call_args.args[0].data)["stream"])
        self.assertEqual(send.call_args.args[0].get_header("Accept"), "text/event-stream")

    def test_sse_multiline_data_and_refusals(self):
        data = b'data: {"type": "response.refusal.delta",\ndata: "delta": "Cannot help"}\n\n'
        data += event("response.completed", response=completed("Cannot help"))
        chunks = []
        provider.read_stream(io.BytesIO(data), chunks.append)
        self.assertEqual(chunks, ["Cannot help"])

    def test_failures_never_retry_after_open_or_execute_partial_tools(self):
        tool = {"type": "function_call", "name": "write_file", "call_id": "w",
                "arguments": '{"path":"unsafe","content":"no"}'}
        for ending in (b"", b"data: [DONE]\n\n", event("response.failed", message="PRIVATE"),
                       event("response.incomplete"), b"data: not-json\n\n",
                       event("response.completed", response={"status": "incomplete"})):
            with self.subTest(ending=ending), tempfile.TemporaryDirectory() as root:
                data = event("response.output_item.done", item=tool) + event("response.output_text.delta", delta="partial") + ending
                events = []
                stream = response(data)
                agent = Harness(root, activity=True, policy=Policy("yolo"), on_event=lambda k, p: events.append((k, p)))
                with patch.object(provider.urllib.request, "urlopen", return_value=stream) as send:
                    with self.assertRaises((RuntimeError, ValueError)):
                        agent.run("task")
                send.assert_called_once()
                self.assertTrue(stream.closed)
                self.assertFalse((Path(root) / "unsafe").exists())
                self.assertFalse(any(m["role"] == "assistant" for m in agent.messages))
                self.assertTrue(any(k == "assistant_delta" for k, _ in events))
                self.assertNotIn("PRIVATE", str(events))

    def test_completed_tools_wait_for_last_event_and_are_journaled_once(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "saved"
            call = {"type": "function_call", "name": "write_file", "call_id": "w",
                    "arguments": '{"path":"saved","content":"ok"}'}
            first = response(event("response.output_text.delta", delta="Preparing") + event("response.completed", response=completed("Preparing", [call])))
            last = response(event("response.output_text.delta", delta="Done") + event("response.completed", response=completed("Done")))
            def observe(kind, payload):
                if kind == "assistant_delta" and payload["text"] == "Preparing":
                    self.assertFalse(target.exists())
            agent = Harness(root, activity=True, on_event=observe)
            with patch.object(provider.urllib.request, "urlopen", side_effect=[first, last]):
                self.assertEqual(agent.run("write"), "Done")
            self.assertEqual(target.read_text(), "ok")
            saved = [json.loads(line) for line in agent.session_path.read_text().splitlines()]
            self.assertEqual([m["text"] for m in saved if m["role"] == "assistant"], ["Preparing", "Done"])
            self.assertNotIn("assistant_delta", agent.session_path.read_text())

    def test_cancel_closes_stream_and_oversized_or_wrong_media_is_rejected(self):
        stream = response(event("response.output_text.delta", delta="one"))
        with patch.object(provider.urllib.request, "urlopen", return_value=stream) as send:
            with self.assertRaises(KeyboardInterrupt):
                provider.complete("model", "system", [], [], on_delta=Mock(side_effect=KeyboardInterrupt))
        self.assertTrue(stream.closed)
        send.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "size limit"):
            provider.read_stream(io.BytesIO(b"data: " + b"x" * 100 + b"\n\n"), Mock(), max_bytes=20)
        stream = response(b"{}")
        stream.headers.replace_header("Content-Type", "application/json")
        with patch.object(provider.urllib.request, "urlopen", return_value=stream):
            with self.assertRaisesRegex(RuntimeError, "SSE"):
                provider.complete("model", "system", [], [], on_delta=Mock())

    def test_display_stream_is_immediate_escaped_and_not_duplicated_on_stdout(self):
        for display_type in (TerminalDisplay, TranscriptDisplay):
            output, answer = io.StringIO(), io.StringIO()
            view = display_type(output, answer)
            with view.task("task"):
                view("model_start", {"model": "test"})
                view("assistant_delta", {"text": "Hello\x1b[2J"})
                self.assertIn("Hello\\x1b[2J", output.getvalue())
                self.assertEqual(answer.getvalue(), "")
                view("model_end", {"ok": True})
                view("assistant", {"text": "Hello\x1b[2J", "tool_calls": []})
            self.assertEqual(answer.getvalue(), "Hello\\x1b[2J\n")
            self.assertNotIn("\x1b", output.getvalue())

    def test_managed_stream_reserves_before_network_and_keeps_uncertain_charge(self):
        selected = SimpleNamespace(protocol='responses', max_output_tokens=20, count_endpoint='https://allowed.test/count',
                                   endpoint='https://allowed.test/responses')
        policy, ledger, transport = Mock(), Mock(), Mock()
        policy.allow_model.return_value = selected
        ledger.status.return_value = {'suspended': False, 'remaining': 1000}
        ledger.reserve.return_value = 'reservation'
        transport.json.return_value = {'input_tokens': 5}
        gateway = ProviderGateway(policy, ledger, '/unused', transport=transport)
        developer = SimpleNamespace(token_limit=1000)
        chunks = []
        def deliver(url, body, on_delta, **kwargs):
            ledger.reserve.assert_called_with(developer, 5, 20)
            self.assertTrue(body['stream'])
            self.assertNotIn('stream', transport.json.call_args.args[2])
            on_delta('Hello 世界')
            return completed()
        transport.stream_json.side_effect = deliver
        with patch.object(gateway, '_headers', return_value={'api-key': 'test-only'}):
            result = gateway.complete(developer, provider='foundry', model='model', messages=[], tools=[], on_delta=chunks.append)
        self.assertEqual(result['text'], 'Hello 世界')
        self.assertEqual(chunks, ['Hello 世界'])
        ledger.settle.assert_called_once_with('reservation', 5, 4)
        ledger.settle.reset_mock()
        transport.stream_json.side_effect = RuntimeError('incomplete stream')
        with patch.object(gateway, '_headers', return_value={'api-key': 'test-only'}):
            with self.assertRaises(RuntimeError):
                gateway.complete(developer, provider='foundry', model='model', messages=[], tools=[], on_delta=chunks.append)
        ledger.settle.assert_not_called()

    def test_managed_stream_cannot_bypass_destination_checks(self):
        transport = ApprovedHTTPS(EnterprisePolicy.parse(configuration()))
        with patch('socket.getaddrinfo') as dns:
            with self.assertRaises(PermissionError):
                transport.stream_json('https://unapproved.example.test/responses', {'stream': True}, Mock())
        dns.assert_not_called()


class IncrementalDeliveryTests(unittest.TestCase):
    def test_cli_receives_text_while_server_withholds_completion(self):
        """Use actual HTTP/SSE, CLI pipes, and flushes; no simulated typing."""
        release, arrived = threading.Event(), threading.Event()
        bodies = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                bodies.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(event('response.output_text.delta', delta='EARLY-DELTA'))
                self.wfile.flush()
                arrived.set()
                if not release.wait(8): return
                self.wfile.write(event('response.output_text.delta', delta='-FINISHED'))
                self.wfile.write(event('response.completed', response=completed('EARLY-DELTA-FINISHED')))
                self.wfile.flush()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for layout in ('compact', 'verbose'):
                release.clear(); arrived.clear()
                with tempfile.TemporaryDirectory() as root:
                    script = f"""from unittest.mock import patch
from chiikawa import cli, provider
with patch.object(provider, 'api_root', return_value='http://127.0.0.1:{server.server_port}'):
    raise SystemExit(cli.main(['-d', {root!r}, '-p', 'hello', '--display', {layout!r}]))
"""
                    env = dict(os.environ, CHIIKAWA_API_KEY='test-only', CHIIKAWA_PROVIDER='foundry', NO_COLOR='1')
                    process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
                    early = b''
                    try:
                        self.assertTrue(arrived.wait(5))
                        while b'EARLY-DELTA' not in early:
                            self.assertTrue(select.select([process.stderr], [], [], 5)[0])
                            block = os.read(process.stderr.fileno(), 65536)
                            self.assertTrue(block, early)
                            early += block
                        self.assertIsNone(process.poll())
                        self.assertFalse(select.select([process.stdout], [], [], 0)[0])
                        release.set()
                        stdout, stderr = process.communicate(timeout=8)
                        self.assertEqual(process.returncode, 0, stderr)
                        self.assertEqual(stdout, b'EARLY-DELTA-FINISHED\n')
                        self.assertTrue(bodies[-1]['stream'])
                    finally:
                        release.set()
                        if process.poll() is None: process.kill()
                        process.communicate(timeout=5)
        finally:
            release.set(); server.shutdown(); server.server_close(); thread.join(3)

    def test_managed_socket_flushes_deltas_before_final_result(self):
        release = threading.Event()
        class Authority:
            def dispatch(self, uid, request, on_delta=None):
                on_delta('MANAGED-EARLY')
                if not release.wait(5): raise RuntimeError('No incremental delivery')
                return {'text': 'MANAGED-EARLY', 'tool_calls': []}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service.sock'
            server = _Server(str(path), _Handler)
            server.authority = Authority()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            chunks = []
            def delta(text):
                chunks.append(text)
                release.set()
            try:
                with patch('chiikawa.enterprise_client.trusted_path'), patch('chiikawa.enterprise_client.ADMIN_UID', os.getuid()):
                    result = EnterpriseClient(path).request('model_stream', {}, on_delta=delta)
                self.assertEqual(chunks, ['MANAGED-EARLY'])
                self.assertEqual(result['text'], 'MANAGED-EARLY')
            finally:
                release.set(); server.shutdown(); server.server_close(); thread.join(3)


@unittest.skipUnless(os.name == 'posix', 'Requires a POSIX terminal')
class StreamingTerminalTests(unittest.TestCase):
    start = test_commands.TerminalTests.start
    cleanup_terminal = test_commands.TerminalTests.cleanup_terminal
    read_until = test_commands.TerminalTests.read_until
    send = test_commands.TerminalTests.send
    assert_restored = test_commands.TerminalTests.assert_restored

    def stream_script(self, root, layout):
        return f"""import time
from pathlib import Path
from unittest.mock import patch
from chiikawa import cli, provider
def complete(*args, on_delta=None, **kwargs):
    on_delta('LIVE-PARTIAL')
    deadline = time.monotonic() + 10
    while not Path({str(Path(root) / 'release')!r}).exists() and time.monotonic() < deadline:
        time.sleep(.02)
    on_delta('-COMPLETE')
    return {{'text': 'LIVE-PARTIAL-COMPLETE', 'tool_calls': []}}
with patch.object(provider, 'complete', side_effect=complete):
    raise SystemExit(cli.main(['-d', {root!r}, '--display', {layout!r}]))
"""

    def test_compact_live_preview_survives_history_then_clears(self):
        with tempfile.TemporaryDirectory() as root:
            self.start(script=self.stream_script(root, 'compact'))
            self.send('hello\r')
            self.read_until(b'LIVE-PARTIAL')
            self.send('\x14')
            history = self.read_until(b'ctrl+t / esc back')
            self.assertIn(b'LIVE-PARTIAL', history)
            self.send('\x14')
            self.read_until(b'\033[?1049l')
            Path(root, 'release').touch()
            self.read_until(b'chiikawa> ')
            self.send('/exit\r')
            self.assert_restored()
            log = next(Path(root, '.chiikawa/sessions').glob('*.jsonl')).read_text()
            self.assertEqual(log.count('LIVE-PARTIAL'), 1)
            self.assertIn('LIVE-PARTIAL-COMPLETE', log)

    def test_verbose_preview_precedes_completion(self):
        with tempfile.TemporaryDirectory() as root:
            self.start(script=self.stream_script(root, 'verbose'))
            self.send('hello\r')
            self.read_until(b'LIVE-PARTIAL')
            Path(root, 'release').touch()
            self.read_until(b'chiikawa> ')
            self.send('/exit\r')
            self.assert_restored()
