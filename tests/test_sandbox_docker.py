"""Real boundary tests. Required in Linux CI; opt in locally with CHIIKAWA_TEST_DOCKER=1."""
import os
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from chiikawa import Harness, provider
from chiikawa.runtime import SandboxRuntime, docker
import test_commands


@unittest.skipUnless(os.environ.get('CHIIKAWA_TEST_DOCKER') == '1', 'Set CHIIKAWA_TEST_DOCKER=1 for real Docker tests')
class DockerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='chiikawa-integration-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.runtime = SandboxRuntime(self.root)

    def bash(self, command, **args):
        return self.runtime.call('bash', dict(command=command, **args))

    def test_boundary_credentials_protected_paths_and_tools(self):
        for name in ('.env', '.env.production', 'credential.md', 'nested/.env.local', '.aws/config',
                     '.ssh/id_rsa', '.codex/auth.json', '.agents/secret', '.chiikawa/secret'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('PRIVATE_SENTINEL')
        (self.root / '.env.example').write_text('PUBLIC_TEMPLATE')
        (self.root / '.git').mkdir()
        (self.root / '.git/config').write_text('original')
        outside = self.root.parent / ('outside-' + self.root.name)
        outside.write_text('HOST_ONLY')
        (self.root / 'outside-link').symlink_to(outside)
        self.addCleanup(outside.unlink)
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'HOST_API_SECRET', 'SSH_AUTH_SOCK': '/host/agent.sock'}):
            output = self.bash('env; id; cat /proc/self/status; cat /proc/self/mountinfo; '
                               'find . -type f -exec cat {} \\; 2>/dev/null; '
                               f'cat {outside} 2>/dev/null; test ! -S /var/run/docker.sock; '
                               'echo overwrite > .git/config; echo leak > .env; '
                               'python3 --version; node --version; npm --version; command -v rg')
        self.assertNotIn('PRIVATE_SENTINEL', output)
        self.assertNotIn('HOST_API_SECRET', output)
        self.assertNotIn('HOST_ONLY', output)
        self.assertNotIn('uid=0(', output)
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            self.runtime.call('read_file', {'path': 'outside-link'})
        self.assertIn('PUBLIC_TEMPLATE', output)
        self.assertIn('Python 3.12', output)
        self.assertIn('v22.', output)
        self.assertIn('Read-only file system', output)
        self.assertEqual((self.root / '.git/config').read_text(), 'original')
        self.assertEqual((self.root / '.env').read_text(), 'PRIVATE_SENTINEL')
        result = self.runtime.call('write_file', {'path': 'allowed.txt', 'content': 'first\n'})
        self.assertEqual(result.details['added'], 1)
        self.runtime.call('edit_file', {'path': 'allowed.txt', 'old': 'first', 'new': 'second'})
        self.assertIn('second', self.runtime.call('read_file', {'path': 'allowed.txt'}))
        self.assertIn('allowed.txt', self.runtime.call('list_files', {}))
        self.assertIn('second', self.runtime.call('grep', {'regex': 'second'}))
        self.assertEqual((self.root / 'allowed.txt').read_text(), 'second\n')
        self.assertIn('blocked', self.bash('touch /etc/forbidden || echo blocked'))
        self.assertIn('fresh', self.bash('test ! -e /tmp/previous && echo fresh; touch /tmp/previous'))
        self.assertIn('fresh', self.bash('test ! -e /tmp/previous && echo fresh'))

    def test_network_deny_and_controlled_allow(self):
        # A service in a separate container makes this identical on Linux and Docker Desktop.
        server = 'chiikawa-network-' + self.root.name
        try:
            docker(['run', '-d', '--name', server, '--network=bridge', '--entrypoint=python3',
                    self.runtime.image, '-m', 'http.server', '8765'])
            address = docker(['inspect', '--format', '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}', server]).strip()
            command = f'''python3 -c 'import urllib.request; print(urllib.request.urlopen("http://{address}:8765", timeout=2).status)' '''
            self.assertNotEqual(self.bash(command).details['exit_code'], 0)
            allowed = SandboxRuntime(self.root, network='allow')
            self.assertIn('200', allowed.call('bash', {'command': command}))
        finally:
            docker(['rm', '-f', server])

    def test_memory_skills_and_real_child_tools(self):
        skill = self.root / 'skills/example/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('---\ndescription: Example skill\n---\nSkill body')
        h = Harness(self.root, isolation='sandbox')
        self.assertNotIn('Example skill', h.system)
        self.assertIn('Example skill', h._environment['catalog'])
        self.assertIn('Skill body', h.tools['use_skill'].run(name='example'))
        h.tools['remember'].run(note='A project fact')
        self.assertIn('A project fact', (self.root / 'CHIIKAWA.md').read_text())
        replies = iter([
            {'text': '', 'tool_calls': [{'name': 'bash', 'args': {'command': 'pwd; python3 --version'}, 'call_id': 'child'}]},
            {'text': 'child done', 'tool_calls': []}])
        seen = []
        def complete(model, system, messages, tools):
            self.assertIn('"workspace": "/workspace"', system)
            seen[:] = messages
            return next(replies)
        with patch.object(provider, 'complete', side_effect=complete):
            self.assertEqual(h.tools['spawn_agent'].run(task='inspect'), 'child done')
        self.assertIn('/workspace', seen[-1]['text'])
        self.assertFalse(list((self.root / '.chiikawa/sessions').glob('*.jsonl')))

    def test_enterprise_policy_stays_on_host_and_project_tools_cannot_modify_it(self):
        from chiikawa import system_policy
        self.runtime.call('write_file', {'path': 'agents.md', 'content': 'Use the project tests.'})
        (self.root / 'CHIIKAWA.md').write_text('PROJECT_CONTEXT_SENTINEL')
        h = Harness(self.root, profile='enterprise')
        self.assertEqual(h.isolation, 'sandbox')
        self.assertEqual(set(h._environment), {'memory', 'catalog'})
        self.assertNotIn('PROJECT_CONTEXT_SENTINEL', h.system)
        self.assertIn('PROJECT_CONTEXT_SENTINEL', h._environment['memory'])
        installed = system_policy.installation_path() / 'SYSTEM_PROMPT.md'
        before = installed.read_bytes()
        # Outside-project source policy is neither mounted nor writable from bash.
        import shlex
        result = h.tools['bash'].run(command='printf tampered > ' + shlex.quote(str(installed)))
        self.assertNotEqual(result.details['exit_code'], 0)
        self.assertEqual(installed.read_bytes(), before)
        staged = h.tools['bash'].run(command='ls -1 /opt/chiikawa-worker; test ! -e /opt/chiikawa-worker/SYSTEM_PROMPT.md')
        self.assertEqual(staged.details['exit_code'], 0)
        self.assertNotIn('SYSTEM_PROMPT.md', staged)
        self.assertNotIn('system_policy.py', staged)
        with self.assertRaisesRegex(RuntimeError, 'escapes'):
            h.tools['write_file'].run(path=str(installed), content='tampered')
        with self.assertRaisesRegex(ValueError, 'downgrade'):
            h.set_isolation('jail')
        h.tools['write_file'].run(path='project.txt', content='permitted')
        self.assertEqual((self.root / 'project.txt').read_text(), 'permitted')
        self.assertIn('project tests', h.tools['read_file'].run(path='agents.md'))
        captured = []
        def complete(model, system, messages, tools):
            captured.append(system)
            self.assertIn('"profile": "enterprise"', system)
            self.assertIn('PROJECT_CONTEXT_SENTINEL', messages[0]['text'])
            return {'text': 'child done', 'tool_calls': []}
        with patch.object(provider, 'complete', side_effect=complete):
            self.assertEqual(h.tools['spawn_agent'].run(task='inspect'), 'child done')
        self.assertEqual(len(captured), 1)
        self.assertEqual(installed.read_bytes(), before)

    def test_timeout_and_interrupt_remove_container_and_descendants(self):
        before = set(docker(['ps', '-aq', '--filter', 'name=chiikawa-']).split())
        self.assertIn('timed out', self.bash('sleep 40 & wait', timeout='0.2'))
        script = ('from chiikawa.runtime import SandboxRuntime; '
                  f'SandboxRuntime({str(self.root)!r}).call("bash", {{"command": "sleep 40 & wait"}})')
        process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                running = set(docker(['ps', '-q', '--filter', 'name=chiikawa-']).split())
                if running - before:
                    break
                time.sleep(.1)
            else:
                self.fail('Container never started')
            config = json.loads(docker(['inspect', next(iter(running - before))]))[0]
            host = config['HostConfig']
            self.assertTrue(host['ReadonlyRootfs'])
            self.assertEqual(host['Memory'], 2 * 1024 ** 3)
            self.assertEqual(host['NanoCpus'], 2 * 10 ** 9)
            self.assertEqual(host['PidsLimit'], 256)
            self.assertEqual(host['NetworkMode'], 'none')
            self.assertIn('ALL', host['CapDrop'])
            self.assertIn('no-new-privileges', host['SecurityOpt'])
            self.assertNotIn(config['Config']['User'].split(':')[0], ('0', 'root'))
            process.send_signal(signal.SIGINT)
            process.communicate(timeout=15)
            self.assertNotEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        self.assertEqual(set(docker(['ps', '-aq', '--filter', 'name=chiikawa-']).split()), before)


@unittest.skipUnless(os.environ.get('CHIIKAWA_TEST_DOCKER') == '1', 'Set CHIIKAWA_TEST_DOCKER=1')
class DockerTerminalTests(test_commands.TerminalTests):
    def test_switch_in_real_terminal_and_green_completion(self):
        self.start(script='''import os
os.environ.pop('NO_COLOR', None)
from chiikawa.cli import main
import tempfile
with tempfile.TemporaryDirectory() as root:
    raise SystemExit(main(['-d', root]))
''')
        self.send('/status\r')
        self.read_until(b'Isolation: Jail')
        self.send('/sand')
        self.read_until(b'\x1b[32m')
        self.send('\t\r')
        self.read_until(b'Switched to Sandbox', timeout=30)
        self.send('/status\r')
        self.read_until(b'Network: deny')
        self.send('/jail\r')
        self.read_until(b'Switched to Jail')
        self.send('/exit\r')
        self.assert_restored()


if __name__ == '__main__':
    unittest.main()
