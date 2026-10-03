"""Exercise real installer updates and failure handling without touching user tools."""

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_release", ROOT / "scripts" / "build_release.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="chiikawa-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        release = builder.build(self.root / "dist")
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        with tarfile.open(release) as archive:
            for entry in archive.getmembers():
                if entry.isfile():
                    (self.bundle / Path(entry.name).name).write_bytes(archive.extractfile(entry).read())
        self.bindir = self.root / "path with spaces"

    def install(self):
        return subprocess.run([sys.executable, str(self.bundle / "install.py"), "--bin-dir", str(self.bindir)],
                              cwd=self.root, text=True, capture_output=True, timeout=30)

    def test_install_and_upgrade_preserve_workspace(self):
        memory = self.root / "CHIIKAWA.md"
        memory.write_text("keep my memory")
        for _ in range(2):
            result = self.install()
            self.assertEqual(result.returncode, 0, result.stderr)
            command = self.bindir / "chiikawa"
            self.assertEqual(command.read_bytes(), (self.bundle / "chiikawa.pyz").read_bytes())
            result = subprocess.run([str(command), "--version"], cwd=self.root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("chiikawa ", result.stdout)
        self.assertEqual(memory.read_text(), "keep my memory")
        self.assertEqual(list(self.bindir.glob(".chiikawa-*")), [])

    def test_damaged_download_preserves_existing_installation(self):
        self.assertEqual(self.install().returncode, 0)
        before = (self.bindir / "chiikawa").read_bytes()
        with (self.bundle / "chiikawa.pyz").open("ab") as target:
            target.write(b"corrupted")
        result = self.install()
        self.assertEqual(result.returncode, 1)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual((self.bindir / "chiikawa").read_bytes(), before)

    def test_unrelated_executable_and_symlink_are_not_overwritten(self):
        self.bindir.mkdir()
        command = self.bindir / "chiikawa"
        command.write_text("unrelated command")
        self.assertEqual(self.install().returncode, 1)
        self.assertEqual(command.read_text(), "unrelated command")
        command.unlink()
        other = self.root / "other"
        other.write_text("keep")
        command.symlink_to(other)
        self.assertEqual(self.install().returncode, 1)
        self.assertTrue(command.is_symlink())
        self.assertEqual(other.read_text(), "keep")

    def test_unrunnable_archive_is_rejected_before_install(self):
        source = self.bundle / "chiikawa.pyz"
        source.write_text("raise SystemExit(7)\n")
        (self.bundle / "SHA256SUMS").write_text(f"{hashlib.sha256(source.read_bytes()).hexdigest()}  chiikawa.pyz\n")
        self.assertEqual(self.install().returncode, 1)
        self.assertFalse((self.bindir / "chiikawa").exists())


if __name__ == "__main__":
    unittest.main()
