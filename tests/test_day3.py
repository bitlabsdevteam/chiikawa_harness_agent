"""Day 3: regression checks for compaction boundaries, persistent facts, and skills.

Mock only the model boundary. Exercise real history transformations, disk writes,
concurrent appenders, fresh processes, and skill discovery in disposable roots.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

from chiikawa import context, memory, provider, skills
from demos.day3_context import run_task


def history(count=12):
    """Create a visible alternating transcript with deterministic message labels."""
    return [{"role": "user" if index % 2 == 0 else "assistant", "text": f"message {index}"}
            for index in range(count)]


class ContextTests(unittest.TestCase):
    """Verify identity, lossless retained state, and valid tool boundaries."""

    def test_estimate_exact_contract(self):
        """Include the complete dictionary representation, even opaque provider fields."""
        messages = [{"role": "assistant", "text": "猫", "provider_output": [{"encrypted_content": "opaque"}]}]
        self.assertEqual(context.CHARS_PER_TOKEN, 4)
        self.assertEqual(context.KEEP_RECENT, 6)
        self.assertEqual(context.estimate_tokens(messages), len(str(messages[0])) / 4)
        self.assertEqual(context.estimate_tokens([]), 0)

    def test_noop_identity_and_threshold(self):
        """Avoid model calls for short histories or histories at or below budget."""
        with patch.object(context.provider, "complete") as complete:
            for messages, budget in (([], 0), (history(7), 0), (history(), 10000),
                                     (history(), context.estimate_tokens(history()))):
                self.assertIs(context.compact("model", messages, budget), messages)
        complete.assert_not_called()

    def test_summary_contract_and_recent_state(self):
        """Make one tool-free summary call and preserve the exact recent message objects."""
        messages = history()
        messages[0]["text"] = "Create files; unresolved error; remaining work"
        messages[-1]["provider_output"] = [{"type": "reasoning", "encrypted_content": "retain-me"}]
        original = copy.deepcopy(messages)
        with patch.object(context.provider, "complete", return_value={"text": "summary", "tool_calls": []}) as complete:
            result = context.compact("model", messages, 0)
        self.assertEqual(messages, original)
        self.assertEqual(result[0], {"role": "user", "text": "[Conversation so far, compacted]\nsummary"})
        for actual, expected in zip(result[1:], messages[-6:]):
            self.assertIs(actual, expected)
        complete.assert_called_once()
        self.assertEqual(complete.call_args.args[:2], ("model", context.SUMMARY_SYSTEM))
        self.assertEqual(complete.call_args.args[3], [])
        transcript = complete.call_args.args[2]
        self.assertEqual(transcript[0]["role"], "user")
        self.assertIn("Create files; unresolved error; remaining work", transcript[0]["text"])
        self.assertNotIn("message 6", transcript[0]["text"])

    def test_orphan_results_are_summarized_not_discarded(self):
        """When a cut bisects a tool group, preserve every result in the old transcript."""
        messages = history(10)
        messages[3] = {"role": "assistant", "text": "", "tool_calls": [
            {"name": "write_file", "args": {"path": "a.txt"}, "call_id": "a"},
            {"name": "write_file", "args": {"path": "b.txt"}, "call_id": "b"}]}
        messages[4] = {"role": "tool", "name": "write_file", "text": "Wrote a.txt", "call_id": "a"}
        messages[5] = {"role": "tool", "name": "write_file", "text": "ERROR writing b.txt", "call_id": "b"}
        with patch.object(context.provider, "complete", return_value={"text": "state", "tool_calls": []}) as complete:
            result = context.compact("model", messages, 0)
        transcript = complete.call_args.args[2][0]["text"]
        self.assertIn("tool call: write_file", transcript)
        self.assertIn("tool (write_file): Wrote a.txt", transcript)
        self.assertIn("ERROR writing b.txt", transcript)
        self.assertEqual(result[1:], messages[6:])
        provider._to_wire(result)

    def test_all_recent_results_can_be_absorbed(self):
        """A large tool batch may consume the whole retained slice without leaving orphans."""
        messages = history(2) + [{"role": "tool", "name": "read_file", "text": f"result {i}"}
                                 for i in range(6)]
        with patch.object(context.provider, "complete", return_value={"text": "all results", "tool_calls": []}) as complete:
            result = context.compact("model", messages, 0)
        self.assertEqual(len(result), 1)
        self.assertIn("result 5", complete.call_args.args[2][0]["text"])

    def test_clipping_and_no_opaque_reasoning_in_summary(self):
        """Keep model input bounded per visible field and do not serialize provider internals."""
        messages = history()
        messages[0] = {"role": "assistant", "text": "x" * 5000,
                       "provider_output": [{"encrypted_content": "never-summarize-this"}],
                       "tool_calls": [{"name": "write_file", "args": {"path": "a", "content": "y" * 5000}}]}
        with patch.object(context.provider, "complete", return_value={"text": "summary"}) as complete:
            context.compact("model", messages, 0)
        transcript = complete.call_args.args[2][0]["text"]
        self.assertNotIn("never-summarize-this", transcript)
        self.assertIn(" [clipped]", transcript)
        self.assertNotIn("x" * 2001, transcript)
        self.assertNotIn("y" * 1001, transcript)
        self.assertIn('"path": "a"', transcript)

    def test_failed_or_invalid_summary_preserves_input(self):
        """Fail visibly without corrupting history when the summarizer cannot finish."""
        messages = history()
        original = copy.deepcopy(messages)
        for response in ({"text": ""}, {"text": " "}, {"text": "text", "tool_calls": [{"name": "bad"}]}):
            with patch.object(context.provider, "complete", return_value=response):
                with self.assertRaises(RuntimeError):
                    context.compact("model", messages, 0)
            self.assertEqual(messages, original)
        with patch.object(context.provider, "complete", side_effect=RuntimeError("network")):
            with self.assertRaisesRegex(RuntimeError, "network"):
                context.compact("model", messages, 0)
        self.assertEqual(messages, original)

    def test_repeated_compaction_keeps_previous_summary(self):
        """Feed the earlier factual summary into the next compaction, preserving task continuity."""
        with patch.object(context.provider, "complete", side_effect=[{"text": "Original task and files"},
                                                                    {"text": "Updated task and files"}]) as complete:
            first = context.compact("model", history(), 0)
            second = context.compact("model", first + history(4), 0)
        self.assertIn("Original task and files", complete.call_args.args[2][0]["text"])
        self.assertEqual(second[0]["text"], "[Conversation so far, compacted]\nUpdated task and files")


class MemoryTests(unittest.TestCase):
    """Check fresh context, durable append semantics, and workspace boundaries."""

    def setUp(self):
        """Create a workspace that is discarded after each independent case."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_prompt_without_memory_and_with_extra(self):
        """Keep stable base rules first, then platform/workdir, then optional extra."""
        with patch.object(memory.platform, "system", return_value="TestOS"):
            prompt = memory.build_system_prompt(self.root, "extra guidance")
        self.assertEqual(prompt, memory.BASE_PROMPT +
                         f"\n\nPlatform: TestOS. Working directory: {self.root.resolve()}\n\nextra guidance")
        self.assertNotIn("Project memory (", prompt)
        self.assertEqual(memory.MEMORY_FILE, "CHIIKAWA.md")

    def test_append_prompt_reload_and_fresh_process(self):
        """Facts persist on disk and a completely new Python process loads them."""
        self.assertEqual(memory.remember(self.root, "Project codename is Maple."), "Remembered in CHIIKAWA.md")
        memory.remember(self.root, "Use UTF-8: 猫.")
        self.assertEqual((self.root / memory.MEMORY_FILE).read_text(),
                         "- Project codename is Maple.\n- Use UTF-8: 猫.\n")
        result = subprocess.run([sys.executable, "-c",
            "import sys; from chiikawa.memory import build_system_prompt; print(build_system_prompt(sys.argv[1]))",
            str(self.root)], capture_output=True, text=True, check=True)
        self.assertIn("Project memory (CHIIKAWA.md):\n- Project codename is Maple.", result.stdout)
        (self.root / memory.MEMORY_FILE).write_text("- Updated fact\n")
        self.assertIn("Updated fact", memory.build_system_prompt(self.root))

    def test_concurrent_appends_keep_every_complete_note(self):
        """Independent writers must not overwrite or interleave complete memory records."""
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: memory.remember(self.root, f"note-{n}"), range(40)))
        self.assertCountEqual((self.root / memory.MEMORY_FILE).read_text().splitlines(),
                              [f"- note-{n}" for n in range(40)])

    def test_fsync_failures_are_not_reported_as_success(self):
        """A storage failure propagates instead of claiming a fact was remembered."""
        with patch.object(memory.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaisesRegex(OSError, "disk failure"):
                memory.remember(self.root, "fact")

    def test_memory_symlink_escape_is_rejected(self):
        """Reject both reads and appends through a memory link outside the workspace."""
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "private"
            target.write_text("unchanged")
            (self.root / memory.MEMORY_FILE).symlink_to(target)
            with self.assertRaises(PermissionError):
                memory.build_system_prompt(self.root)
            with self.assertRaises(PermissionError):
                memory.remember(self.root, "new")
            self.assertEqual(target.read_text(), "unchanged")


class SkillTests(unittest.TestCase):
    """Verify metadata-only discovery and exact-name, contained skill loading."""

    def setUp(self):
        """Create an empty workspace for catalog and reload tests."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_skill(self, name, text):
        """Install a skill fixture as data under its local catalog directory."""
        path = self.root / skills.SKILLS_DIR / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_empty_catalog_and_missing_skill(self):
        """Return no prompt for an empty catalog and an actionable miss result."""
        self.assertEqual(skills.SKILLS_DIR, "skills")
        self.assertEqual(skills.catalog(self.root), {})
        self.assertEqual(skills.catalog_prompt(self.root), "")
        self.assertEqual(skills.read_skill(self.root, "missing"),
                         "ERROR: no skill named missing. Available: (none)")

    def test_metadata_is_sorted_and_body_is_lazy(self):
        """Advertise descriptions only; return the full body through read_skill."""
        text = '---\nname: ignored-name\ndescription: "Customer-facing writing"\n---\nSpeak pirate.\n'
        path = self.write_skill("brand-voice", text)
        self.write_skill("alpha", "---\ndescription: First skill\n---\nBODY SECRET\n")
        catalog = skills.catalog(self.root)
        self.assertEqual(list(catalog), ["alpha", "brand-voice"])
        self.assertEqual(catalog["brand-voice"], {"description": "Customer-facing writing", "path": str(path.resolve())})
        self.assertEqual(skills.catalog_prompt(self.root),
            "Skills available (load one with the use_skill tool when relevant):\n"
            "- alpha: First skill\n- brand-voice: Customer-facing writing")
        self.assertNotIn("BODY SECRET", skills.catalog_prompt(self.root))
        self.assertEqual(skills.read_skill(self.root, "brand-voice"), text)

    def test_front_matter_only_and_folded_description(self):
        """Ignore body descriptions and tolerate missing or simple multiline metadata."""
        for text in ("description: body only", "---\nname: a\n---\ndescription: body only",
                     "---\ndescription: unclosed\nbody"):
            self.write_skill("plain", text)
            self.assertEqual(skills.catalog(self.root)["plain"]["description"], "")
        self.write_skill("plain", "---\ndescription: >-\n  Write friendly\n  welcome messages\n---\nbody")
        self.assertEqual(skills.catalog(self.root)["plain"]["description"], "Write friendly welcome messages")

    def test_reload_and_exact_name_lookup(self):
        """File-only edits take effect immediately; names cannot traverse directories."""
        path = self.write_skill("brand-voice", "---\ndescription: Writing\n---\nPlain style")
        self.assertIn("Plain style", skills.read_skill(self.root, "brand-voice"))
        path.write_text("---\ndescription: Writing\n---\nPirate style")
        self.assertIn("Pirate style", skills.read_skill(self.root, "brand-voice"))
        self.assertEqual(skills.read_skill(self.root, "../brand-voice"),
                         "ERROR: no skill named ../brand-voice. Available: brand-voice")

    def test_external_skill_symlink_is_not_loaded(self):
        """Do not discover linked skill files outside the workspace."""
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "SKILL.md"
            target.write_text("---\ndescription: Outside\n---\nsecret")
            local = self.root / skills.SKILLS_DIR / "external"
            local.mkdir(parents=True)
            (local / "SKILL.md").symlink_to(target)
            self.assertEqual(skills.catalog(self.root), {})


class WiringTests(unittest.TestCase):
    """Exercise the Day 3 sockets using the existing run_loop implementation."""

    def test_skill_is_loaded_through_a_tool_result(self):
        """The model sees catalog metadata first and receives the body only after a call."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skills/brand-voice/SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text("---\ndescription: Writing\n---\nArrr, matey!")
            snapshots = []

            def complete(model, system, messages, tools):
                """Record independent request snapshots and simulate a skill lookup."""
                snapshots.append((system, copy.deepcopy(messages)))
                return ({"text": "", "tool_calls": [{"name": "use_skill", "args": {"name": "brand-voice"},
                                                      "call_id": "skill"}]} if len(snapshots) == 1 else
                        {"text": "Arrr, matey!", "tool_calls": []})

            with patch.object(provider, "complete", side_effect=complete):
                answer, messages = run_task("model", directory, "Write a welcome", on_event=Mock())
            self.assertEqual(answer, "Arrr, matey!")
            self.assertNotIn("Arrr, matey!", snapshots[0][0])
            self.assertIn("Skills available", snapshots[0][0])
            self.assertIn("Arrr, matey!", snapshots[1][1][-1]["text"])
            self.assertEqual(messages[2]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
