"""Real native Jail boundary checks; explicitly opt in outside nested sandboxes."""

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest

from chiikawa.enterprise_jail import EnterpriseJail


@unittest.skipUnless(os.environ.get("CHIIKAWA_TEST_JAIL") == "1", "Requires native OS Jail; set CHIIKAWA_TEST_JAIL=1")
class NativeJailTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="chiikawa-native-tests-")
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / "project"
        self.root.mkdir()
        self.jail = EnterpriseJail(self.root, os.getuid(), os.getgid())

    def python(self, source, timeout="10"):
        return self.jail.call("bash", {"command": shlex.quote(sys.executable) + " -I -c " + shlex.quote(source),
                                       "timeout": timeout})

    def test_project_edits_memory_skills_and_system_boundary(self):
        self.jail.call("write_file", {"path": "test.txt", "content": "permitted project edit"})
        self.assertIn("permitted project edit", self.jail.call("read_file", {"path": "test.txt"}))
        self.assertEqual((self.root / "test.txt").read_text(), "permitted project edit")
        outside = self.parent / "outside.txt"
        outside.write_text("OUTSIDE_SENTINEL")
        result = self.python(f"from pathlib import Path; print(Path({str(outside)!r}).read_text())")
        self.assertNotIn("OUTSIDE_SENTINEL", result)
        self.assertNotEqual(result.details["exit_code"], 0)
        self.jail.call("remember", {"note": "use unittest for project checks"})
        self.assertIn("use unittest", self.jail.environment()["memory"])
        skill = self.root / "skills/example/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: example\ndescription: Project test conventions\n---\nSKILL_SENTINEL")
        self.assertIn("example", self.jail.environment()["catalog"])
        self.assertIn("SKILL_SENTINEL", self.jail.call("use_skill", {"name": "example"}))

    def test_protected_files_git_and_environment(self):
        (self.root / ".env").write_text("PROTECTED_SENTINEL")
        (self.root / ".env.example").write_text("template")
        (self.root / "credential.md").write_text("PROTECTED_SENTINEL")
        (self.root / ".git").mkdir()
        (self.root / ".git/config").write_text("preserve")
        for path in (".env", "credential.md"):
            result = self.python(f"from pathlib import Path; print(Path({path!r}).read_text())")
            self.assertNotIn("PROTECTED_SENTINEL", result)
        self.assertIn("template", self.jail.call("read_file", {"path": ".env.example"}))
        result = self.python("from pathlib import Path; Path('.git/config').write_text('overwrite')")
        self.assertNotEqual(result.details["exit_code"], 0)
        self.assertEqual((self.root / ".git/config").read_text(), "preserve")
        os.environ["CHIIKAWA_TEST_HOST_SECRET"] = "HOST_SECRET_SENTINEL"
        self.addCleanup(os.environ.pop, "CHIIKAWA_TEST_HOST_SECRET", None)
        result = self.python("import os; print(os.environ.get('CHIIKAWA_TEST_HOST_SECRET', 'not-forwarded'))")
        self.assertIn("not-forwarded", result)
        self.assertNotIn("HOST_SECRET_SENTINEL", result)

    def test_network_is_blocked_and_temporary_data_is_not_shared(self):
        result = self.python("import socket; socket.create_connection(('1.1.1.1', 443), timeout=1)")
        self.assertNotEqual(result.details["exit_code"], 0)
        self.python("import pathlib, tempfile; (pathlib.Path(tempfile.gettempdir())/'private-temp-marker').write_text('ephemeral')")
        result = self.python("import pathlib, tempfile; print((pathlib.Path(tempfile.gettempdir())/'private-temp-marker').exists())")
        self.assertIn("False", result)

    def test_unsafe_protected_symlinks_fail_before_worker(self):
        outside = self.parent / "outside.txt"
        outside.write_text("private")
        (self.root / ".env").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.jail.environment()


if __name__ == "__main__":
    unittest.main()
