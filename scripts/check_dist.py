#!/usr/bin/env python3
"""Audit release contents and exercise installed commands outside the checkout."""

import hashlib
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


ROOT = Path(__file__).resolve().parents[1]
VERSION = runpy.run_path(str(ROOT / "chiikawa" / "_version.py"))["__version__"]


def smoke(command, workspace):
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONPATH", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "CHIIKAWA_API_KEY"}}
    def run(*args):
        return subprocess.run([*command, *args], cwd=workspace, env=env,
                              input="", text=True, capture_output=True, timeout=30)
    result = run("--version")
    assert result.returncode == 0 and result.stdout.strip() == f"chiikawa {VERSION}", result
    result = run("--help")
    assert result.returncode == 0 and "--resume" in result.stdout, result
    result = run()
    assert result.returncode == 0 and "C H I I K A W A" in result.stdout and "chiikawa>" in result.stdout, result
    result = run("-p", "offline probe", "--max-turns", "0")
    assert result.returncode == 1 and "AZURE_OPENAI_ENDPOINT" in result.stderr, result
    assert "C H I I K A W A" not in result.stdout, result


def main():
    dist = ROOT / "dist"
    wheel = dist / f"chiikawa_harness-{VERSION}-py3-none-any.whl"
    sdist = dist / f"chiikawa_harness-{VERSION}.tar.gz"
    portable = dist / f"chiikawa-{VERSION}.tar.gz"
    metadata_dir = f"chiikawa_harness-{VERSION}.dist-info"
    package_files = {f"chiikawa/{path.name}" for path in (ROOT / "chiikawa").glob("*.py")}
    with zipfile.ZipFile(wheel) as archive:
        assert set(archive.namelist()) == package_files | {
            f"{metadata_dir}/{name}" for name in ("METADATA", "WHEEL", "entry_points.txt", "top_level.txt", "RECORD")
        }, archive.namelist()
        metadata = archive.read(f"{metadata_dir}/METADATA").decode()
        assert "Requires-Python: >=3.10" in metadata and "Requires-Dist:" not in metadata
        assert "chiikawa = chiikawa.cli:main" in archive.read(f"{metadata_dir}/entry_points.txt").decode()
    with zipfile.ZipFile(dist / "chiikawa.pyz") as archive:
        assert {name for name in archive.namelist() if not name.endswith("/")} == package_files | {"__main__.py", "CHIIKAWA-ARCHIVE"}
    with tarfile.open(sdist) as archive:
        allowed = {"PKG-INFO", "README.md", "INSTALL.md", "MANIFEST.in", "pyproject.toml", "setup.cfg"}
        allowed.update(package_files)
        allowed.update(f"{directory}/{path.name}" for directory in ("scripts", "tests", "demos")
                       for path in (ROOT / directory).glob("*.py"))
        allowed.update(f"chiikawa_harness.egg-info/{name}" for name in
                       ("PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "top_level.txt"))
        for entry in archive.getmembers():
            assert not entry.issym() and not entry.islnk(), entry.name
            if entry.isfile():
                name = entry.name.split("/", 1)[1]
                assert name in allowed, name
    with tempfile.TemporaryDirectory(prefix="chiikawa-install-check-") as temporary:
        workspace = Path(temporary)
        with tarfile.open(portable) as archive:
            expected = {f"chiikawa-{VERSION}/{name}" for name in ("chiikawa.pyz", "install.py", "INSTALL.md", "SHA256SUMS")}
            assert {entry.name for entry in archive.getmembers() if entry.isfile()} == expected
            assert all(entry.isfile() or entry.isdir() for entry in archive.getmembers())
            # Extract only the known files, without permitting archive paths to direct writes.
            bundle = workspace / "bundle"
            bundle.mkdir()
            for name in expected:
                (bundle / Path(name).name).write_bytes(archive.extractfile(name).read())
        bindir = workspace / "custom bin"
        subprocess.run([sys.executable, str(bundle / "install.py"), "--bin-dir", str(bindir)],
                       cwd=workspace, check=True)
        smoke([str(bindir / "chiikawa")], workspace)
        venv_dir = workspace / "venv"
        venv.create(venv_dir, with_pip=True)
        python = venv_dir / "bin" / "python"
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)], check=True)
        smoke([str(venv_dir / "bin" / "chiikawa")], workspace)
        subprocess.run([str(python), "-m", "pip", "uninstall", "-y", "chiikawa-harness"], check=True)
        assert not (venv_dir / "bin" / "chiikawa").exists()
    files = [wheel, sdist, portable, dist / "chiikawa.pyz"]
    (dist / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files))
    print("Release contents, portable install, wheel install, startup, exit codes, and removal verified.")


if __name__ == "__main__":
    main()
