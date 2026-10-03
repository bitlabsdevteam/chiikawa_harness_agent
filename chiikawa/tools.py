"""Day 2: small, string-argument tools with bounded output and rooted file access.

All file paths share one realpath check. The shell runs in the workspace but is
not a filesystem sandbox; use the separate policy hook to control execution.
"""

import fnmatch
import difflib
import inspect
import os
import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

IGNORED = {".git", "node_modules", "__pycache__", ".venv"}


class ToolResult(str):
    """Keep model-facing text compatible while attaching terminal presentation data."""

    def __new__(cls, text, **details):
        result = super().__new__(cls, text)
        result.details = details
        return result


def change_details(path, before, after, created=False):
    """Bound diff work and output; calculate changes from the actual file contents."""
    details = {"path": path, "operation": "created" if created else "updated"}
    if before is None or len(before) + len(after) > 128_000:
        return {**details, "diff_note": "Diff omitted (large or non-text file)."}
    old_lines, new_lines = before.splitlines(), after.splitlines()
    added = removed = 0
    for tag, i, j, k, l in difflib.SequenceMatcher(None, old_lines, new_lines).get_opcodes():
        if tag in ("insert", "replace"):
            added += l - k
        if tag in ("delete", "replace"):
            removed += j - i
    preview = []
    for line in difflib.unified_diff(old_lines, new_lines, fromfile=path, tofile=path, n=2, lineterm=""):
        if len(preview) == 40:
            preview.append("... diff truncated ...")
            break
        preview.append(line[:240])
    return {**details, "added": added, "removed": removed, "diff": preview}


@dataclass
class Tool:
    """Keep the public name, provider schema, and executable callable together."""

    name: str
    spec: dict
    run: Callable


def tool(description, **params):
    """Describe a function as a tool; defaults make string arguments optional."""
    def decorate(fn):
        """Inspect the callable once and preserve its original implementation."""
        arguments = inspect.signature(fn).parameters
        properties = {name: {"type": "string", "description": params[name]}
                      for name in arguments}
        required = [name for name, arg in arguments.items()
                    if arg.default is inspect.Parameter.empty]
        return Tool(fn.__name__, {"schema": {
            "name": fn.__name__, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        }}, fn)
    return decorate


def core_tools(workdir) -> list[Tool]:
    """Create six tools rooted at the canonical workspace, without global state."""
    root = Path(workdir).resolve()

    def resolve(path):
        """Reject traversal and symlinks escaping the canonical workspace."""
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise PermissionError(f"{path!r} escapes the working directory")
        return target

    def paths(pattern):
        """Walk matching files, pruning ignored directories and outside symlinks."""
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(name for name in dirs if name not in IGNORED)
            for name in sorted(files):
                relative = str((Path(directory) / name).relative_to(root))
                if not (fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern)):
                    continue
                try:
                    target = resolve(relative)
                except PermissionError:
                    continue
                if target.is_file():
                    yield relative, target

    @tool("Read a UTF-8 file with line numbers", path="File path within the workspace")
    def read_file(path):
        """Number at most 4,000 lines and report the total when truncating."""
        lines = resolve(path).read_text(encoding="utf-8").splitlines()
        result = [f"{index}\t{line}" for index, line in enumerate(lines[:4000], 1)]
        if len(lines) > 4000:
            result.append(f"... truncated; {len(lines)} total lines")
        return ToolResult("\n".join(result), path=path, lines=len(lines))

    @tool("Write a UTF-8 file", path="Destination path", content="Complete file contents")
    def write_file(path, content):
        """Create missing parents and report the number of characters written."""
        target = resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        created = not target.exists()
        before = "" if created else None
        if not created:
            try:
                if target.stat().st_size <= 128_000:
                    before = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                pass  # A preview must not prevent an otherwise permitted overwrite.
        target.write_text(content, encoding="utf-8")
        return ToolResult(f"Wrote {len(content)} chars to {path}",
                          **change_details(path, before, content, created))

    @tool("Replace one exact snippet", path="File path", old="Unique existing text", new="Replacement text")
    def edit_file(path, old, new):
        """Require an unambiguous exact match before modifying any file content."""
        target = resolve(path)
        content = target.read_text(encoding="utf-8")
        count = content.count(old)
        # Uniqueness prevents a short or stale snippet from editing the wrong spot.
        if count == 0:
            return "ERROR: snippet not found — read the file and copy it exactly"
        if count != 1:
            return f"ERROR: snippet appears {count} times — include more context to make it unique"
        updated = content.replace(old, new, 1)
        target.write_text(updated, encoding="utf-8")
        return ToolResult(f"Edited {path}", **change_details(path, content, updated))

    @tool("Run a shell command in the workspace", command="Shell command", timeout="Timeout in seconds")
    def bash(command, timeout="120"):
        """Capture bounded output and stop the process group on timeout or interrupt."""
        seconds = float(timeout)
        with subprocess.Popen(command, shell=True, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, errors="replace",
                              start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=seconds)
            except subprocess.TimeoutExpired:
                # Stop descendants too, so a timed-out command cannot keep working.
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                return f"ERROR: timed out after {timeout}s"
            except KeyboardInterrupt:
                # The command has its own session, so terminal SIGINT misses it.
                # Stop its descendants before the CLI exits and offers resume.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
                raise
        output = stdout + stderr
        if len(output) > 12000:
            output = output[:6000] + "\n... output truncated ...\n" + output[-6000:]
        return ToolResult(output or f"(exit {process.returncode}, no output)", exit_code=process.returncode)

    @tool("List workspace files", pattern="Glob matched against relative path or basename")
    def list_files(pattern="**/*"):
        """Sort matching paths, showing at most 500 and counting the remainder."""
        # The default recursive pattern also includes files in the workspace root.
        matches = sorted(path for path, _ in paths("*" if pattern == "**/*" else pattern))
        result = matches[:500]
        if len(matches) > 500:
            result.append(f"... and {len(matches) - 500} more")
        return "\n".join(result)

    @tool("Search workspace text files", regex="Python regular expression", pattern="File glob")
    def grep(regex, pattern="*"):
        """Return at most 200 path/line matches with source text clipped to 200 chars."""
        expression = re.compile(regex)
        hits = []
        for path, target in sorted(paths(pattern)):
            try:
                with target.open(encoding="utf-8") as source:
                    for number, line in enumerate(source, 1):
                        if expression.search(line):
                            hits.append(f"{path}:{number}: {line.rstrip(chr(10) + chr(13))[:200]}")
                            if len(hits) == 200:
                                return "\n".join(hits)
            except (UnicodeError, OSError):
                continue
        return "\n".join(hits)

    return [read_file, write_file, edit_file, bash, list_files, grep]
