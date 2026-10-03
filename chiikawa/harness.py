"""Day 4: compose tools, policy, memory, compaction, sessions, and bounded children.

Persist messages before exposing events or executing their tools. Keep the full
journal separate from the compacted working view; children never create logs.
"""

import os
from pathlib import Path

from . import context, loop, memory, provider, session, skills
from .security import Policy
from .subagent import subagent_tool
from .tools import core_tools, tool


class Harness:
    """A single active conversation over one workspace, with explicit dependencies."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=600_000, max_turns=120,
                 session_path=None, enable_subagents=True, persist=True, _depth=0,
                 activity=False, reasoning_summary=True):
        """Create a workspace and compose the existing modules without rewriting them."""
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.model = model or os.environ.get("CHIIKAWA_MODEL") or provider.DEFAULT_MODEL
        self.policy = policy if policy is not None else Policy("yolo")
        self.on_event = on_event if on_event is not None else lambda kind, payload: None
        self.budget_tokens, self.max_turns = budget_tokens, max_turns
        self.session_path = Path(session_path) if session_path is not None else None
        self.persist, self._depth = persist, _depth
        self.activity, self.reasoning_summary = activity, reasoning_summary
        self.messages, self._recorded = [], 0
        extras = list(extra_tools.values()) if isinstance(extra_tools, dict) else list(extra_tools or [])
        self.tools = {item.name: item for item in core_tools(self.workdir)}

        @tool("Remember a trusted, non-secret project fact", note="Fact to append to project memory")
        def remember(note):
            """Persist a project fact through the existing memory module."""
            return memory.remember(self.workdir, note)

        @tool("Load a project's skill instructions", name="Skill name from the catalog")
        def use_skill(name):
            """Return a selected skill body as tool output, never as elevated instructions."""
            return skills.read_skill(self.workdir, name)

        def make_child(depth):
            """Inherit configuration and policy, but give each child empty ephemeral history."""
            return Harness(self.workdir, model=self.model, policy=self.policy, extra_tools=extras,
                           system_extra=system_extra, on_event=self.on_event,
                           budget_tokens=self.budget_tokens, max_turns=self.max_turns,
                           enable_subagents=enable_subagents, persist=False, _depth=depth,
                           activity=activity, reasoning_summary=reasoning_summary)

        self.tools[remember.name] = remember
        catalog = skills.catalog_prompt(self.workdir)
        if catalog:
            self.tools[use_skill.name] = use_skill
        if enable_subagents:
            self.tools["spawn_agent"] = subagent_tool(make_child, depth=_depth)
        self.tools.update({item.name: item for item in extras})
        self.system = memory.build_system_prompt(self.workdir, "\n\n".join(
            section for section in (catalog, system_extra) if section))

    def _flush(self):
        """Record each newly appended message once; do not mark failed writes as recorded."""
        self._recorded = min(self._recorded, len(self.messages))
        while self._recorded < len(self.messages):
            if self.persist and self.session_path is not None:
                session.append(self.session_path, self.messages[self._recorded])
            self._recorded += 1

    def resume(self, path=None):
        """Restore a session and persist only synthesized repairs, never the whole history."""
        selected = Path(path) if path is not None else session.latest(self.workdir)
        if selected is None:
            return False
        loaded = session.load(selected)
        recorded = len(session._read(selected))
        self.messages, self.session_path = loaded, selected
        self._recorded = recorded
        self._flush()
        return bool(loaded)

    def run(self, task) -> str:
        """Append a task and run the loop with durable events and transient compaction.

        Only one run may be active on a Harness/session at a time. Compaction
        changes the model view; the journal keeps the original complete history.
        """
        if self.persist and self.session_path is None:
            self.session_path = session.new_session(self.workdir, task[:32])
        self.messages.append({"role": "user", "text": task})
        self._flush()

        def on_event(kind, payload):
            """Sync history before an observer can fail, stop the process, or inspect it."""
            self._flush()
            self.on_event(kind, payload)

        def before_turn(messages):
            """Flush pending input, then move the cursor to the new working-view boundary."""
            self._flush()
            replacement = context.compact(self.model, messages, self.budget_tokens)
            # A summary is a view of already-journaled events, not another event.
            self._recorded = len(replacement)
            return replacement

        return loop.run_loop(self.model, self.system, self.messages, self.tools, on_event,
                             self.policy.check, max_turns=self.max_turns, before_turn=before_turn,
                             activity=self.activity, reasoning_summary=self.reasoning_summary)
