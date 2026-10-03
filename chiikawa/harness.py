"""Day 4: compose tools, policy, memory, compaction, sessions, and bounded children.

Persist messages before exposing events or executing their tools. Keep the full
journal separate from the compacted working view; children never create logs.
"""

import os
from pathlib import Path

from . import context, loop, memory, providers, session, skills
from .security import Policy
from .subagent import subagent_tool
from .tools import core_tools, tool


class Harness:
    """A single active conversation over one workspace, with explicit dependencies."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=context.DEFAULT_BUDGET_TOKENS, max_turns=120,
                 session_path=None, enable_subagents=True, persist=True, _depth=0,
                 activity=False, reasoning_summary=True, provider=None, max_output_tokens=None):
        """Create a workspace and compose the existing modules without rewriting them."""
        self.backend = providers.select(provider)
        self.provider_name = self.backend.NAME
        configured_model = os.environ.get(self.backend.MODEL_ENV)
        self._model_explicit = bool(model or configured_model)
        self.model = model or configured_model or self.backend.DEFAULT_MODEL
        if max_output_tokens is not None and (not isinstance(max_output_tokens, int) or max_output_tokens <= 0):
            raise ValueError("max_output_tokens must be a positive integer.")
        self._max_output_override = max_output_tokens
        self.max_output_tokens = self.backend.MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
        self.workdir = Path(workdir).resolve()
        self.workdir.mkdir(parents=True, exist_ok=True)
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
                           activity=activity, reasoning_summary=reasoning_summary,
                           provider=self.provider_name, max_output_tokens=self._max_output_override)

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
        if loaded:
            saved_provider = next((item["provider"] for item in loaded if item.get("provider")), None)
            if saved_provider is None:
                saved_provider = "openrouter" if any("openrouter_message" in item for item in loaded) else "foundry"
            if saved_provider != self.provider_name:
                raise RuntimeError(f"This session uses {saved_provider}; resume with --provider {saved_provider} "
                                   "or start a new session without --resume.")
            if saved_provider == "openrouter":
                saved_model = next((item["model"] for item in reversed(loaded) if item.get("model")), None)
                if saved_model:
                    if self._model_explicit and self.model != saved_model:
                        raise RuntimeError(f"This OpenRouter session uses {saved_model}; resume with -m {saved_model} "
                                           "or start a new session to change models.")
                    self.model = saved_model
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
        message = {"role": "user", "text": task}
        if self.provider_name == "openrouter":
            message.update(provider=self.provider_name, model=self.model)
        self.messages.append(message)
        self._flush()

        def on_event(kind, payload):
            """Sync history before an observer can fail, stop the process, or inspect it."""
            self._flush()
            self.on_event(kind, payload)
            if self.activity and kind == "usage" and payload.get("source") == "response":
                self.on_event("context", context.context_status(self.messages, self.budget_tokens))

        def before_turn(messages):
            """Flush pending input, then move the cursor to the new working-view boundary."""
            self._flush()
            options = {"backend": self.backend, "max_output_tokens": self._max_output_override}
            if self.activity:
                status = context.context_status(messages, self.budget_tokens)
                on_event("context", status)
                if status["can_compact"]:
                    on_event("compaction_start", status)
                options["on_usage"] = lambda usage: on_event("usage", {
                    **usage, "source": "compaction", "max_output_tokens": self.max_output_tokens})
            replacement = context.compact(self.model, messages, self.budget_tokens, **options)
            if self.activity and replacement is not messages:
                on_event("compaction", {"tokens_before": status["estimated_tokens"],
                                       "tokens_after": context.estimate_tokens(replacement),
                                       "threshold": self.budget_tokens})
                on_event("context", context.context_status(replacement, self.budget_tokens))
            # A summary is a view of already-journaled events, not another event.
            self._recorded = len(replacement)
            return replacement

        return loop.run_loop(self.model, self.system, self.messages, self.tools, on_event,
                             self.policy.check, max_turns=self.max_turns, before_turn=before_turn,
                             activity=self.activity, reasoning_summary=self.reasoning_summary,
                             backend=self.backend, max_output_tokens=self._max_output_override)
