#!/usr/bin/env python3
"""Install the adjacent, checksummed zipapp without pip or administrator access."""

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install Chiikawa for the current user")
    parser.add_argument("--bin-dir", type=Path, default=Path.home() / ".local" / "bin",
                        help="Command directory (default: ~/.local/bin)")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10) or os.name != "posix":
        parser.error("Chiikawa requires Python 3.10+ on macOS, Linux, or WSL.")

    source_dir = Path(__file__).resolve().parent
    source = source_dir / "chiikawa.pyz"
    temporary = None
    try:
        checksums = dict(line.split(maxsplit=1)[::-1] for line in
                         (source_dir / "SHA256SUMS").read_text().splitlines() if line.strip())
        expected = checksums.get("chiikawa.pyz")
        if expected is None or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise ValueError("Chiikawa archive checksum mismatch; download the release again.")
        # Verify this interpreter can actually run the release before replacing anything.
        version = subprocess.run([sys.executable, str(source), "--version"], check=True,
                                 capture_output=True, text=True).stdout.strip()
        destination_dir = args.bin_dir.expanduser().resolve()
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / "chiikawa"
        if destination.is_symlink():
            raise ValueError(f"Refusing to replace a symlink: {destination}")
        if destination.exists():
            # Do not overwrite a pipx/uv launcher or an unrelated executable.
            try:
                with zipfile.ZipFile(destination) as archive:
                    owned = archive.read("CHIIKAWA-ARCHIVE") == b"chiikawa-harness\n"
            except (OSError, KeyError, zipfile.BadZipFile):
                owned = False
            if not owned:
                raise ValueError(f"{destination} belongs to another installation; choose --bin-dir.")
        with tempfile.NamedTemporaryFile(dir=destination_dir, prefix=".chiikawa-", delete=False) as target:
            temporary = Path(target.name)
            with source.open("rb") as release:
                shutil.copyfileobj(release, target)
            target.flush()
            os.fsync(target.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, destination)
        temporary = None
        print(f"Installed {version}: {destination}")
        path_dirs = [Path(entry).expanduser().resolve() for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
        if destination_dir not in path_dirs:
            print("Add this directory to PATH in your shell profile:")
            print(f"  export PATH={shlex.quote(str(destination_dir))}:\"$PATH\"")
        print("Run chiikawa --help, then follow INSTALL.md to configure Foundry.")
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
