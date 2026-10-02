"""Day 4: durable JSONL sessions with torn-tail tolerance and tool-call repair.

Keep the journal append-only except for a damaged tail. Lock readers/writers,
sync every record, and preserve provider call IDs and opaque response items.
"""

import fcntl
import json
import os
import re
import time
from pathlib import Path

SESSION_DIR = ".chiikawa/sessions"
INTERRUPTED = "Interrupted before this ran (process restarted)."


def _directory(workdir):
    """Resolve the session directory without following an external symlink."""
    root = Path(workdir).resolve()
    directory = (root / SESSION_DIR).resolve()
    if not directory.is_relative_to(root):
        raise PermissionError(f"{SESSION_DIR!r} escapes the working directory")
    return directory


def _sync_directory(directory):
    """Make a newly created session's directory entry durable on local POSIX filesystems."""
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def new_session(workdir, label="session"):
    """Reserve a private, collision-resistant timestamp/slug JSONL file and return its path."""
    directory = _directory(workdir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", label).strip("-")[:40].rstrip("-") or "session"
    while True:
        stamp = time.time_ns()
        path = directory / f"{stamp // 10**9}.{stamp % 10**9:09d}-{slug}.jsonl"
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            continue
        os.close(descriptor)
        _sync_directory(directory)
        return path


def _parse(source):
    """Read complete JSON messages and return the byte offset before any damaged tail."""
    source.seek(0)
    messages, end = [], 0
    while line := source.readline():
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            break  # A killed writer can leave an incomplete JSON or UTF-8 tail.
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "tool"}:
            raise ValueError("Invalid session message: expected a user, assistant, or tool dictionary")
        messages.append(message)
        end = source.tell()
    return messages, end


def _read(path):
    """Read the persisted prefix under a shared lock, without repairing it on disk."""
    with Path(path).open("rb") as source:
        fcntl.flock(source, fcntl.LOCK_SH)
        return _parse(source)[0]


def append(path, message):
    """Append one Unicode JSON record, first removing any previously torn tail."""
    encoded = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
    path = Path(path)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_RDWR, 0o600), "r+b") as target:
        fcntl.flock(target, fcntl.LOCK_EX)
        _, end = _parse(target)
        target.seek(end)
        target.truncate()
        if end:
            target.seek(end - 1)
            separator = b"" if target.read(1) == b"\n" else b"\n"
            target.seek(end)
            target.write(separator)
        target.write(encoded)
        target.flush()
        os.fsync(target.fileno())
    _sync_directory(path.parent)


def load(path):
    """Load the valid prefix and synthesize results for unfinished final tool calls.

    The exact interruption text is the course protocol, not proof that a tool
    had no side effects before the crash. Inspect external state before retrying.
    """
    messages = _read(path)
    last = next((index for index in range(len(messages) - 1, -1, -1)
                 if messages[index]["role"] == "assistant"), None)
    if last is not None:
        completed = sum(message["role"] == "tool" for message in messages[last + 1:])
        for call in messages[last].get("tool_calls", [])[completed:]:
            result = {"role": "tool", "name": call["name"], "text": INTERRUPTED}
            if "call_id" in call:
                result["call_id"] = call["call_id"]
            messages.append(result)
    return messages


def latest(workdir):
    """Return the most recently modified contained JSONL session, or None."""
    directory = _directory(workdir)
    paths = [path for path in directory.glob("*.jsonl")
             if path.is_file() and path.resolve().is_relative_to(directory)]
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, path.name), default=None)
