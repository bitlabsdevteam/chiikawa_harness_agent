#!/usr/bin/env python3
"""Manage a small, persistent task ledger from the command line."""

import argparse
from contextlib import contextmanager, nullcontext
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import time
import unicodedata


class TaskmanError(Exception):
    """An expected user or storage error that does not need a traceback."""


def description_text(value):
    """Normalize a task description and reject blank or unsafe terminal text."""
    text = value.strip()
    if not text:
        raise ValueError("description must not be blank")
    bidi_controls = {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"}
           or unicodedata.bidirectional(char) in bidi_controls
           or char in "\u061c\u200e\u200f" for char in value):
        raise ValueError("description must be a single line without control or bidi formatting characters")
    if not any(unicodedata.category(char)[0] in "LNPS" for char in text):
        raise ValueError("description must contain visible text, not only spacing or formatting marks")
    return text


def description_arg(value):
    """Adapt description validation to argparse's usage-error protocol."""
    try:
        return description_text(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def positive_id(value):
    """Accept positive integer task IDs, reporting invalid IDs as usage errors."""
    if not value.isascii() or not value.isdecimal():
        raise argparse.ArgumentTypeError("ID must be a positive integer written with digits 0-9")
    try:
        ident = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ID is too large") from exc
    if ident < 1:
        raise argparse.ArgumentTypeError("ID must be a positive integer")
    return ident


def lock_seconds(value):
    """Accept finite, nonnegative lock deadlines, including fail-fast zero."""
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("lock timeout must be a finite nonnegative number") from exc
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("lock timeout must be a finite nonnegative number")
    return seconds


def table_columns(value):
    """Validate a requested table width without permitting an unusably narrow rail."""
    width = positive_id(value)
    if width < 24:
        raise argparse.ArgumentTypeError("width must be at least 24 columns")
    return width


def store_path(value):
    """Reject unusable path arguments before accessing the filesystem."""
    if not value.strip() or "\x00" in value:
        raise argparse.ArgumentTypeError("store PATH must not be blank or contain NUL")
    return Path(value)


def unique_object(pairs):
    """Decode a JSON object without silently accepting duplicate field names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key!r}")
        result[key] = value
    return result


def new_store():
    """Return an empty version-one ledger with a permanent ID counter."""
    return {"version": 1, "next_id": 1, "tasks": []}


def validate_store(data):
    """Reject malformed ledger values before any operation can rewrite them."""
    if not isinstance(data, dict):
        raise ValueError("root must be an object")
    if set(data) != {"version", "next_id", "tasks"}:
        raise ValueError("root fields must be exactly version, next_id, tasks")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise ValueError("unsupported or missing storage version (expected 1)")
    if type(data.get("next_id")) is not int or data["next_id"] < 1:
        raise ValueError("next_id must be a positive integer")
    if not isinstance(data.get("tasks"), list):
        raise ValueError("tasks must be an array")
    seen = set()
    for position, task in enumerate(data["tasks"], start=1):
        try:
            validate_task(task, seen)
        except ValueError as exc:
            ident = task.get("id") if isinstance(task, dict) else None
            label = f"task at position {position}"
            if type(ident) is int and ident > 0:
                label += f" (ID {ident})"
            raise ValueError(f"{label}: {exc}") from exc
    if seen and data["next_id"] <= max(seen):
        raise ValueError("next_id must exceed all existing task IDs")
    return data


def validate_task(task, seen):
    """Validate one task and register its ID in a shared uniqueness set."""
    if not isinstance(task, dict):
        raise ValueError("each task must be an object")
    if set(task) != {"id", "description", "done"}:
        raise ValueError("task fields must be exactly id, description, done")
    ident = task["id"]
    if type(ident) is not int or ident < 1 or ident in seen:
        raise ValueError("task IDs must be unique positive integers")
    seen.add(ident)
    if type(task["done"]) is not bool:
        raise ValueError("task done values must be true or false")
    if not isinstance(task["description"], str):
        raise ValueError("task descriptions must be strings")
    if description_text(task["description"]) != task["description"]:
        raise ValueError("stored descriptions must not have surrounding whitespace")


def regular_file_info(path):
    """Inspect without following a final symlink; reject devices and directories."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise TaskmanError("store must be a regular file, not a symlink, directory or device")
    return info


def load_store(path):
    """Load and validate UTF-8 JSON; a missing file represents an empty ledger."""
    if regular_file_info(path) is None:
        return new_store()
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle, object_pairs_hook=unique_object)
        return validate_store(data)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TaskmanError(f"corrupt storage: {exc}; file left unchanged") from exc


def lock_path_for(path):
    """Keep sidecar names within common POSIX filename limits, even for long stores."""
    name = os.fsencode(path.name)
    if len(name) <= 240:
        return path.with_name(path.name + ".lock")
    digest = hashlib.sha256(name).hexdigest()
    return path.with_name(f".taskman-{digest}.lock")


@contextmanager
def mutation_lock(path, timeout):
    """Serialize writers with a bounded, interruptible wait on a persistent lock."""
    regular_file_info(path)
    lock_path = lock_path_for(path)
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TaskmanError("lock path must be a regular file")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TaskmanError(
                        f"store is busy after {timeout:g}s; retry or increase --lock-timeout; "
                        "do not delete the lock file"
                    ) from exc
                time.sleep(min(0.05, remaining))
        yield
    finally:
        os.close(descriptor)


def save_store(path, data):
    """Atomically replace the ledger using a flushed temporary file beside it."""
    info = regular_file_info(path)
    if info is not None and not os.access(path, os.W_OK):
        raise PermissionError(13, "Permission denied: store is not writable", str(path))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".taskman-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            if info is not None:
                os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode) & 0o777)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def text_clusters(text):
    """Group combining marks, joined emoji and flag pairs for conservative wrapping."""
    clusters = []
    for char in text:
        mark = unicodedata.category(char) in {"Mn", "Mc", "Me", "Cf"}
        modifier = "\U0001f3fb" <= char <= "\U0001f3ff"
        flag_pair = (clusters and len(clusters[-1]) == 1
                     and "\U0001f1e6" <= clusters[-1] <= "\U0001f1ff"
                     and "\U0001f1e6" <= char <= "\U0001f1ff")
        if clusters and (mark or modifier or clusters[-1].endswith("\u200d") or flag_pair):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def cluster_width(cluster):
    """Estimate terminal cells without relying on external Unicode-width packages."""
    if "\u200d" in cluster or "\ufe0f" in cluster:
        return 2
    if len(cluster) == 2 and all("\U0001f1e6" <= char <= "\U0001f1ff" for char in cluster):
        return 2
    return sum(0 if unicodedata.category(char) in {"Mn", "Mc", "Me", "Cf"}
               or "\U0001f3fb" <= char <= "\U0001f3ff"
               else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
               for char in cluster)


def display_width(text):
    """Measure ordinary terminal cell width, including common multi-codepoint emoji."""
    return sum(cluster_width(cluster) for cluster in text_clusters(text))


def wrap_cell(text, width):
    """Wrap at cluster boundaries while preserving every description character."""
    lines, current, used = [], "", 0
    for cluster in text_clusters(text):
        size = cluster_width(cluster)
        if current and used + size > width:
            lines.append(current)
            current, used = "", 0
        current += cluster
        used += size
    lines.append(current)
    return lines


def render_table(headers, rows, numeric=(), columns=None):
    """Render aligned rows with an optional bounded, indented description column."""
    cells = [[str(value) for value in row] for row in rows]
    widths = [max([display_width(header)] + [display_width(row[i]) for row in cells])
              for i, header in enumerate(headers)]
    if columns is not None:
        available = max(4, columns - sum(widths[:-1]) - 2 * (len(headers) - 1))
        widths[-1] = min(widths[-1], available)

    def format_row(row):
        """Pad only the rail; keep description fragments, including internal spaces, intact."""
        padded = []
        for i, value in enumerate(row):
            padding = " " * max(0, widths[i] - display_width(value))
            padded.append(padding + value if i in numeric else
                          value + padding if i < len(row) - 1 else value)
        return "  ".join(padded)

    lines = [format_row(headers), format_row(["-" * width for width in widths])]
    for row in cells:
        fragments = wrap_cell(row[-1], widths[-1]) if columns is not None else [row[-1]]
        for index, fragment in enumerate(fragments):
            rail = row[:-1] if index == 0 else [""] * (len(row) - 1)
            lines.append(format_row([*rail, fragment]))
    return "\n".join(lines)


def execute(args, data):
    """Apply a parsed command, returning its output and whether persistence is needed."""
    tasks = data["tasks"]
    if args.command == "add":
        ident = data["next_id"]
        tasks.append({"id": ident, "description": args.description, "done": False})
        data["next_id"] += 1
        return f"Added task {ident}: {args.description}", True
    if args.command == "list":
        rows = [task for task in sorted(tasks, key=lambda task: task["id"])
                if args.status == "all" or task["done"] == (args.status == "done")]
        if not rows:
            hint = f"python3 taskman.py --store {shlex.quote(str(args.store))}"
            if args.status == "open" and tasks:
                return "No open tasks. Everything in this store is done.", False
            if args.status == "done" and tasks:
                return f"No completed tasks yet. Finish one with: {hint} done ID", False
            return f'No tasks in this store. Add one with: {hint} add "Plan the week"', False
        return render_table(
            ["ID", "STATUS", "TASK"],
            [(task["id"], "done" if task["done"] else "open", task["description"])
             for task in rows], numeric=(0,), columns=args.width
        ), False
    if args.command == "stats":
        done = sum(task["done"] for task in tasks)
        rate = f"{done / len(tasks):.0%}" if tasks else "n/a"
        return render_table(["METRIC", "VALUE"],
                            [("Total", len(tasks)), ("Open", len(tasks) - done),
                             ("Done", done), ("Completion", rate)], numeric=(1,)), False
    task = next((task for task in tasks if task["id"] == args.id), None)
    if task is None:
        raise TaskmanError(f"unknown task ID {args.id}; use 'list' to find task IDs")
    if args.command == "done":
        if task["done"]:
            return f"Task {args.id} is already done; no changes made.", False
        task["done"] = True
        return f"Completed task {args.id}: {task['description']}", True
    tasks.remove(task)
    return f"Removed task {args.id}: {task['description']}", True


def build_parser():
    """Define the task-oriented command line and its argument validation."""
    parser = argparse.ArgumentParser(
        prog="taskman.py", description="A local task ledger with permanent IDs.", allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n  python3 taskman.py add "Plan the week"\n'
               '  python3 taskman.py list --status open\n'
               '  python3 taskman.py --store ./work.json done 1\n\n'
               'Use COMMAND --help for details. --store works before or after COMMAND.\n'
               'Exit codes: 0 success, 1 task/storage/output error, 2 invalid arguments, 130 interrupted.'
    )
    parser.add_argument("--store", type=store_path, default=Path(".taskman.json"),
                        metavar="PATH", help="JSON store (default: .taskman.json in the current directory)")
    parser.add_argument("--lock-timeout", type=lock_seconds, default=5.0, metavar="SECONDS",
                        help="maximum writer wait, 0 for fail-fast (default: 5 seconds)")
    commands = parser.add_subparsers(dest="command", required=True)

    def command_parser(name, summary, example):
        """Give each subcommand its own help, example and position-independent store option."""
        command = commands.add_parser(name, help=summary, description=summary.capitalize() + ".",
                                      allow_abbrev=False, epilog="Example: python3 taskman.py " + example)
        command.add_argument("--store", type=store_path, default=argparse.SUPPRESS,
                             metavar="PATH", help="JSON store (overrides an earlier --store)")
        command.add_argument("--lock-timeout", type=lock_seconds, default=argparse.SUPPRESS,
                             metavar="SECONDS", help="maximum writer wait; ignored by list/stats")
        return command

    add = command_parser("add", "capture a new open task", 'add "Plan the week"')
    add.add_argument("description", type=description_arg, help="quoted, nonblank, single-line task; use -- before text starting with -")
    listing = command_parser("list", "show tasks in permanent ID order", "list --status open")
    listing.add_argument("--status", choices=("all", "open", "done"), default="all",
                         help="tasks to show (default: all)")
    listing.add_argument("--width", type=table_columns, default=80, metavar="COLUMNS",
                         help="wrap task text with an indented continuation rail (default: 80, minimum: 24)")
    for name, summary in (("done", "mark a task complete (repeats succeed without changes)"),
                          ("rm", "permanently remove a task; there is no undo")):
        command = command_parser(name, summary, name + " 1")
        command.add_argument("id", type=positive_id, metavar="ID", help="positive permanent task ID from list")
    command_parser("stats", "summarize counts and completion rate", "stats --store ./work.json")
    return parser


def run(argv=None):
    """Execute a command while separating storage diagnostics from output failures."""
    args = build_parser().parse_args(argv)
    path = args.store
    try:
        path = path.expanduser()
        lock = mutation_lock(path, args.lock_timeout) if args.command in {"add", "done", "rm"} else nullcontext()
        with lock:
            data = load_store(path)
            message, changed = execute(args, data)
            if changed:
                save_store(path, data)
    except (TaskmanError, OSError, RuntimeError) as exc:
        print(f"taskman: error: store {str(path)!r}: {exc}", file=sys.stderr)
        return 1
    print(message, flush=True)
    return 0


def silence_stdout():
    """Avoid a second broken-pipe exception when Python flushes stdout at shutdown."""
    with open(os.devnull, "w", encoding="utf-8") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())


def main(argv=None):
    """Handle cancellation, closed consumers and output encoding without tracebacks."""
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        try:
            code = run(argv)
        except SystemExit as exc:
            code = exc.code
        sys.stdout.flush()
        return code
    except KeyboardInterrupt:
        print("taskman: interrupted; inspect the selected store before retrying a mutation.", file=sys.stderr)
        return 130
    except BrokenPipeError:
        silence_stdout()
        return 1
    except OSError as exc:
        silence_stdout()
        print(f"taskman: error: output failed: {exc}; the command may already be saved; "
              "inspect the selected store before retrying.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
