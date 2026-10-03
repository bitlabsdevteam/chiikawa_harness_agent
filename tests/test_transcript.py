"""Public transcript rendering, durable replay, and real terminal interaction."""

import contextlib
import io
import os
from pathlib import Path
import tempfile
import time
import shlex
import unittest
from unittest.mock import patch

from chiikawa import Harness, cli, provider, session
from chiikawa.transcript import TranscriptDisplay, wrapped
from chiikawa.terminal import TerminalDisplay
from chiikawa.prompt import width
import test_commands


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.output, self.answers = io.StringIO(), io.StringIO()
        self.display = TranscriptDisplay(self.output, self.answers)
        self.addCleanup(self.display.close)

    def test_working_covers_every_nonterminal_event_in_both_plain_layouts(self):
        call = {"name": "read_file", "call_id": "read", "args": {"path": "app.py"}}
        events = [
            ("model_start", {"model": "test"}),
            ("context", {"estimated_tokens": 10, "threshold": 100, "can_compact": False}),
            ("compaction_start", {"threshold": 100}),
            ("compaction", {"tokens_before": 110, "tokens_after": 20}),
            ("model_end", {"ok": True}),
            ("reasoning", {"text": "Public summary"}),
            ("assistant", {"text": "I will read the file.", "tool_calls": [call]}),
            ("tool_start", call),
            ("tool_end", {**call, "text": "content", "status": "done"}),
            ("usage", {"input": 20, "output": 3, "max_output_tokens": 100}),
        ]
        for display_type in (TerminalDisplay, TranscriptDisplay):
            with self.subTest(layout=display_type.__name__):
                output, answer = io.StringIO(), io.StringIO()
                display = display_type(output, answer)
                with display.task("test"):
                    self.assertIn("Working...", output.getvalue())
                    started = display.started
                    for kind, payload in events:
                        before = len(output.getvalue())
                        display(kind, payload)
                        self.assertIn("Working...", output.getvalue()[before:], kind)
                        self.assertEqual(display.started, started, kind)
                    display("assistant", {"text": "Finished", "tool_calls": []})
                    before = len(output.getvalue())
                    display("usage", {"input": 1, "output": 1, "max_output_tokens": 100})
                    self.assertNotIn("Working...", output.getvalue()[before:])
                self.assertEqual(answer.getvalue(), "Finished\n")
                self.assertIsNone(display.worker)
                self.assertNotIn("\033", output.getvalue())

    def test_current_action_is_visible_safe_and_approval_is_explicit(self):
        with self.display.task("update"):
            for name, verb in (("read_file", "Reading"), ("write_file", "Writing"), ("edit_file", "Editing")):
                call = {"name": name, "call_id": name, "args": {"path": "app\033[2J.py", "content": "UNWRITTEN"}}
                before = len(self.output.getvalue())
                self.display("tool_start", call)
                self.assertIn(f"{verb} app\\x1b[2J.py", self.output.getvalue()[before:])
                self.display("tool_end", {**call, "status": "blocked", "text": "BLOCKED: denied"})
            self.assertNotIn("UNWRITTEN", self.output.getvalue())
            with self.display.approval():
                self.assertIn("Working...", self.output.getvalue())
                self.assertIn("Waiting for approval", self.output.getvalue())
        self.assertNotIn("\033", self.output.getvalue())

    def test_live_history_paging_does_not_skip_output_with_status_header(self):
        self.display.restore([
            {"role": "assistant", "text": "", "tool_calls": [
                {"name": "bash", "call_id": "a", "args": {"command": "report"}}]},
            {"role": "tool", "name": "bash", "call_id": "a", "status": "done",
             "text": "\n".join(f"ROW-{i:02d}" for i in range(40))}])
        self.display.tty = self.display.running = self.display.viewing = True
        with patch.object(self.display, "size", return_value=(80, 12)):
            self.display._paint()
            def visible_numbers():
                return [int(text.strip()[4:]) for text, _ in self.display.last_frame[2]
                        if text.strip().startswith("ROW-")]
            bottom_page = visible_numbers()
            self.display._key(b"\x1b[5~")
            previous_page = visible_numbers()
        self.assertEqual(previous_page[-1] + 1, bottom_page[0])

    def test_grouped_exploration_command_preview_and_public_expansion(self):
        with self.display.task("Inspect the project"):
            for index, name in enumerate(("read_file", "grep")):
                call = {"name": name, "call_id": str(index), "args": {"path": "app.py", "regex": "enterprise", "pattern": "README.md"}}
                self.display("tool_start", call)
                self.display("tool_end", {**call, "text": "DETAIL-ONLY", "status": "done"})
            call = {"name": "bash", "call_id": "shell", "args": {"command": "git status --short"}}
            self.display("tool_start", call)
            self.display("tool_end", {**call, "status": "done", "details": {"exit_code": 0},
                                      "text": "\n".join(f"file-{i}" for i in range(36))})
            self.display("assistant", {"text": "Finished.", "provider_output": "PRIVATE", "openrouter_message": "OPAQUE"})
        compact = self.output.getvalue()
        self.assertEqual(compact.count("• Explored"), 1)
        self.assertIn("Read app.py", compact)
        self.assertIn("Search README.md for enterprise", compact)
        self.assertIn("• Ran git status --short", compact)
        self.assertIn("+ 33 lines (ctrl+t to expand)", compact)
        self.assertNotIn("DETAIL-ONLY", compact)
        self.assertNotIn("file-35", compact)
        self.assertEqual(self.answers.getvalue(), "Finished.\n")
        self.display.show_history()
        expanded = self.output.getvalue()
        self.assertIn("DETAIL-ONLY", expanded)
        self.assertIn("file-35", expanded)
        self.assertNotIn("PRIVATE", expanded)
        self.assertNotIn("OPAQUE", expanded)
        self.assertNotIn("\033", expanded)

    def test_replay_retains_failures_and_diffs_without_reexecuting(self):
        replies = [
            {"text": "", "tool_calls": [
                {"name": "bash", "call_id": "a", "args": {"command": "printf failure; exit 7"}},
                {"name": "write_file", "call_id": "b", "args": {"path": "a.txt", "content": "new\n"}}]},
            {"text": "Report", "tool_calls": []}]
        agent = Harness(self.root, activity=True, on_event=self.display)
        with patch.object(provider, "complete", side_effect=replies), self.display.task("Work"):
            agent.run("Work")
        messages = session.load(agent.session_path)
        failed = next(m for m in messages if m.get("call_id") == "a")
        self.assertEqual(failed["status"], "error")
        self.assertEqual(failed["details"]["exit_code"], 7)
        before = agent.session_path.read_bytes()
        with patch.object(provider, "complete") as request:
            self.display.restore(messages)
            self.display.show_history()
        request.assert_not_called()
        self.assertEqual(agent.session_path.read_bytes(), before)
        self.assertIn("Failed: Ran printf failure; exit 7 · exit 7", self.output.getvalue())
        self.assertIn("+new", self.output.getvalue())

    def test_legacy_replay_never_invents_exit_success_or_displays_wire_state(self):
        self.display.restore([
            {"role": "user", "text": "task"},
            {"role": "assistant", "text": "", "reasoning_summary": "Public summary", "provider_output": "PRIVATE",
             "openrouter_message": "OPAQUE", "tool_calls": [{"name": "bash", "call_id": "a", "args": {"command": "test"}}]},
            {"role": "tool", "call_id": "a", "text": "failed in legacy log"}])
        self.display.show_history()
        self.assertIn("exit status not recorded", self.output.getvalue())
        self.assertIn("Public summary", self.output.getvalue())
        self.assertNotIn("PRIVATE", self.output.getvalue())
        self.assertNotIn("OPAQUE", self.output.getvalue())

    def test_controls_width_and_full_long_commands(self):
        command = "echo\t" + "猫e\u0301" * 90 + "\033]52;unsafe\a"
        self.display.restore([
            {"role": "assistant", "text": "", "tool_calls": [{"name": "bash", "call_id": "a", "args": {"command": command}}]},
            {"role": "tool", "call_id": "a", "text": "\033[2Jbad", "status": "error"}])
        with patch.object(self.display, "size", return_value=(30, 10)):
            rows = self.display.rows(self.display.entries, expanded=True)
        self.assertTrue(all(width(text) <= 30 for text, _ in rows))
        self.assertTrue(all(width(row) <= 7 for row in wrapped("猫e\u0301猫e\u0301" * 9, 7)))
        self.assertNotIn("\033", "".join(text for text, _ in rows))
        self.assertIn("unsafe", "".join(text[2:] for text, _ in rows))

    def test_compact_cli_stdout_approval_and_plain_fallback(self):
        reply = {"text": "Done", "tool_calls": [], "usage": {"input": 20, "output": 5}}
        with contextlib.redirect_stdout(self.answers), contextlib.redirect_stderr(self.output), \
             patch.object(provider, "complete", return_value=reply):
            self.assertEqual(cli.main(["-d", str(self.root), "--display", "compact", "-p", "hello"]), 0)
        self.assertEqual(self.answers.getvalue(), "Done\n")
        self.assertNotIn("\033", self.output.getvalue())
        self.display("usage", {"input": None, "output": 5})
        self.assertIn("partial", self.display.status())

    def test_answer_finishing_while_history_open_is_delivered_once(self):
        self.display.viewing = True
        self.display("assistant", {"text": "Final answer", "tool_calls": []})
        self.assertFalse(self.display.viewing)
        self.assertEqual(self.answers.getvalue(), "Final answer\n")
        self.display.close()
        self.assertEqual(self.answers.getvalue(), "Final answer\n")

    def test_nested_calls_with_reused_ids_and_blocked_changes(self):
        with self.display.task("delegate"):
            parent = {"name": "spawn_agent", "call_id": "same", "args": {"task": "inspect"}}
            child = {"name": "write_file", "call_id": "same", "args": {"path": "a.txt", "content": "UNWRITTEN"}}
            self.display("tool_start", parent)
            self.display("tool_start", child)
            self.display("tool_end", {**child, "status": "blocked", "text": "BLOCKED: read-only"})
            self.display("tool_end", {**parent, "status": "done", "text": "Child reported the denial."})
        self.assertEqual(self.display.entries[1]["status"], "done")
        self.assertEqual(self.display.entries[2]["status"], "blocked")
        self.display.show_history()
        self.assertIn("Blocked: Write a.txt", self.output.getvalue())
        self.assertNotIn("UNWRITTEN", self.output.getvalue())
        self.assertEqual(self.display.pending, {})


@unittest.skipUnless(os.name == "posix", "Requires a POSIX terminal")
class TranscriptTerminalTests(unittest.TestCase):
    start = test_commands.TerminalTests.start
    cleanup_terminal = test_commands.TerminalTests.cleanup_terminal
    read_until = test_commands.TerminalTests.read_until
    send = test_commands.TerminalTests.send
    assert_restored = test_commands.TerminalTests.assert_restored

    def script(self, body, options=""):
        return """import tempfile, time
from unittest.mock import patch
from chiikawa import cli, provider
count = 0
def complete(*args, **kwargs):
    global count
    count += 1
""" + body + """
with tempfile.TemporaryDirectory() as root, patch.object(provider, 'complete', side_effect=complete):
    raise SystemExit(cli.main(['-d', root, """ + options + "]))\n"

    def test_live_expansion_scroll_resize_and_idle_history_preserve_draft(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        release = Path(temporary.name) / 'finish'
        script = self.script("""    if count == 1:
        return {'text': '', 'tool_calls': [{'name': 'bash', 'call_id': 'a', 'args': {'command': "printf 'one\\ntwo\\nthree\\nfour\\nfive\\nsix\\n'"}}]}
    from pathlib import Path
    deadline = time.monotonic() + 10
    while not Path(RELEASE).exists() and time.monotonic() < deadline:
        time.sleep(.02)
    return {'text': 'FINAL-ANSWER', 'tool_calls': []}
""".replace('RELEASE', repr(str(release))), "'--mode', 'yolo'")
        self.start(script=script, height=16)
        self.send("inspect\r")
        self.read_until(b"ctrl+t to expand")
        self.send("\x14")
        expanded = self.read_until(b"ctrl+t / esc back")
        self.assertIn(b"six", expanded)
        self.assertIn(b"Working...", expanded)
        self.send("\x1b[H")
        self.read_until(b"Transcript")
        import fcntl, struct, termios
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack('HHHH', 12, 48, 0, 0))
        self.send("\x1b[6~\x14")
        self.read_until(b"\033[?1049l")
        release.touch()
        self.assertIn(b"FINAL-ANSWER", self.read_until(b"chiikawa> "))
        self.send("draft\x14")
        self.read_until(b"Transcript")
        self.send("\x14")
        self.read_until(b"chiikawa> draft")
        self.send("\x15/exit\r")
        self.assert_restored()

    def test_safe_approval_owns_input_then_final_result_is_visible(self):
        script = self.script("""    if count == 1:
        return {'text': '', 'tool_calls': [{'name': 'write_file', 'call_id': 'a', 'args': {'path': 'note.txt', 'content': 'saved\\n'}}]}
    return {'text': 'WRITE-FINISHED', 'tool_calls': []}
""")
        self.start(script=script)
        self.send("write\r")
        approval = self.read_until(b"approve write_file? [y/a/N]")
        self.assertIn(b"Waiting for approval", approval)
        self.send("y\r")
        result = self.read_until(b"WRITE-FINISHED")
        self.assertIn(b"+1 -0", result)
        self.send("/exit\r")
        self.assert_restored()

    def test_verbose_working_indicator_survives_shell_execution(self):
        script = self.script("""    if count == 1:
        return {'text': '', 'tool_calls': [{'name': 'bash', 'call_id': 'a', 'args': {'command': 'sleep 0.4; printf TOOL-FINISHED'}}]}
    return {'text': 'TASK-FINISHED', 'tool_calls': []}
""", "'--display', 'verbose', '--mode', 'yolo'")
        self.start(script=script)
        self.send("work\r")
        output = self.read_until(b"TASK-FINISHED")
        self.assertIn(b"Working...", output)
        self.assertIn(b"Running sleep 0.4", output)
        self.assertIn(b"Reviewing tool result", output)
        self.assertIn(b"TOOL-FINISHED", output)
        self.send("/exit\r")
        self.assert_restored()

    def test_escape_interrupt_restores_terminal_and_keeps_recoverable_session(self):
        script = self.script("""    time.sleep(30)
    return {'text': 'SHOULD-NOT-FINISH', 'tool_calls': []}
""")
        self.start(script=script)
        self.send("work\r")
        self.read_until(b"esc to interrupt")
        self.send("\x1b")
        self.read_until(b"Interrupted.")
        self.assert_restored(130)

    def test_escape_stops_shell_descendants(self):
        with tempfile.TemporaryDirectory() as root:
            started, late = Path(root) / 'started', Path(root) / 'late'
            command = f"touch {shlex.quote(str(started))}; sleep 1; touch {shlex.quote(str(late))}"
            script = self.script(f"""    if count == 1:
        return {{'text': '', 'tool_calls': [{{'name': 'bash', 'call_id': 'a', 'args': {{'command': {command!r}}}}}]}}
    return {{'text': 'DONE', 'tool_calls': []}}
""", "'--mode', 'yolo'")
            self.start(script=script)
            self.send("work\r")
            self.read_until(b"Running")
            deadline = time.monotonic() + 3
            while not started.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue(started.exists())
            self.send("\x1b")
            self.read_until(b"Interrupted.")
            self.assert_restored(130)
            time.sleep(1.1)
            self.assertFalse(late.exists())


if __name__ == "__main__":
    unittest.main()
