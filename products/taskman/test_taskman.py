"""Black-box regression tests for taskman using isolated subprocess stores."""

import ast
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import os
import re
import shlex
import signal
import stat
import time
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().with_name("taskman.py")


class TaskmanCLITests(unittest.TestCase):
    """Exercise the actual CLI and persisted bytes, not mocked command handlers."""

    def setUp(self):
        """Allocate a private working directory and store for each test."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.store = self.directory / "tasks.json"
        self.environment = dict(os.environ, HOME=str(self.directory), TMPDIR=str(self.directory),
                                PYTHONIOENCODING="utf-8")

    def cli(self, *args, code=0, store=None, env=None):
        """Invoke taskman and assert the expected exit code and diagnostic channel."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--store", str(store or self.store), *args],
            cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=10,
            env={**self.environment, **(env or {})}
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        if code == 0:
            self.assertEqual(result.stderr, "")
        else:
            self.assertEqual(result.stdout, "")
            self.assertIn("error:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        return result

    def start_child(self, *args, bootstrap=None, stdout=subprocess.PIPE):
        """Start a real CLI child and register cleanup even when an assertion fails."""
        prefix = [sys.executable, str(SCRIPT)]
        if bootstrap is not None:
            prelude = "import sys; sys.path.insert(0, sys.argv.pop(1)); import taskman\n"
            prefix = [sys.executable, "-c", prelude + bootstrap, str(SCRIPT.parent)]
        child = subprocess.Popen(
            [*prefix, "--store", str(self.store), *args], cwd=self.directory,
            stdout=stdout, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=self.environment
        )
        self.addCleanup(self.stop_child, child)
        return child

    def stop_child(self, child):
        """Reap child processes and close pipes after both successful and failed tests."""
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)

    def wait_for_marker(self, marker, child):
        """Synchronize fault tests with child readiness rather than arbitrary sleeps."""
        deadline = time.monotonic() + 5
        while not marker.exists():
            if child.poll() is not None:
                self.fail(f"child exited before readiness: {child.communicate()}")
            if time.monotonic() >= deadline:
                self.fail("child did not reach its readiness marker")
            time.sleep(0.01)

    def read_store(self):
        """Decode the test ledger for semantic persistence assertions."""
        return json.loads(self.store.read_text(encoding="utf-8"))

    def test_empty_list_does_not_create_store(self):
        """Listing a missing store is an actionable empty state, not a mutation."""
        self.assertIn("No tasks", self.cli("list").stdout)
        self.assertFalse(self.store.exists())

    def test_add_persists_and_lists(self):
        """A separate process can retrieve a task created by add."""
        self.assertIn("Added task 1", self.cli("add", "Buy tea").stdout)
        self.assertIn("Buy tea", self.cli("list").stdout)
        self.assertEqual(self.read_store()["tasks"][0],
                         {"id": 1, "description": "Buy tea", "done": False})

    def test_description_is_trimmed(self):
        """Surrounding ordinary whitespace is normalized at capture time."""
        self.cli("add", "  Plan a walk  ")
        self.assertEqual(self.read_store()["tasks"][0]["description"], "Plan a walk")

    def test_unicode_round_trip(self):
        """Non-ASCII text survives storage and terminal output without escaping."""
        text = "买茶 • café ☕ 👩‍💻"
        self.cli("add", text)
        self.assertIn(text, self.cli("list").stdout)
        self.assertIn(text, self.store.read_text(encoding="utf-8"))

    def test_blank_description_preserves_bytes(self):
        """Blank capture fails before an existing ledger can change."""
        self.cli("add", "Keep me")
        before = self.store.read_bytes()
        for text in ("", "   ", "\t"):
            with self.subTest(text=text):
                self.cli("add", text, code=2)
                self.assertEqual(self.store.read_bytes(), before)

    def test_controls_rejected(self):
        """Multiline and terminal escape input never reaches the ledger."""
        for text in ("line\nbreak", "tab\there", "escape\x1b[31m", "bell\x07"):
            with self.subTest(text=text):
                self.cli("add", text, code=2)
        self.assertFalse(self.store.exists())

    def test_completion_and_duplicate_are_idempotent(self):
        """Completion persists once; the second call leaves bytes and mtime unchanged."""
        self.cli("add", "Send report")
        self.assertIn("Completed task 1", self.cli("done", "1").stdout)
        before = self.store.read_bytes(), self.store.stat().st_mtime_ns
        self.assertIn("already done", self.cli("done", "1").stdout)
        self.assertEqual((self.store.read_bytes(), self.store.stat().st_mtime_ns), before)
        self.assertTrue(self.read_store()["tasks"][0]["done"])

    def test_unknown_ids_preserve_store(self):
        """Unknown task references fail without rewriting an existing ledger."""
        self.cli("add", "Stay")
        before = self.store.read_bytes()
        for command in ("done", "rm"):
            self.assertIn("unknown task ID 999", self.cli(command, "999", code=1).stderr)
            self.assertEqual(self.store.read_bytes(), before)

    def test_ids_are_not_reused(self):
        """Deleting every task does not reset the persistent ID counter."""
        self.cli("add", "First")
        self.cli("rm", "1")
        self.cli("add", "Second")
        self.assertEqual(self.read_store()["tasks"][0]["id"], 2)
        self.assertEqual(self.read_store()["next_id"], 3)

    def test_status_filters(self):
        """Explicit open and done views only contain their matching tasks."""
        self.cli("add", "Open task")
        self.cli("add", "Finished task")
        self.cli("done", "2")
        open_output = self.cli("list", "--status", "open").stdout
        done_output = self.cli("list", "--status", "done").stdout
        self.assertIn("Open task", open_output)
        self.assertNotIn("Finished task", open_output)
        self.assertIn("Finished task", done_output)
        self.assertNotIn("Open task", done_output)

    def test_stats_counts(self):
        """Statistics include total, open and completed counts."""
        self.cli("add", "One")
        self.cli("add", "Two")
        self.cli("done", "1")
        output = self.cli("stats").stdout
        self.assertRegex(output, r"Total\s+2")
        self.assertRegex(output, r"Open\s+1")
        self.assertRegex(output, r"Done\s+1")

    def test_empty_stats(self):
        """An empty ledger has zero counts and stays absent on disk."""
        self.assertRegex(self.cli("stats").stdout, r"Total\s+0")
        self.assertFalse(self.store.exists())

    def test_corrupt_json_preserved(self):
        """All commands reject broken JSON and preserve its exact bytes."""
        before = b'{"tasks": [unfinished'
        self.store.write_bytes(before)
        for args in (("list",), ("stats",), ("add", "New"), ("done", "1"), ("rm", "1")):
            with self.subTest(args=args):
                self.assertIn("corrupt storage", self.cli(*args, code=1).stderr)
                self.assertEqual(self.store.read_bytes(), before)

    def test_invalid_utf8_preserved(self):
        """Invalid encoding is an ordinary storage error, never an implicit reset."""
        self.store.write_bytes(b"\xff\xfe")
        self.cli("add", "New", code=1)
        self.assertEqual(self.store.read_bytes(), b"\xff\xfe")

    def test_invalid_schema_preserved(self):
        """JSON alone is insufficient: IDs, counters and field types are validated."""
        valid = {"version": 1, "next_id": 2,
                 "tasks": [{"id": 1, "description": "Keep", "done": False}]}
        invalid = [[], {}, {**valid, "version": 2}, {**valid, "next_id": True},
                   {**valid, "next_id": 1}, {**valid, "tasks": valid["tasks"] * 2}]
        for key, value in (("id", True), ("id", 0), ("done", "false"),
                           ("description", " "), ("description", 5)):
            invalid.append({**valid, "tasks": [{**valid["tasks"][0], key: value}]})
        for data in invalid:
            with self.subTest(data=data):
                self.store.write_text(json.dumps(data), encoding="utf-8")
                before = self.store.read_bytes()
                self.cli("add", "New", code=1)
                self.assertEqual(self.store.read_bytes(), before)

    def test_separate_stores(self):
        """Explicit paths keep task collections completely isolated."""
        other = self.directory / "other.json"
        self.cli("add", "Private")
        self.assertIn("No tasks", self.cli("list", store=other).stdout)
        self.cli("add", "Other", store=other)
        self.assertNotIn("Other", self.cli("list").stdout)

    def test_missing_parent_is_not_created(self):
        """A missing parent is reported without silently building directories."""
        nested = self.directory / "absent" / "tasks.json"
        self.cli("add", "No place", store=nested, code=1)
        self.assertFalse(nested.parent.exists())

    def test_usage_errors_do_not_write(self):
        """Argparse rejects missing arguments, malformed IDs and unknown commands."""
        for args in ((), ("add",), ("done", "abc"), ("rm",), ("unknown",),
                     ("list", "--status", "maybe")):
            with self.subTest(args=args):
                self.cli(*args, code=2)
        self.assertFalse(self.store.exists())

    def test_table_alignment(self):
        """Numeric IDs align right and task text starts at a stable column."""
        data = {"version": 1, "next_id": 101,
                "tasks": [{"id": 2, "description": "短", "done": False},
                          {"id": 100, "description": "Longer task", "done": True}]}
        self.store.write_text(json.dumps(data), encoding="utf-8")
        lines = self.cli("list").stdout.splitlines()
        self.assertEqual(lines[0], " ID  STATUS  TASK")
        self.assertEqual(lines[2], "  2  open    短")
        self.assertEqual(lines[3], "100  done    Longer task")

    def test_no_temporary_files_after_save(self):
        """Successful writes do not leave partial JSON files behind."""
        self.cli("add", "Atomic")
        self.cli("done", "1")
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_store_option_after_every_command(self):
        """Each subparser accepts --store and a later path overrides the earlier one."""
        later = self.directory / "later store.json"
        for args in (("add", "Later"), ("list",), ("stats",), ("done", "1"), ("rm", "1")):
            with self.subTest(args=args):
                self.cli(*args, "--store", str(later))
        self.assertFalse(self.store.exists())
        self.assertEqual(json.loads(later.read_text())["next_id"], 2)

    def test_help_is_actionable_and_read_only(self):
        """Root and command help include working examples without touching storage."""
        for command in ((), ("add",), ("list",), ("done",), ("rm",), ("stats",)):
            with self.subTest(command=command):
                output = self.cli(*command, "--help").stdout
                self.assertIn("Example", output)
                self.assertIn("--store", output)
                self.assertIn("python3 taskman.py", output)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_default_store_is_local_to_working_directory(self):
        """Omitting --store writes only the documented working-directory ledger."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "add", "Local"], cwd=self.directory,
            capture_output=True, text=True, encoding="utf-8", timeout=10
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        default = self.directory / ".taskman.json"
        self.assertEqual(json.loads(default.read_text())["tasks"][0]["description"], "Local")
        self.assertFalse(self.store.exists())

    def test_relative_store_path(self):
        """Relative store paths are interpreted against the process working directory."""
        self.cli("add", "Relative", store=Path("relative.json"))
        self.assertTrue((self.directory / "relative.json").is_file())
        self.assertFalse(self.store.exists())

    def test_empty_hint_keeps_selected_store(self):
        """The empty-state add example remains isolated, even with spaces in its path."""
        selected = self.directory / "work tasks.json"
        output = self.cli("list", store=selected).stdout
        self.assertIn(f"--store '{selected}'", output)
        self.assertIn('add "Plan the week"', output)

    def test_sorted_rows_do_not_rewrite_store(self):
        """A hand-reordered valid ledger displays in ID order without changing file bytes."""
        data = {"version": 1, "next_id": 10,
                "tasks": [{"id": 9, "description": "Later", "done": False},
                          {"id": 2, "description": "Earlier", "done": True}]}
        self.store.write_text(json.dumps(data), encoding="utf-8")
        before = self.store.read_bytes(), self.store.stat().st_mtime_ns
        output = self.cli("list").stdout
        self.assertLess(output.index("Earlier"), output.index("Later"))
        self.cli("stats")
        self.assertEqual((self.store.read_bytes(), self.store.stat().st_mtime_ns), before)
        self.assertFalse(self.store.with_name(self.store.name + ".lock").exists())

    def test_dash_prefixed_description(self):
        """The standard -- delimiter supports task text starting with a dash."""
        self.cli("add", "--", "--check the draft")
        self.assertIn("--check the draft", self.cli("list").stdout)

    def test_nonpositive_ids_are_usage_errors(self):
        """Zero, negative and fractional IDs are invalid input rather than missing tasks."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        for command in ("done", "rm"):
            for ident in ("0", "-1", "1.5", "words"):
                with self.subTest(command=command, ident=ident):
                    self.assertIn("positive integer", self.cli(command, ident, code=2).stderr)
                    self.assertEqual(self.store.read_bytes(), before)

    def test_filtered_empty_states(self):
        """An empty filter does not falsely suggest that the whole store is empty."""
        self.cli("add", "Finish me")
        self.assertIn("No completed tasks yet", self.cli("list", "--status", "done").stdout)
        self.cli("done", "1")
        self.assertIn("No open tasks", self.cli("list", "--status", "open").stdout)
        self.assertIn("Finish me", self.cli("list").stdout)

    def test_duplicate_json_keys_rejected(self):
        """Ambiguous root and task fields are never silently collapsed on save."""
        values = (
            '{"version":1,"next_id":2,"next_id":1,"tasks":[]}',
            '{"version":1,"next_id":2,"tasks":[{"id":1,"description":"A",'
            '"description":"B","done":false}]}',
        )
        for value in values:
            with self.subTest(value=value):
                self.store.write_text(value, encoding="utf-8")
                before = self.store.read_bytes()
                self.assertIn("duplicate JSON field", self.cli("add", "New", code=1).stderr)
                self.assertEqual(self.store.read_bytes(), before)

    def test_unknown_schema_fields_rejected(self):
        """Unexpected fields cannot be silently discarded or accidentally interpreted."""
        self.cli("add", "Keep")
        base = self.read_store()
        values = [{**base, "future": 5},
                  {**base, "tasks": [{**base["tasks"][0], "priority": "high"}]}]
        for value in values:
            with self.subTest(value=value):
                self.store.write_text(json.dumps(value), encoding="utf-8")
                before = self.store.read_bytes()
                self.assertIn("fields must be exactly", self.cli("done", "1", code=1).stderr)
                self.assertEqual(self.store.read_bytes(), before)

    def test_storage_error_identifies_path(self):
        """Diagnostics identify which isolated ledger needs attention."""
        self.store.write_text("not json", encoding="utf-8")
        error = self.cli("list", code=1).stderr
        self.assertIn(str(self.store), error)
        self.assertIn("file left unchanged", error)

    def test_invalid_store_path(self):
        """Blank path arguments fail validation rather than opening the working directory."""
        for path in ("", "   "):
            with self.subTest(path=path):
                self.assertIn("PATH must not be blank", self.cli("list", "--store", path, code=2).stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_symlink_store_rejected(self):
        """Mutations never replace a symlink or change its target ledger."""
        target = self.directory / "real.json"
        self.cli("add", "Keep", store=target)
        before = target.read_bytes()
        self.store.symlink_to(target)
        for args in (("list",), ("add", "New")):
            self.assertIn("regular file", self.cli(*args, code=1).stderr)
        self.assertTrue(self.store.is_symlink())
        self.assertEqual(target.read_bytes(), before)

    def test_dangling_symlink_rejected(self):
        """A dangling symlink is not mistaken for a missing ledger."""
        target = self.directory / "missing.json"
        self.store.symlink_to(target)
        self.cli("add", "No", code=1)
        self.assertTrue(self.store.is_symlink())
        self.assertFalse(target.exists())

    def test_directory_store_rejected(self):
        """A directory path gets a clear error without altering its contents."""
        self.store.mkdir()
        self.assertIn("regular file", self.cli("list", code=1).stderr)
        self.cli("add", "No", code=1)
        self.assertEqual(list(self.store.iterdir()), [])

    def test_fifo_store_does_not_block(self):
        """A FIFO is rejected before a blocking read can occur."""
        os.mkfifo(self.store)
        self.assertIn("regular file", self.cli("list", code=1).stderr)
        self.assertTrue(stat.S_ISFIFO(self.store.stat().st_mode))

    def test_unicode_format_controls_rejected(self):
        """Unicode line separators and bidi formatting cannot spoof table rows."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        for control in ("\u2028", "\u2029", "\u202e", "\u2066", "\u200f"):
            with self.subTest(control=repr(control)):
                self.cli("add", "Bad" + control + "text", code=2)
                self.assertEqual(self.store.read_bytes(), before)

    def test_natural_rtl_and_combining_text_allowed(self):
        """Ordinary multilingual writing and emoji joiners remain valid descriptions."""
        for text in ("شراء الشاي", "שלום", "cafe\u0301", "👩‍💻"):
            with self.subTest(text=text):
                self.cli("add", text)
                self.assertIn(text, self.cli("list").stdout)

    def test_unsafe_stored_text_rejected(self):
        """Hand-edited unsafe text is rejected on reads as well as on capture."""
        data = {"version": 1, "next_id": 2,
                "tasks": [{"id": 1, "description": "spoof\u202etext", "done": False}]}
        self.store.write_text(json.dumps(data), encoding="utf-8")
        before = self.store.read_bytes()
        self.cli("list", code=1)
        self.assertEqual(self.store.read_bytes(), before)

    def test_concurrent_adds_keep_all_tasks(self):
        """Competing writer processes retain every task with unique monotonic IDs."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.cli("add", f"Concurrent {i}"), range(16)))
        data = self.read_store()
        self.assertEqual([task["id"] for task in data["tasks"]], list(range(1, 17)))
        self.assertEqual({task["description"] for task in data["tasks"]},
                         {f"Concurrent {i}" for i in range(16)})
        self.assertEqual(data["next_id"], 17)

    def test_lock_symlink_rejected(self):
        """A substituted lock symlink cannot redirect lock access to another file."""
        target = self.directory / "unrelated"
        target.write_text("untouched", encoding="utf-8")
        self.store.with_name(self.store.name + ".lock").symlink_to(target)
        self.cli("add", "No", code=1)
        self.assertEqual(target.read_text(), "untouched")
        self.assertFalse(self.store.exists())

    def test_file_modes_preserved(self):
        """New stores are private; atomic replacement keeps existing basic permissions."""
        self.cli("add", "Private")
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o600)
        self.store.chmod(0o640)
        self.cli("add", "Shared read")
        self.assertEqual(stat.S_IMODE(self.store.stat().st_mode), 0o640)

    def test_completion_rate(self):
        """Completion percentage is rounded and absent totals are explicitly not applicable."""
        self.assertRegex(self.cli("stats").stdout, r"Completion\s+n/a")
        for text in ("A", "B", "C"):
            self.cli("add", text)
        self.assertRegex(self.cli("stats").stdout, r"Completion\s+0%")
        self.cli("done", "1")
        self.assertRegex(self.cli("stats").stdout, r"Completion\s+33%")
        self.cli("done", "2")
        self.cli("done", "3")
        self.assertRegex(self.cli("stats").stdout, r"Completion\s+100%")

    def test_atomic_failures_preserve_original(self):
        """Faults before replacement preserve bytes and remove the incomplete temporary file."""
        self.cli("add", "Original")
        before = self.store.read_bytes(), self.store.stat().st_mtime_ns
        for target in ("os.replace", "os.fsync", "json.dump"):
            with self.subTest(target=target):
                bootstrap = (
                    "import sys; sys.path.insert(0, sys.argv.pop(1)); import taskman\n"
                    "from unittest.mock import patch\n"
                    f"with patch('taskman.{target}', side_effect=OSError('simulated write failure')):\n"
                    "    sys.exit(taskman.main())\n"
                )
                result = subprocess.run(
                    [sys.executable, "-c", bootstrap, str(SCRIPT.parent),
                     "--store", str(self.store), "add", "Not saved"],
                    cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=10
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertIn("simulated write failure", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual((self.store.read_bytes(), self.store.stat().st_mtime_ns), before)
                self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.cli("add", "Next")
        self.assertEqual(self.read_store()["tasks"][-1]["id"], 2)

    def test_lock_wait_has_a_deadline(self):
        """A held lock produces a bounded actionable error without blocking readers."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        with self.store.with_name("tasks.json.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for timeout in ("0", "0.15"):
                started = time.monotonic()
                error = self.cli("add", "No", "--lock-timeout", timeout, code=1).stderr
                self.assertLess(time.monotonic() - started, 2)
                self.assertIn("store is busy", error)
                self.assertIn("do not delete the lock", error)
                self.cli("list")
                self.assertEqual(self.store.read_bytes(), before)
        self.cli("add", "After release")
        self.assertEqual(self.read_store()["next_id"], 3)

    def test_invalid_lock_deadlines(self):
        """Nonfinite and negative timeout values fail before accessing storage."""
        for value in ("nan", "inf", "-1", "nope"):
            with self.subTest(value=value):
                self.cli("add", "No", "--lock-timeout", value, code=2)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_interrupt_while_waiting(self):
        """Ctrl-C during lock waiting returns 130 and leaves task bytes unchanged."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        marker = self.directory / "waiting"
        bootstrap = (
            "original_sleep = taskman.time.sleep\n"
            "def announce(seconds):\n"
            "    '''Announce that the child is blocked on a live writer lock.'''\n"
            "    taskman.Path('waiting').touch()\n"
            "    original_sleep(seconds)\n"
            "taskman.time.sleep = announce\n"
            "sys.exit(taskman.main())\n"
        )
        with self.store.with_name("tasks.json.lock").open("r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            child = self.start_child("add", "No", "--lock-timeout", "20", bootstrap=bootstrap)
            self.wait_for_marker(marker, child)
            child.send_signal(signal.SIGINT)
            output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 130, error)
        self.assertEqual(output, "")
        self.assertIn("interrupted", error)
        self.assertNotIn("Traceback", error)
        self.assertEqual(self.store.read_bytes(), before)
        self.cli("add", "Lock released")

    def test_interrupt_before_replace_cleans_up(self):
        """Ctrl-C before commit removes the temporary file and releases the writer lock."""
        self.cli("add", "Original")
        before = self.store.read_bytes()
        bootstrap = (
            "def pause(source, destination):\n"
            "    '''Pause just before the commit boundary.'''\n"
            "    taskman.Path('ready').touch()\n"
            "    taskman.time.sleep(30)\n"
            "taskman.os.replace = pause\n"
            "sys.exit(taskman.main())\n"
        )
        child = self.start_child("add", "Not saved", bootstrap=bootstrap)
        self.wait_for_marker(self.directory / "ready", child)
        child.send_signal(signal.SIGINT)
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 130, error)
        self.assertEqual(output, "")
        self.assertNotIn("Traceback", error)
        self.assertEqual(self.store.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.cli("add", "After cancellation")
        self.assertEqual(self.read_store()["next_id"], 3)

    def test_closed_stdout_is_quiet(self):
        """A closed pipe produces neither a traceback nor a second shutdown exception."""
        for args in (("list",), ("--help",), ("add", "Committed once")):
            with self.subTest(args=args):
                reader, writer = os.pipe()
                os.close(reader)
                try:
                    child = self.start_child(*args, stdout=writer)
                finally:
                    os.close(writer)
                _, error = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 1, error)
                self.assertEqual(error, "")
        self.assertEqual(len(self.read_store()["tasks"]), 1)

    def test_output_device_failure_warns_about_commit(self):
        """A failed confirmation must not imply that an already-saved addition was rolled back."""
        bootstrap = (
            "from unittest.mock import patch\n"
            "with patch.object(sys.stdout, 'write', side_effect=OSError('output device failed')):\n"
            "    sys.exit(taskman.main())\n"
        )
        child = self.start_child("add", "Saved", bootstrap=bootstrap)
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 1, error)
        self.assertEqual(output, "")
        self.assertIn("may already be saved", error)
        self.assertNotIn("Traceback", error)
        self.assertEqual(self.read_store()["tasks"][0]["description"], "Saved")

    def test_ascii_stdout_does_not_break_unicode_mutations(self):
        """ASCII-only output escapes unsupported glyphs but retains original JSON text."""
        output = self.cli("add", "买茶", env={"PYTHONIOENCODING": "ascii:strict"}).stdout
        self.assertIn(r"\u4e70\u8336", output)
        self.assertEqual(self.read_store()["tasks"][0]["description"], "买茶")
        self.cli("list", env={"PYTHONIOENCODING": "ascii:strict"})

    def test_wrapping_at_three_terminal_widths(self):
        """Narrow, normal and wide tables retain the full text under a clear continuation rail."""
        text = "Review the quarterly draft  " * 12 + "carefully."
        self.cli("add", text)
        for width in (40, 80, 120):
            with self.subTest(width=width):
                lines = self.cli("list", "--width", str(width)).stdout.splitlines()
                self.assertTrue(all(len(line) <= width for line in lines))
                self.assertEqual(lines[2][:12], " 1  open    ")
                self.assertTrue(all(line[:12] == " " * 12 for line in lines[3:]))
                self.assertEqual("".join(line[12:] for line in lines[2:]), text)
        self.assertEqual(self.cli("list").stdout, self.cli("list", "--width", "80").stdout)

    def test_unicode_wrapping_keeps_clusters(self):
        """CJK, combining marks and joined emoji wrap without breaking the demonstrated clusters."""
        examples = (("茶" * 35, 2), ("e\u0301" * 35, 1), ("👩‍💻" * 35, 2))
        for text, cells in examples:
            with self.subTest(text=text):
                self.cli("add", text)
                lines = self.cli("list", "--width", "24").stdout.splitlines()[2:]
                task_lines = lines[-((35 * cells + 11) // 12):]
                self.assertEqual("".join(line[12:] for line in task_lines), text)
                for line in task_lines:
                    fragment = line[12:]
                    if cells == 1:
                        self.assertTrue(fragment.endswith("\u0301"))
                        self.assertLessEqual(len(fragment) // 2, 12)
                    elif "\u200d" in text:
                        self.assertEqual(fragment.replace("👩‍💻", ""), "")
                        self.assertLessEqual(fragment.count("👩‍💻") * 2, 12)
                    else:
                        self.assertLessEqual(len(fragment) * 2, 12)

    def test_invalid_table_widths(self):
        """Unusable widths are rejected before reading the store."""
        for value in ("0", "23", "-1", "wide", "2.5"):
            self.cli("list", "--width", value, code=2)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invisible_only_descriptions_rejected(self):
        """Zero-width formatting and standalone marks do not create apparently blank tasks."""
        for text in ("\u200b", "\u200d", "\u0301", " \ufe0f "):
            with self.subTest(text=repr(text)):
                self.assertIn("visible text", self.cli("add", text, code=2).stderr)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_invisible_stored_description_preserved(self):
        """The visibility requirement also protects against blank-looking hand-edited tasks."""
        data = {"version": 1, "next_id": 2,
                "tasks": [{"id": 1, "description": "\u200b", "done": False}]}
        self.store.write_text(json.dumps(data), encoding="utf-8")
        before = self.store.read_bytes()
        self.cli("add", "No", code=1)
        self.assertEqual(self.store.read_bytes(), before)

    def test_abbreviated_options_rejected(self):
        """Typos cannot select a store or filter via implicit argparse abbreviation."""
        for args in (("--sto", str(self.store), "list"), ("list", "--sta", "open"),
                     ("add", "No", "--lock-t", "0")):
            self.cli(*args, code=2)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_noncanonical_id_syntax_rejected(self):
        """Python-specific numeric spellings are not silently treated as task IDs."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        for value in ("+1", " 1 ", "١", "１", "0_1"):
            for command in ("done", "rm"):
                self.cli(command, value, code=2)
        self.assertEqual(self.store.read_bytes(), before)
        self.cli("done", "01")

    def test_corruption_error_locates_task(self):
        """A damaged task is identified by one-based position and its known stable ID."""
        data = {"version": 1, "next_id": 10,
                "tasks": [{"id": 3, "description": "Keep", "done": False},
                          {"id": 9, "description": "Broken", "done": "yes"}]}
        self.store.write_text(json.dumps(data), encoding="utf-8")
        before = self.store.read_bytes()
        error = self.cli("list", code=1).stderr
        self.assertIn("position 2 (ID 9)", error)
        self.assertIn("done values must be true or false", error)
        self.assertEqual(self.store.read_bytes(), before)

    def test_long_store_basename(self):
        """A valid near-limit basename works for both atomic temporary files and locks."""
        for name in ("a" * 251, "茶" * 83):
            with self.subTest(name=name):
                selected = self.directory / name
                self.cli("add", "Long path", store=selected)
                self.cli("done", "1", store=selected)
                self.assertIn("Long path", self.cli("list", store=selected).stdout)
        self.assertEqual(len(list(self.directory.glob(".taskman-*.lock"))), 2)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_partial_serialization_failure_preserves_store(self):
        """A real partially written temporary file is removed after a serialization failure."""
        self.cli("add", "Original")
        before = self.store.read_bytes()
        bootstrap = (
            "def partial(data, handle, **kwargs):\n"
            "    '''Write and flush incomplete JSON before simulating a full device.'''\n"
            "    handle.write('{\"version\":'); handle.flush()\n"
            "    raise OSError('simulated device full after partial write')\n"
            "taskman.json.dump = partial\n"
            "sys.exit(taskman.main())\n"
        )
        child = self.start_child("add", "No", bootstrap=bootstrap)
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 1, error)
        self.assertEqual(output, "")
        self.assertIn("partial write", error)
        self.assertEqual(self.store.read_bytes(), before)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_readers_see_atomic_snapshots(self):
        """Readers see old complete JSON before replacement and new complete JSON afterwards."""
        self.cli("add", "Original")
        before = self.store.read_bytes()
        bootstrap = (
            "original_replace = taskman.os.replace\n"
            "def pause(source, destination):\n"
            "    '''Expose a deterministic observation window before commit.'''\n"
            "    taskman.Path('ready').touch()\n"
            "    while not taskman.Path('release').exists(): taskman.time.sleep(0.01)\n"
            "    original_replace(source, destination)\n"
            "taskman.os.replace = pause\n"
            "sys.exit(taskman.main())\n"
        )
        child = self.start_child("add", "New task", bootstrap=bootstrap)
        self.wait_for_marker(self.directory / "ready", child)
        self.assertEqual(self.store.read_bytes(), before)
        for _ in range(3):
            self.assertNotIn("New task", self.cli("list").stdout)
            self.cli("stats")
        (self.directory / "release").touch()
        output, error = child.communicate(timeout=5)
        self.assertEqual(child.returncode, 0, error)
        self.assertIn("Added task 2", output)
        self.assertIn("New task", self.cli("list").stdout)
        self.assertEqual(self.read_store()["next_id"], 3)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "requires non-root POSIX permissions")
    def test_readonly_store_cannot_be_replaced(self):
        """Atomic replacement respects a user's read-only file intent."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        self.store.chmod(0o400)
        try:
            self.cli("list")
            self.assertIn("Permission denied", self.cli("add", "No", code=1).stderr)
            self.assertEqual(self.store.read_bytes(), before)
        finally:
            self.store.chmod(0o600)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "requires non-root POSIX permissions")
    def test_readonly_directory_preserves_store(self):
        """A parent without write permission prevents replacement but still allows reads."""
        self.cli("add", "Keep")
        before = self.store.read_bytes()
        self.directory.chmod(0o500)
        try:
            self.cli("list")
            self.assertIn("Permission denied", self.cli("add", "No", code=1).stderr)
            self.assertEqual(self.store.read_bytes(), before)
        finally:
            self.directory.chmod(0o700)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0, "requires non-root POSIX permissions")
    def test_unreadable_store_preserved(self):
        """Unreadable JSON produces a concise error and remains intact."""
        self.cli("add", "Secret")
        before = self.store.read_bytes()
        self.store.chmod(0)
        try:
            self.assertIn("Permission denied", self.cli("list", code=1).stderr)
        finally:
            self.store.chmod(0o600)
        self.assertEqual(self.store.read_bytes(), before)


def project_metrics():
    """Recompute documentation counts from source and prose rather than cached claims."""
    root = SCRIPT.parent
    trees = [ast.parse((root / name).read_text(encoding="utf-8"))
             for name in ("taskman.py", "test_taskman.py")]
    definitions = [node for tree in trees for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    test_tree = trees[1]
    classes = {node.name: node for node in test_tree.body if isinstance(node, ast.ClassDef)}
    test_counts = {name: sum(isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
                             for node in cls.body) for name, cls in classes.items()}
    readme = (root / "README.md").read_text(encoding="utf-8")
    prose = re.sub(r"```.*?```", "", readme, flags=re.S)
    return {
        "Test cases": sum(test_counts.values()),
        "CLI subprocess cases": test_counts["TaskmanCLITests"],
        "Project-contract cases": test_counts["ProjectContractTests"],
        "Python source files": len(trees),
        "Documented definitions": sum(bool(ast.get_docstring(node)) for node in definitions),
        "README sections (H2)": len(re.findall(r"^## ", readme, re.M)),
        "README prose words": len(re.findall(r"\b[\w'-]+\b", prose)),
        "Walkthrough blocks": len(re.findall(r"```sh\n# walkthrough\n", readme)),
        "Output snapshots": len(re.findall(r"```text\n", readme)),
        "Review findings": len(re.findall(r"^\d+\. ", (root / "REVIEW.md").read_text(), re.M)),
    }


class ProjectContractTests(unittest.TestCase):
    """Keep executable documentation, review evidence and measured counts reproducible."""

    def test_readme_walkthroughs(self):
        """Execute the documented workflow, snapshots, schema and guarded recovery in isolation."""
        readme = SCRIPT.with_name("README.md").read_text(encoding="utf-8")
        blocks = [block for block in re.findall(r"```sh\n(.*?)```", readme, re.S)
                  if block.startswith("# walkthrough\n")]
        self.assertEqual(len(blocks), 5)
        prefix = shlex.quote(sys.executable) + " " + shlex.quote(str(SCRIPT))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            environment = dict(os.environ, HOME=temporary, TMPDIR=temporary,
                               PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")

            def shell(script, extra_env=None):
                """Run a literal walkthrough with only its interpreter/script location adapted."""
                return subprocess.run(
                    ["/bin/sh", "-eu"], input=script.replace("python3 taskman.py", prefix),
                    cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=30,
                    env={**environment, **(extra_env or {})}
                )

            result = shell("\n".join(blocks))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            snapshots = re.findall(r"```text\n(.*?)```", readme, re.S)
            self.assertEqual(len(snapshots), 2)
            for snapshot in snapshots:
                self.assertIn(snapshot, result.stdout)
            self.assertEqual(list(directory.glob("taskman-demo.*")), [])
            self.assertFalse((directory / ".taskman.json").exists())
            personal = json.loads((directory / "personal-tasks.json").read_text())
            self.assertEqual(personal["tasks"][0]["description"], "Arrange a weekend walk")

            example = re.findall(r"```json\n(.*?)```", readme, re.S)
            self.assertEqual(len(example), 1)
            selected = directory / "example.json"
            selected.write_text(example[0], encoding="utf-8")
            original = selected.read_bytes()
            validation = shell(f'python3 taskman.py --store {shlex.quote(str(selected))} list')
            self.assertEqual(validation.returncode, 0, validation.stderr)
            self.assertIn("Book the dentist", validation.stdout)
            self.assertEqual(selected.read_bytes(), original)

            recovery = next(block for block in blocks if "BACKUP=" in block)
            corrupted_recovery = recovery.replace(
                'cp -p "$STORE" "$BACKUP" &&',
                'cp -p "$STORE" "$BACKUP" &&\nprintf \'%s\' \'not json\' > "$BACKUP" &&'
            )
            self.assertNotEqual(recovery, corrupted_recovery)
            rejected = shell(corrupted_recovery, {"DEMO_DIR": temporary, "STORE": str(selected)})
            self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
            self.assertIn("corrupt storage", rejected.stderr)
            self.assertEqual(selected.read_bytes(), original)
            self.assertEqual(list(directory.glob("restore.*")), [])

    def test_documented_evidence(self):
        """Audit all six deliverables, source docstrings, exact review numbering and live counts."""
        root = SCRIPT.parent
        names = ("taskman.py", "test_taskman.py", "README.md", "DESIGN.md", "REVIEW.md", "QUALITY.md")
        texts = {name: (root / name).read_text(encoding="utf-8") for name in names}
        test_names = set()
        for name in names[:2]:
            tree = ast.parse(texts[name])
            self.assertTrue(ast.get_docstring(tree), name)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self.assertTrue(ast.get_docstring(node), f"{name}:{node.name}")
                    if node.name.startswith("test_"):
                        test_names.add(node.name)
        review = texts["REVIEW.md"]
        self.assertEqual(re.findall(r"^(\d+)\. ", review, re.M), list(map(str, range(1, 13))))
        entries = re.split(r"^\d+\. ", review, flags=re.M)[1:]
        for entry in entries:
            self.assertIn("**Fix:**", entry)
            self.assertIn("**Evidence:**", entry)
            references = set(re.findall(r"`(test_\w+)`", entry))
            self.assertTrue(references, entry)
            self.assertLessEqual(references, test_names)
        for name in names:
            self.assertIn(name, review)
        for phrase in ("Acceptance matrix", "40-", "80-", "120-", "Web quotas remain inapplicable"):
            self.assertIn(phrase, texts["DESIGN.md"])
        for label, count in project_metrics().items():
            self.assertIn(f"| {label} | {count} |", texts["QUALITY.md"])
        self.assertEqual(project_metrics()["Test cases"], len(test_names))


if __name__ == "__main__":
    unittest.main(verbosity=2)
