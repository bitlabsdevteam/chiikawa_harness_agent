"""Exercise actual SIGINT cleanup of an independently running shell process group."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "posix", "Shell process groups require POSIX")
class ShellInterruptTests(unittest.TestCase):
    """Ensure interrupting the harness cannot leave its shell mutating the workspace."""

    def test_interrupt_stops_pending_shell_write(self):
        """Signal the real tool after its child starts and reject a delayed side effect."""
        with tempfile.TemporaryDirectory() as directory:
            child = "from pathlib import Path; import time; Path('started').touch(); time.sleep(1); Path('late').touch()"
            parent = """import shlex, sys
from chiikawa.tools import core_tools
bash = next(tool for tool in core_tools(sys.argv[1]) if tool.name == 'bash')
try:
    bash.run(command=shlex.join([sys.executable, '-c', sys.argv[2]]))
except KeyboardInterrupt:
    raise SystemExit(130)
"""
            process = subprocess.Popen([sys.executable, "-c", parent, directory, child],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not (Path(directory) / "started").exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue((Path(directory) / "started").exists(), "child never started")
                process.send_signal(signal.SIGINT)
                stdout, stderr = process.communicate(timeout=3)
                self.assertEqual(process.returncode, 130, (stdout, stderr))
                time.sleep(1.1)
                self.assertFalse((Path(directory) / "late").exists(), "command survived Ctrl-C")
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate()


if __name__ == "__main__":
    unittest.main()
