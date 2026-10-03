"""Local command semantics and end-to-end slash menus in an actual pseudo-terminal."""

import contextlib
import io
import json
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from chiikawa import Harness, cli, openrouter, provider, session
from chiikawa.commands import Commands


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

    def test_discovery_status_and_unknown_commands_are_local(self):
        with patch.object(provider, 'complete') as request:
            self.assertTrue(self.commands.handle('/'))
            self.assertTrue(self.commands.handle('/status'))
            self.assertTrue(self.commands.handle('/typo'))
            self.assertFalse(self.commands.handle('ordinary task'))
        request.assert_not_called()
        for name in ('/model', '/provider', '/status', '/new', '/help', '/exit', '/sandbox', '/jail'):
            self.assertIn(name, self.output.getvalue())
        self.assertIn('Provider: foundry', self.output.getvalue())
        self.assertIn('Output limit: 65,536', self.output.getvalue())
        self.assertIn('Unknown command: /typo', self.output.getvalue())
        self.assertFalse((self.root / '.chiikawa').exists())

    def test_completion_and_trailing_slash_alias(self):
        self.assertEqual([item[0] for item in self.commands.candidates('/pro')], ['/provider'])
        self.assertEqual([item[0] for item in self.commands.candidates('/provider o')], ['/provider openrouter'])
        self.commands.handle('/provider/ openrouter')
        self.assertEqual(self.commands.harness.provider_name, 'openrouter')
        self.assertEqual(self.commands.harness.model, 'openai/gpt-5.4')
        self.assertEqual(self.commands.harness.max_output_tokens, 16384)
        self.assertEqual(self.commands.candidates('/unrecognized'), [])
        self.assertEqual(self.commands.candidates('hello /'), [])
        self.assertIn('/model openai/gpt-5.4', [item[0] for item in self.commands.candidates('/model ')])

    def test_invalid_commands_and_same_configuration_preserve_session(self):
        old = self.commands.harness
        for command in ('/provider unknown', '/model two words', '/status extra', '/new extra', '/exit now',
                        '/provider foundry', '/model gpt-6-astra', '/model', '/provider'):
            self.assertTrue(self.commands.handle(command))
            self.assertIs(self.commands.harness, old)
        self.assertFalse(self.commands.exiting)
        self.commands.handle('/provider openrouter')
        old = self.commands.harness
        self.commands.handle('/model unqualified')
        self.assertIs(self.commands.harness, old)

    def test_new_conversation_preserves_previous_journal_and_configuration(self):
        with patch.object(provider, 'complete', return_value={'text': 'answer', 'tool_calls': []}):
            self.commands.harness.run('task')
        previous = self.commands.harness.session_path
        original = previous.read_bytes()
        self.commands.handle('/model new-deployment')
        self.assertEqual(self.commands.harness.model, 'new-deployment')
        self.assertEqual(self.commands.harness.messages, [])
        self.assertIsNone(self.commands.harness.session_path)
        self.assertEqual(previous.read_bytes(), original)
        with patch.object(provider, 'complete', return_value={'text': 'second', 'tool_calls': []}):
            self.commands.harness.run('new task')
        self.assertNotEqual(self.commands.harness.session_path, previous)
        self.commands.handle('/new')
        self.assertEqual(self.commands.harness.model, 'new-deployment')
        self.assertEqual(self.commands.harness.messages, [])
        self.assertIn(str(previous), self.output.getvalue())

    def test_provider_environment_and_atomic_failure(self):
        os.environ['OPENROUTER_MODEL'] = 'anthropic/claude-sonnet-4.6'
        self.commands.handle('/provider openrouter')
        self.assertEqual(self.commands.harness.model, os.environ['OPENROUTER_MODEL'])
        old = self.commands.harness
        def fail(*args, **kwargs):
            raise RuntimeError('construction failed')
        self.commands.make_harness = fail
        self.commands.handle('/new')
        self.assertIs(self.commands.harness, old)
        self.assertIn('construction failed', self.output.getvalue())

    def test_cli_switches_actual_requests_and_preserves_settings(self):
        foundry_models, router_models = [], []
        def foundry(model, *args, **kwargs):
            foundry_models.append((model, kwargs))
            return {'text': 'foundry answer', 'tool_calls': []}
        def router(model, *args, **kwargs):
            router_models.append((model, kwargs))
            return {'text': 'router answer', 'tool_calls': []}
        tasks = ['first task', '/provider openrouter', 'second task', '/model vendor/custom-model',
                 'third task', '/status', '/new', '/status', '/exit']
        with patch('builtins.input', side_effect=tasks), contextlib.redirect_stderr(io.StringIO()), \
             patch.object(provider, 'complete', side_effect=foundry), patch.object(openrouter, 'complete', side_effect=router):
            status = cli.main(['-d', str(self.root), '--mode', 'read-only', '--max-output-tokens', '555',
                               '--context-threshold', '1000', '--max-turns', '2', '--no-reasoning'])
        self.assertEqual(status, 0)
        self.assertEqual([item[0] for item in foundry_models], ['gpt-6-astra'])
        self.assertEqual([item[0] for item in router_models], ['openai/gpt-5.4', 'vendor/custom-model'])
        for _, kwargs in foundry_models + router_models:
            self.assertEqual(kwargs['max_output_tokens'], 555)
            self.assertNotIn('reasoning_summary', kwargs)
        for text in ('Mode: read-only', 'Output limit: 555', '/ 1,000 tokens', 'Turn limit: 2'):
            self.assertIn(text, self.output.getvalue())
        logs = list((self.root / '.chiikawa/sessions').glob('*.jsonl'))
        self.assertEqual(len(logs), 3)
        self.assertTrue(all(len(session.load(path)) == 2 for path in logs))
        self.assertFalse(any('"text": "/' in path.read_text() for path in logs))

    def test_plain_terminal_commands_and_headless_literal_slash(self):
        with patch('builtins.input', side_effect=['/', '/status', '/provider/ openrouter', '/exit']), \
             contextlib.redirect_stderr(io.StringIO()), patch.object(provider, 'complete') as foundry, \
             patch.object(openrouter, 'complete') as router:
            self.assertEqual(cli.main(['-d', str(self.root)]), 0)
        foundry.assert_not_called()
        router.assert_not_called()
        self.assertNotIn('\033', self.output.getvalue())
        with patch.object(provider, 'complete', return_value={'text': 'answer', 'tool_calls': []}), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['-d', str(self.root), '-p', '/status']), 0)
        log = next((self.root / '.chiikawa/sessions').glob('*.jsonl'))
        self.assertEqual(session.load(log)[0]['text'], '/status')


@unittest.skipUnless(os.name == 'posix', 'Interactive editor requires POSIX')
class TerminalTests(unittest.TestCase):
    def start(self, script=None, width=90, height=24):
        import fcntl
        import pty
        import termios
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.master, self.slave = pty.openpty()
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
        self.original_settings = termios.tcgetattr(self.slave)
        env = {key: value for key, value in os.environ.items() if key not in {
            'CHIIKAWA_PROVIDER', 'CHIIKAWA_MODEL', 'OPENROUTER_MODEL', 'OPENROUTER_API_KEY',
            'AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'CHIIKAWA_API_KEY'}}
        env.update(TERM='xterm-256color', NO_COLOR='1')
        args = [sys.executable, '-c', script] if script else [sys.executable, '-m', 'chiikawa', '-d', str(self.root)]
        self.process = subprocess.Popen(args, stdin=self.slave, stdout=self.slave, stderr=self.slave,
                                        env=env, start_new_session=True)
        self.addCleanup(self.cleanup_terminal)
        self.read_until(b'chiikawa> ')

    def cleanup_terminal(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=3)
        os.close(self.master)
        os.close(self.slave)

    def read_until(self, needle, timeout=5):
        output = b''
        deadline = time.monotonic() + timeout
        while needle not in output and time.monotonic() < deadline:
            if select.select([self.master], [], [], .05)[0]:
                output += os.read(self.master, 65536)
        self.assertIn(needle, output, repr(output))
        return output

    def send(self, value):
        os.write(self.master, value.encode() if isinstance(value, str) else value)

    def assert_restored(self, expected=0):
        import termios
        deadline, tail = time.monotonic() + 5, b''
        while self.process.poll() is None and time.monotonic() < deadline:
            if select.select([self.master], [], [], .05)[0]:
                tail += os.read(self.master, 65536)
        self.assertIsNotNone(self.process.poll(), repr(tail))
        self.assertEqual(self.process.returncode, expected, repr(tail))
        restored = termios.tcgetattr(self.slave)
        # BSD sets PENDIN when restoring canonical input; it is transient kernel state.
        restored[3] &= ~getattr(termios, 'PENDIN', 0)
        self.original_settings[3] &= ~getattr(termios, 'PENDIN', 0)
        self.assertEqual(restored, self.original_settings)

    def test_immediate_menu_filter_tab_provider_and_model_pickers(self):
        self.start()
        self.send('/')  # Deliberately no Enter: every command must already be visible.
        output = self.read_until(b'Esc dismiss')
        for command in (b'/model', b'/provider', b'/status', b'/new', b'/help', b'/exit', b'/sandbox', b'/jail'):
            self.assertIn(command, output)
        self.send('pro')
        self.read_until(b'chiikawa> /pro')
        self.send('\t')
        self.read_until(b'foundry (current)')
        self.send('\x1b[B\r')
        self.read_until(b'New conversation')
        self.send('/status\r')
        self.read_until(b'Model: openai/gpt-5.4')
        self.send('/model\r')
        self.read_until(b'openai/gpt-5.4 (current)')
        self.send('\x15/model vendor/custom\r')
        self.read_until(b'model: vendor/custom')
        self.send('/exit\r')
        self.assert_restored()
        self.assertFalse((self.root / '.chiikawa').exists())

    def test_arrow_selection_escape_and_eof_restore_terminal(self):
        self.start()
        self.send('/\x1b[B\x1b[B\r')
        self.read_until(b'Provider: foundry')
        self.send('/')
        self.read_until(b'Esc dismiss')
        self.send('\x1b')
        self.read_until(b'chiikawa> /')
        self.send('\x7f\x04')
        self.assert_restored()

    def test_interrupt_restores_terminal(self):
        self.start()
        self.send('/')
        self.read_until(b'Esc dismiss')
        self.process.send_signal(signal.SIGINT)
        self.read_until(b'Interrupted.')
        self.assert_restored(130)

    def test_unicode_editing_bracketed_paste_and_history(self):
        script = """import json
from chiikawa.prompt import Prompt
p = Prompt(lambda text: [])
for i in range(3):
    print('RESULT:' + json.dumps(p.read(), ensure_ascii=False), flush=True)
"""
        self.start(script=script, width=32, height=12)
        self.send('猫ab\x1b[D\x7fZ\r')
        self.read_until('RESULT:"猫Zb"'.encode())
        self.send('\x1b[A\r')
        self.read_until('RESULT:"猫Zb"'.encode())
        self.send('\x1b[200~hello\n/status\x1b[201~')
        self.read_until(b'/status')
        self.assertIsNone(self.process.poll(), 'Pasted newline submitted the prompt')
        self.send('\r')
        self.read_until(b'RESULT:"hello /status"')
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
