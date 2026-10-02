"""Day 4: durability, crash repair, persistence cursors, and child isolation.

Use real JSONL files and tool dispatch with a mocked model boundary. Explicitly
test resume and compaction regressions that would duplicate or orphan messages.
"""

import copy
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from chiikawa import Harness, Policy, Tool, tool
from chiikawa import context, provider, session
from chiikawa.subagent import subagent_tool


def reply(text="done", calls=None):
    """Build the provider-neutral response required by the existing loop."""
    return {"text": text, "tool_calls": calls or []}


def call(name="read_file", args=None, identifier="call_1"):
    """Create a correlated function call without any provider-global state."""
    return {"name": name, "args": args or {"path": "a.txt"}, "call_id": identifier}


class WorkspaceCase(unittest.TestCase):
    """Allocate an isolated workspace for every persistence test."""

    def setUp(self):
        """Create a temporary workspace and arrange cleanup after the test."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class SessionTests(WorkspaceCase):
    """Exercise the real file format, truncation repair, and call-result pairing."""

    def test_new_session_slug_unique_and_private(self):
        """Reserve unique timestamp names, sanitize labels, and use private permissions."""
        first = session.new_session(self.root, " ../Hello, WORLD! " + "a" * 80)
        second = session.new_session(self.root, " ../Hello, WORLD! " + "a" * 80)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root.resolve() / ".chiikawa/sessions")
        match = re.fullmatch(r"\d+\.\d{9}-([A-Za-z0-9-]{1,40})\.jsonl", first.name)
        self.assertIsNotNone(match)
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)
        self.assertTrue(session.new_session(self.root, "///").name.endswith("-session.jsonl"))

    def test_unicode_and_raw_provider_state_round_trip(self):
        """Keep every message field, including opaque output and phase metadata."""
        path = session.new_session(self.root)
        message = {"role": "assistant", "text": "猫", "tool_calls": [], "provider_output": [
            {"type": "message", "phase": "final_answer", "content": []},
            {"type": "reasoning", "encrypted_content": "opaque"}]}
        session.append(path, message)
        self.assertIn("猫", path.read_text())
        self.assertEqual(session.load(path), [message])
        self.assertEqual(len(path.read_text().splitlines()), 1)

    def test_torn_tail_stops_and_append_removes_it(self):
        """Ignore the first invalid line and make subsequent appends reachable on reload."""
        path = session.new_session(self.root)
        user = {"role": "user", "text": "before"}
        session.append(path, user)
        with path.open("ab") as target:
            target.write(b'{"role":"assistant","text":"\xe7\x8c\n{"role":"user","text":"ignored"}\n')
        damaged = path.read_bytes()
        self.assertEqual(session.load(path), [user])
        self.assertEqual(path.read_bytes(), damaged)  # load itself is read-only.
        after = {"role": "user", "text": "after"}
        session.append(path, after)
        self.assertEqual(session.load(path), [user, after])
        self.assertNotIn(b"ignored", path.read_bytes())

    def test_valid_last_line_without_newline(self):
        """Accept a complete JSON record and separate the next append correctly."""
        path = session.new_session(self.root)
        first = {"role": "user", "text": "first"}
        path.write_text(json.dumps(first))
        session.append(path, {"role": "assistant", "text": "second"})
        self.assertEqual(len(session.load(path)), 2)

    def test_repair_partial_batch_preserves_ids_and_existing_results(self):
        """Repair only missing calls from the last assistant, in declared order."""
        path = session.new_session(self.root)
        calls = [call(identifier=f"call_{i}") for i in range(3)]
        messages = [{"role": "user", "text": "read"},
                    {"role": "assistant", "text": "", "tool_calls": calls},
                    {"role": "tool", "name": "read_file", "call_id": "call_0", "text": "actual"}]
        for message in messages:
            session.append(path, message)
        repaired = session.load(path)
        self.assertEqual(repaired[:3], messages)
        self.assertEqual(repaired[3:], [
            {"role": "tool", "name": "read_file", "call_id": "call_1", "text": session.INTERRUPTED},
            {"role": "tool", "name": "read_file", "call_id": "call_2", "text": session.INTERRUPTED}])
        wire = provider._to_wire(repaired)
        self.assertEqual([item["call_id"] for item in wire if item.get("type") == "function_call_output"],
                         ["call_0", "call_1", "call_2"])

    def test_empty_complete_and_legacy_histories(self):
        """Handle empty logs, text-only conversations, and calls without provider IDs."""
        path = session.new_session(self.root)
        self.assertEqual(session.load(path), [])
        session.append(path, {"role": "user", "text": "hello"})
        self.assertEqual(len(session.load(path)), 1)
        session.append(path, {"role": "assistant", "text": "", "tool_calls": [{"name": "legacy", "args": {}}]})
        self.assertEqual(session.load(path)[-1], {"role": "tool", "name": "legacy", "text": session.INTERRUPTED})

    def test_latest_uses_mtime_and_ignores_non_sessions(self):
        """Choose the newest JSONL file, returning None without creating a directory."""
        self.assertIsNone(session.latest(self.root))
        self.assertFalse((self.root / session.SESSION_DIR).exists())
        older = session.new_session(self.root)
        newer = session.new_session(self.root)
        os.utime(older, ns=(100, 100))
        os.utime(newer, ns=(200, 200))
        (newer.parent / "notes.txt").write_text("ignore")
        self.assertEqual(session.latest(self.root), newer)

    def test_invalid_record_and_sync_failures_are_visible(self):
        """Never treat a semantic corruption or disk-sync failure as successful durability."""
        path = session.new_session(self.root)
        path.write_text('42\n')
        with self.assertRaisesRegex(ValueError, "Invalid session message"):
            session.load(path)
        path.write_text("")
        with patch.object(session.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                session.append(path, {"role": "user", "text": "input"})

    def test_default_session_directory_cannot_escape_workspace(self):
        """Do not follow a session-directory symlink into another project's files."""
        with tempfile.TemporaryDirectory() as outside:
            (self.root / ".chiikawa").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(PermissionError):
                session.new_session(self.root)
            with self.assertRaises(PermissionError):
                session.latest(self.root)


class SubagentTests(unittest.TestCase):
    """Keep delegation bounds independent of model behavior."""

    def test_schema_fresh_factory_and_final_report(self):
        """Pass only the task to a fresh child and return its final report."""
        children = [Mock(), Mock()]
        children[0].run.return_value = "first report"
        children[1].run.return_value = "second report"
        factory = Mock(side_effect=children)
        spawn = subagent_tool(factory)
        self.assertIsInstance(spawn, Tool)
        self.assertEqual(spawn.name, "spawn_agent")
        self.assertEqual(spawn.spec["schema"]["parameters"]["required"], ["task"])
        self.assertIn("cannot see this conversation", spawn.spec["schema"]["description"])
        self.assertEqual(spawn.run(task="one"), "first report")
        self.assertEqual(spawn.run(task="two"), "second report")
        self.assertEqual([entry.args for entry in factory.call_args_list], [(1,), (1,)])
        children[0].run.assert_called_once_with("one")

    def test_depth_limit_prevents_construction(self):
        """At or beyond the ceiling, never create a child or invoke its model."""
        for depth in (2, 3):
            factory = Mock()
            result = subagent_tool(factory, depth=depth).run(task="task")
            self.assertEqual(result, "ERROR: sub-agent depth limit reached; do this task yourself")
            factory.assert_not_called()


class HarnessTests(WorkspaceCase):
    """Test full composition with durable events and transient context changes."""

    def test_defaults_exports_and_composition(self):
        """Honor explicit model, then environment, and expose all requested primitives."""
        with patch.dict(os.environ, {"CHIIKAWA_MODEL": "environment"}):
            harness = Harness(self.root)
            self.assertEqual(harness.model, "environment")
            self.assertEqual(Harness(self.root, model="explicit").model, "explicit")
        self.assertEqual(harness.policy.mode, "yolo")
        self.assertEqual(harness.budget_tokens, 600_000)
        self.assertEqual(harness.max_turns, 120)
        self.assertIn("remember", harness.tools)
        self.assertIn("spawn_agent", harness.tools)
        self.assertNotIn("use_skill", harness.tools)
        self.assertFalse((self.root / session.SESSION_DIR).exists())
        self.assertTrue(callable(tool))

    def test_skills_and_extra_tools_merge(self):
        """Add skills conditionally and let explicitly supplied tools override names."""
        path = self.root / "skills/brand-voice/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\ndescription: Writing\n---\nPirate speech")
        replacement = Tool("read_file", {"schema": {}}, lambda **_: "override")
        harness = Harness(self.root, extra_tools=[replacement], system_extra="Additional context", enable_subagents=False)
        self.assertIs(harness.tools["read_file"], replacement)
        self.assertIn("use_skill", harness.tools)
        self.assertNotIn("spawn_agent", harness.tools)
        self.assertIn("Skills available", harness.system)
        self.assertTrue(harness.system.endswith("Additional context"))

    def test_every_message_is_durable_before_external_event(self):
        """Observers always see a fully synced assistant or tool record on disk."""
        (self.root / "a.txt").write_text("data")
        observed = []
        harness = None

        def observe(kind, payload):
            """Inspect the durable journal at each callback boundary."""
            persisted = session._read(harness.session_path)
            observed.append(kind)
            if kind == "assistant":
                self.assertEqual(persisted[-1], payload)
            if kind == "tool_end":
                self.assertEqual(persisted[-1], payload)

        harness = Harness(self.root, on_event=observe)
        with patch.object(provider, "complete", side_effect=[reply("", [call()]), reply()]):
            self.assertEqual(harness.run("read"), "done")
        self.assertEqual(observed, ["assistant", "tool_start", "tool_end", "assistant"])
        self.assertEqual([message["role"] for message in session.load(harness.session_path)],
                         ["user", "assistant", "tool", "assistant"])

    def test_resume_does_not_reappend_history_and_repairs_once(self):
        """Persist only missing results, remove torn bytes, and keep future appends reachable."""
        path = session.new_session(self.root)
        original = [{"role": "user", "text": "task"},
                    {"role": "assistant", "text": "", "tool_calls": [call()]}]
        for message in original:
            session.append(path, message)
        with path.open("ab") as damaged:
            damaged.write(b'{"role":"tool"')
        harness = Harness(self.root)
        self.assertTrue(harness.resume())
        self.assertEqual(len(session._read(path)), 3)
        self.assertTrue(harness.resume(path))
        self.assertEqual(len(session._read(path)), 3)
        with patch.object(provider, "complete", return_value=reply("continued")):
            self.assertEqual(harness.run("continue the task"), "continued")
        persisted = session.load(path)
        self.assertEqual(persisted[:2], original)
        self.assertEqual(len(persisted), 5)
        self.assertEqual(sum(message.get("text") == session.INTERRUPTED for message in persisted), 1)
        self.assertEqual(len(list(path.parent.glob("*.jsonl"))), 1)

    def test_compaction_keeps_append_journal_without_duplicates(self):
        """Pruning the model view must neither duplicate old events nor skip new ones."""
        (self.root / "a.txt").write_text("data")
        main_calls = []
        summaries = []

        def complete(model, system, messages, tools):
            """Generate enough tool turns to trigger two real compaction passes."""
            if system == context.SUMMARY_SYSTEM:
                summaries.append(copy.deepcopy(messages))
                return reply("Previous task: read a.txt; continue.")
            main_calls.append(copy.deepcopy(messages))
            return reply("", [call(identifier=f"call_{len(main_calls)}")]) if len(main_calls) <= 6 else reply()

        harness = Harness(self.root, budget_tokens=0)
        with patch.object(provider, "complete", side_effect=complete):
            harness.run("read repeatedly")
        persisted = session._read(harness.session_path)
        self.assertTrue(summaries)
        self.assertEqual(len(persisted), 14)
        self.assertEqual(sum(message["role"] == "user" for message in persisted), 1)
        self.assertFalse(any(message.get("text", "").startswith("[Conversation so far") for message in persisted))
        self.assertLess(len(harness.messages), len(persisted))
        restored = Harness(self.root)
        self.assertTrue(restored.resume())
        self.assertEqual(restored.messages, persisted)
        with patch.object(provider, "complete", return_value=reply("next")):
            restored.run("new task")
        self.assertEqual(len(session._read(harness.session_path)), 16)

    def test_turn_limit_user_and_final_reply_are_persisted(self):
        """The otherwise silent wrap-up message must be flushed before the final request."""
        harness = Harness(self.root, max_turns=0)
        with patch.object(provider, "complete", return_value=reply("wrap up")):
            harness.run("task")
        self.assertEqual([message["text"] for message in session.load(harness.session_path)],
                         ["task", "Turn limit reached; wrap up now.", "wrap up"])

    def test_provider_and_persistence_failures(self):
        """Save input before provider failure; never call the model after a failed journal write."""
        harness = Harness(self.root)
        with patch.object(provider, "complete", side_effect=RuntimeError("network")):
            with self.assertRaisesRegex(RuntimeError, "network"):
                harness.run("task")
        self.assertEqual(session.load(harness.session_path), [{"role": "user", "text": "task"}])
        another = Harness(self.root)
        with patch.object(session, "append", side_effect=OSError("disk")), patch.object(provider, "complete") as complete:
            with self.assertRaisesRegex(OSError, "disk"):
                another.run("task")
        complete.assert_not_called()

    def test_children_clean_context_same_policy_and_no_sessions(self):
        """Two child invocations share files/configuration but not parent or sibling messages."""
        policy = Policy("read-only")
        observed = []

        def complete(model, system, messages, tools):
            """Inspect each freshly constructed child at its first model call."""
            observed.append(copy.deepcopy(messages))
            self.assertEqual(model, "child-model")
            self.assertIn("custom system", system)
            return reply("child report")

        parent = Harness(self.root, model="child-model", policy=policy, system_extra="custom system")
        parent.messages = [{"role": "user", "text": "parent-only secret context"}]
        with patch.object(provider, "complete", side_effect=complete), patch.object(session, "new_session") as new:
            self.assertEqual(parent.tools["spawn_agent"].run(task="first child"), "child report")
            self.assertEqual(parent.tools["spawn_agent"].run(task="second child"), "child report")
        new.assert_not_called()
        self.assertEqual(observed, [[{"role": "user", "text": "first child"}],
                                    [{"role": "user", "text": "second child"}]])
        self.assertFalse((self.root / session.SESSION_DIR).exists())

    def test_child_policy_is_enforced(self):
        """A child inherits the parent's write restrictions, including when invoked directly."""
        parent = Harness(self.root, policy=Policy("read-only"))
        seen = []

        def complete(model, system, messages, tools):
            """Attempt a write once, then inspect the child-visible denial."""
            seen.append(copy.deepcopy(messages))
            return reply("", [call("write_file", {"path": "blocked", "content": "x"})]) if len(seen) == 1 else reply()

        with patch.object(provider, "complete", side_effect=complete):
            parent.tools["spawn_agent"].run(task="write a file")
        self.assertTrue(seen[-1][-1]["text"].startswith("BLOCKED:"))
        self.assertFalse((self.root / "blocked").exists())

    def test_persist_false_and_missing_resume(self):
        """Ephemeral runs create no logs and absence of a session is an ordinary false result."""
        harness = Harness(self.root, persist=False)
        self.assertFalse(harness.resume())
        with patch.object(provider, "complete", return_value=reply()):
            harness.run("task")
        self.assertIsNone(harness.session_path)
        self.assertFalse((self.root / session.SESSION_DIR).exists())


if __name__ == "__main__":
    unittest.main()
