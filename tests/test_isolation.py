"""Isolation lifecycle tests without requiring a Docker installation."""
import copy
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chiikawa import Harness, provider, session
from chiikawa.commands import Commands
from chiikawa.runtime import JailRuntime, SandboxRuntime, protected_paths, validate_session
from chiikawa.security import Policy
from chiikawa.tools import Tool


class FakeSandbox(JailRuntime):
    def __init__(self, root, image, network):
        super().__init__(root)
        self.network = network

    def environment(self):
        return {'memory': '', 'catalog': ''}

    def tools(self):
        return [Tool('bash', {'schema': {}}, lambda **args: 'container')]


class IsolationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_atomic_switch_preserves_journal_policy_and_model(self):
        policy = Policy('read-only')
        h = Harness(self.root, policy=policy, model='chosen')
        with patch.object(provider, 'complete', return_value={'text': 'answer', 'tool_calls': []}):
            h.run('keep this task')
        original = copy.deepcopy(h.messages)
        path = h.session_path
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            self.assertTrue(h.set_isolation('sandbox'))
            self.assertFalse(h.set_isolation('sandbox'))
        self.assertEqual(h.messages[:-1], original)
        self.assertEqual(h.session_path, path)
        self.assertEqual(session.load(path), h.messages)
        self.assertIs(h.policy, policy)
        self.assertEqual(h.model, 'chosen')
        self.assertIn('"platform": "Linux"', h.system)
        self.assertEqual(h.tools['bash'].run(command='pwd'), 'container')
        self.assertTrue(h.set_isolation('jail'))
        self.assertIn(str(self.root), h.tools['bash'].run(command='pwd'))
        self.assertEqual(len(h.messages), len(original) + 2)

    def test_activation_failure_retains_runtime_history_and_disk(self):
        h = Harness(self.root)
        with patch.object(provider, 'complete', return_value={'text': 'ok', 'tool_calls': []}):
            h.run('hello')
        original, tools, system = copy.deepcopy(h.messages), h.tools, h.system
        disk = h.session_path.read_bytes()
        with patch('chiikawa.harness.SandboxRuntime', side_effect=RuntimeError('daemon unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'daemon unavailable'):
                h.set_isolation('sandbox')
        self.assertEqual(h.isolation, 'jail')
        self.assertEqual(h.messages, original)
        self.assertEqual(h.session_path.read_bytes(), disk)
        self.assertIs(h.tools, tools)
        self.assertEqual(h.system, system)

    def test_changes_only_between_runs_and_lock_released_after_error(self):
        h = Harness(self.root)
        def complete(*args, **kwargs):
            with self.assertRaisesRegex(RuntimeError, 'between runs'):
                h.set_isolation('jail')
            with self.assertRaisesRegex(RuntimeError, 'Only one run'):
                h.run('nested')
            raise RuntimeError('provider failure')
        with patch.object(provider, 'complete', side_effect=complete):
            with self.assertRaisesRegex(RuntimeError, 'provider failure'):
                h.run('task')
        self.assertFalse(h.set_isolation('jail'))

    def test_resume_defaults_to_jail_and_records_current_environment(self):
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            h = Harness(self.root)
            h.set_isolation('sandbox')
        resumed = Harness(self.root)
        self.assertTrue(resumed.resume())
        self.assertEqual(resumed.isolation, 'jail')
        self.assertEqual(resumed.messages[-1]['isolation'], 'jail')
        self.assertIn('on resume', resumed.messages[-1]['text'])

    def test_sandbox_start_resume_and_openrouter_notice_only_session(self):
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            h = Harness(self.root, isolation='sandbox')
            with patch.object(provider, 'complete', return_value={'text': 'ok', 'tool_calls': []}):
                h.run('sandbox startup')
            resumed = Harness(self.root)
            self.assertTrue(resumed.resume(h.session_path))
            self.assertEqual(resumed.messages[-1]['isolation'], 'jail')
            router = Harness(self.root, provider='openrouter', model='vendor/chosen')
            router.set_isolation('sandbox')
            restored = Harness(self.root, provider='openrouter', model='vendor/chosen')
            self.assertTrue(restored.resume(router.session_path))
            self.assertEqual(restored.model, 'vendor/chosen')

    def test_worker_validation_and_journal_failures_are_atomic(self):
        h = Harness(self.root)
        original_tools = h.tools
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            with patch.object(FakeSandbox, 'environment', side_effect=RuntimeError('invalid worker')):
                with self.assertRaisesRegex(RuntimeError, 'invalid worker'):
                    h.set_isolation('sandbox')
            with patch.object(session, 'append', side_effect=OSError('disk full')):
                with self.assertRaisesRegex(OSError, 'disk full'):
                    h.set_isolation('sandbox')
        self.assertEqual(h.messages, [])
        self.assertEqual(h.isolation, 'jail')
        self.assertIs(h.tools, original_tools)

    def test_commands_inherit_mode_and_network_without_host_prompt_reads(self):
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            h = Harness(self.root, sandbox_network='allow', sandbox_image='custom')
            c = Commands(h, lambda p, m, **options: Harness(self.root, provider=p, model=m, **options))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                c.handle('/sandbox')
                with patch('chiikawa.runtime.memory.read_memory', side_effect=AssertionError('host read')):
                    for command in ('/new', '/model next', '/provider openrouter'):
                        c.handle(command)
                        self.assertEqual(c.harness.isolation, 'sandbox')
                        self.assertEqual(c.harness.sandbox_network, 'allow')
                        self.assertEqual(c.harness.sandbox_image, 'custom')
                        self.assertIs(c.harness.policy, h.policy)
                c.handle('/status')
                c.handle('/jail')
            self.assertIn('Isolation: Sandbox', output.getvalue())
            self.assertIn('Network: allow', output.getvalue())
            self.assertEqual(c.harness.isolation, 'jail')
            self.assertEqual([x[0] for x in c.candidates('/sand')], ['/sandbox'])

    def test_host_extras_and_unsafe_sessions_rejected_before_docker(self):
        h = Harness(self.root, extra_tools=[Tool('custom', {}, lambda: 'host')])
        with patch('chiikawa.harness.SandboxRuntime') as runtime:
            with self.assertRaisesRegex(ValueError, 'extra_tools'):
                h.set_isolation('sandbox')
            runtime.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'sessions'):
            validate_session(self.root, self.root / 'custom.jsonl')
        (self.root / '.chiikawa').symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            validate_session(self.root)

    def test_host_home_and_remote_daemons_rejected(self):
        with patch('chiikawa.runtime.docker') as docker:
            with self.assertRaisesRegex(ValueError, 'host home'):
                SandboxRuntime(Path.home())
            docker.assert_not_called()
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://remote:2375', 'DOCKER_CONTEXT': ''}), \
             patch('chiikawa.runtime.docker') as docker:
            with self.assertRaisesRegex(ValueError, 'local Docker'):
                SandboxRuntime(self.root)
            docker.assert_not_called()

    def test_masks_templates_links_and_special_files(self):
        for name in ('.env', '.env.production', '.env.example', '.env.local.sample', 'credential.md'):
            (self.root / name).touch()
        (self.root / '.aws').mkdir()
        masked = {p.name for p, _ in protected_paths(self.root)}
        self.assertEqual(masked, {'.env', '.env.production', 'credential.md', '.aws'})
        (self.root / '.env').unlink()
        (self.root / '.env').symlink_to('credential.md')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            protected_paths(self.root)
        (self.root / '.env').unlink()
        os.link(self.root / 'credential.md', self.root / 'alias')
        with self.assertRaisesRegex(ValueError, 'hard-linked'):
            protected_paths(self.root)

    def test_child_inherits_isolation_after_switch(self):
        h = Harness(self.root)
        with patch('chiikawa.harness.SandboxRuntime', FakeSandbox):
            h.set_isolation('sandbox')
            def complete(model, system, messages, tools):
                self.assertIn('"platform": "Linux"', system)
                self.assertEqual(messages, [{'role': 'user', 'text': 'child', 'isolation': 'sandbox', 'profile': 'standard'}])
                return {'text': 'child report', 'tool_calls': []}
            with patch.object(provider, 'complete', side_effect=complete):
                self.assertEqual(h.tools['spawn_agent'].run(task='child'), 'child report')
        self.assertEqual(len(list((self.root / '.chiikawa/sessions').glob('*.jsonl'))), 1)


if __name__ == '__main__':
    unittest.main()
