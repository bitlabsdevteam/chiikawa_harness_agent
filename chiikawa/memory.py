"""Contained project memory, read separately from trusted operating policy.

Keep memory within the canonical workspace. Serialize appends with a file lock,
flush data to disk, and never hide storage failures behind a success message.
"""

import fcntl
import os
import platform
from pathlib import Path

MEMORY_FILE = "CHIIKAWA.md"


def _memory_path(workdir):
    """Resolve the memory file without following links outside the workspace."""
    root = Path(workdir).resolve()
    path = (root / MEMORY_FILE).resolve()
    if not path.is_relative_to(root):
        raise PermissionError(f"{MEMORY_FILE!r} escapes the working directory")
    return root, path


def read_memory(workdir):
    """Read contained project memory as data, never as trusted operating policy."""
    _, path = _memory_path(workdir)
    if path.exists():
        with path.open(encoding="utf-8") as source:
            fcntl.flock(source, fcntl.LOCK_SH)
            return source.read()
    return ""


def build_system_prompt(workdir, extra=""):
    """Legacy host helper: policy/platform only; use read_memory for project context."""
    if extra:
        raise ValueError("System prompt overrides are not supported; use agents.md or ordinary task context.")
    from .system_policy import compose_system, load_policy
    return compose_system(load_policy(), {"platform": platform.system(), "workspace": str(Path(workdir).resolve())})


def remember(workdir, note):
    """Append a fact under an exclusive POSIX lock, then sync file and directory."""
    _, path = _memory_path(workdir)
    with path.open("a", encoding="utf-8") as target:
        fcntl.flock(target, fcntl.LOCK_EX)
        target.write(f"- {note}\n")
        target.flush()
        os.fsync(target.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return f"Remembered in {MEMORY_FILE}"
