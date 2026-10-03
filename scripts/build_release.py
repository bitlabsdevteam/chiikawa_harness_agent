#!/usr/bin/env python3
"""Build a portable zipapp and installer bundle from an explicit source allowlist."""

import argparse
import hashlib
from pathlib import Path
import runpy
import shutil
import tarfile
import tempfile
import zipapp


ROOT = Path(__file__).resolve().parents[1]


def build(output):
    version = runpy.run_path(str(ROOT / "chiikawa" / "_version.py"))["__version__"]
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="chiikawa-release-") as temporary:
        staging = Path(temporary)
        app = staging / "app"
        package = app / "chiikawa"
        package.mkdir(parents=True)
        for source in sorted((ROOT / "chiikawa").glob("*.py")):
            shutil.copyfile(source, package / source.name)
        (app / "CHIIKAWA-ARCHIVE").write_text("chiikawa-harness\n")
        (app / "__main__.py").write_text(
            'import os, sys\n'
            'if sys.version_info < (3, 10) or os.name != "posix":\n'
            '    sys.exit("Chiikawa requires Python 3.10+ on macOS, Linux, or WSL.")\n'
            'from chiikawa.cli import main\n'
            'sys.exit(main())\n'
        )
        bundle = staging / f"chiikawa-{version}"
        bundle.mkdir()
        zipapp.create_archive(app, bundle / "chiikawa.pyz", interpreter="/usr/bin/env python3",
                              compressed=True)
        shutil.copyfile(ROOT / "scripts" / "install.py", bundle / "install.py")
        shutil.copyfile(ROOT / "INSTALL.md", bundle / "INSTALL.md")
        checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                            for path in sorted(bundle.iterdir()))
        (bundle / "SHA256SUMS").write_text(checksums)
        archive = output / f"chiikawa-{version}.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            target.add(bundle, arcname=bundle.name)
        shutil.copyfile(bundle / "chiikawa.pyz", output / "chiikawa.pyz")
        (output / "chiikawa.pyz").chmod(0o755)
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(build(args.output.resolve()))


if __name__ == "__main__":
    main()
