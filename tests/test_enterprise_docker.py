"""Real OS ownership checks in a disposable, offline Linux container."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

from chiikawa.runtime import DEFAULT_IMAGE


@unittest.skipUnless(os.environ.get("CHIIKAWA_TEST_DOCKER") == "1", "Requires the explicitly built Sandbox image and Docker")
class ManagedOwnershipTests(unittest.TestCase):
    def test_nonadmin_cannot_change_policy_read_quota_or_use_admin_commands(self):
        script = r'''
import json, os, pathlib, subprocess, sys
from chiikawa.enterprise_admin import write_policy
from chiikawa.enterprise_policy import EnterprisePolicy, Developer
from chiikawa.enterprise_quota import QuotaLedger
root = pathlib.Path('/managed')
policy_dir = root / 'policy'
policy_dir.mkdir(mode=0o755)
state = root / 'state'
state.mkdir(mode=0o700)
path = policy_dir / 'enterprise.json'
raw = {'version': 1, 'tenant_id': '11111111-1111-4111-8111-111111111111',
       'client_id': '22222222-2222-4222-8222-222222222222',
       'developers': {}, 'providers': {}, 'network': {'allow': []}}
write_policy(path, raw)
assert EnterprisePolicy.load(path).default_isolation == 'jail'
ledger = QuotaLedger(state / 'usage.sqlite3')
developer = Developer('33333333-3333-4333-8333-333333333333', 12345, 'd' * 64, True, 100)
ledger.reserve(developer, 30, 30)
probe = """
from pathlib import Path
from chiikawa.enterprise_admin import main
from chiikawa.enterprise_quota import QuotaLedger
for path, operation in [
    ('/managed/policy/enterprise.json', lambda p: p.write_text('forged')),
    ('/managed/policy/enterprise.json', lambda p: p.unlink()),
    ('/managed/state/usage.sqlite3', lambda p: p.read_bytes())]:
    try:
        operation(Path(path))
    except PermissionError:
        pass
    else:
        raise AssertionError('developer accessed protected state')
try:
    QuotaLedger('/managed/state/usage.sqlite3')
except PermissionError:
    pass
else:
    raise AssertionError('developer opened privileged ledger')
assert main(['--policy', '/managed/policy/enterprise.json', 'validate']) == 1
"""
subprocess.run([sys.executable, '-c', probe], user=12345, group=12345,
               env={'PYTHONPATH': '/opt/test', 'PATH': '/usr/bin:/bin'}, check=True)
assert ledger.status(developer)['charged'] == 60
before = path.read_bytes()
path.chmod(0o666)
try:
    EnterprisePolicy.load(path)
except PermissionError:
    pass
else:
    raise AssertionError('accepted developer-writable policy')
path.chmod(0o644)
linked = policy_dir / 'linked.json'
linked.symlink_to(path)
try:
    EnterprisePolicy.load(linked)
except PermissionError:
    pass
else:
    raise AssertionError('accepted policy symlink')
assert path.read_bytes() == before
print('OS ownership, policy integrity and quota access verified')
'''
        name = "chiikawa-management-test-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="chiikawa-management-test-") as temporary:
            staging = Path(temporary)
            staging.chmod(0o755)
            package = staging / "chiikawa"
            package.mkdir(mode=0o755)
            (package / "__init__.py").write_text("")
            source = Path(__file__).resolve().parents[1] / "chiikawa"
            for path in source.glob("enterprise_*.py"):
                shutil.copyfile(path, package / path.name)
            try:
                result = subprocess.run([
                    "docker", "run", "--name", name, "--rm", "--pull=never", "--network=none", "--read-only", "--user=0:0",
                    "--security-opt=no-new-privileges", "--cpus=1", "--memory=256m", "--pids-limit=64",
                    "--tmpfs=/managed:mode=755", "--tmpfs=/tmp:mode=1777",
                    "--mount", f"type=bind,src={staging},dst=/opt/test,readonly",
                    "--env=PYTHONPATH=/opt/test", "--entrypoint=python3", DEFAULT_IMAGE, "-c", script],
                    text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("OS ownership", result.stdout)
            finally:
                cleanup = subprocess.run(["docker", "rm", "--force", name], capture_output=True, text=True, timeout=30)
                if cleanup.returncode and "No such container" not in cleanup.stderr:
                    self.fail("Could not remove management-test container: " + cleanup.stderr)


if __name__ == "__main__":
    unittest.main()
