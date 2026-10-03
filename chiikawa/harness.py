"""Day 4: compose tools, policy, memory, compaction, sessions, and bounded children.

Persist messages before exposing events or executing their tools. Keep the full
journal separate from the compacted working view; children never create logs.
"""

import os
import platform
import threading
from pathlib import Path

from . import context, loop, providers, session, system_policy
from .security import Policy
from .subagent import subagent_tool
from .tools import tool
from .runtime import DEFAULT_IMAGE, JailRuntime, SandboxRuntime, validate_session


class Harness:
    """A single active conversation over one workspace, with explicit dependencies."""

    def __init__(self, workdir=".", model=None, policy=None, extra_tools=None,
                 system_extra="", on_event=None, budget_tokens=context.DEFAULT_BUDGET_TOKENS, max_turns=120,
                 session_path=None, enable_subagents=True, persist=True, _depth=0,
                 activity=False, reasoning_summary=True, provider=None, max_output_tokens=None,
                 isolation=None, sandbox_network="deny", sandbox_image=DEFAULT_IMAGE, profile=None,
                 enterprise_session=None):
        """Create a workspace and compose the existing modules without rewriting them."""
        if system_extra:
            raise ValueError("system_extra overrides are not supported; use agents.md or ordinary task context.")
        from .enterprise_policy import managed_policy_present
        managed = managed_policy_present()
        profile = profile or ("enterprise" if managed else "standard")
        if managed and profile != "enterprise":
            raise PermissionError("Company IT manages this machine; the standard profile is disabled.")
        if profile not in {"standard", "enterprise"}:
            raise ValueError("profile must be standard or enterprise")
        self._profile = profile
        self._core_policy = system_policy.load_policy()
        isolation = isolation if isolation is not None else "jail"
        self._enterprise = enterprise_session
        if profile == "enterprise":
            if enterprise_session is None:
                raise PermissionError("Enterprise requires an authenticated IT management session; sign in through the CLI or EnterpriseClient.")
            enterprise_session.refresh()
            self.backend = enterprise_session.backend(provider)
        else:
            if enterprise_session is not None:
                raise ValueError("A managed session requires the enterprise profile.")
            self.backend = providers.select(provider)
        self.provider_name = self.backend.NAME
        configured_model = os.environ.get(self.backend.MODEL_ENV) if profile == "standard" else None
        self._model_explicit = bool(model or configured_model)
        self.model = model or configured_model or self.backend.DEFAULT_MODEL
        if profile == "enterprise" and self.model not in self.backend.models:
            raise PermissionError("Model is not approved by company IT.")
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
        self._run_lock = threading.Lock()
        self._extras = list(extra_tools.values()) if isinstance(extra_tools, dict) else list(extra_tools or [])
        self._enable_subagents = enable_subagents
        self.sandbox_network, self.sandbox_image = sandbox_network, sandbox_image
        if profile == "enterprise" and sandbox_image == DEFAULT_IMAGE:
            self.sandbox_image = self._enterprise.configuration.get("sandbox_image") or DEFAULT_IMAGE
        if isolation not in {"jail", "sandbox"}:
            raise ValueError("isolation must be jail or sandbox")
        if sandbox_network not in {"deny", "allow"}:
            raise ValueError("sandbox_network must be deny or allow")
        runtime, tools, environment = self._prepare_isolation(isolation)
        self._isolation, self._runtime, self.tools, self._environment = isolation, runtime, tools, environment

    @property
    def profile(self):
        return self._profile

    @property
    def isolation(self):
        return self._isolation

    @property
    def policy_fingerprint(self):
        return self._core_policy.fingerprint

    @property
    def system(self):
        """Read-only view, composed from installed policy and host-owned facts only."""
        return system_policy.compose_system(self._core_policy, {
            "profile": self.profile,
            "platform": "Linux" if self.isolation == "sandbox" else platform.system(),
            "workspace": "/workspace" if self.isolation == "sandbox" else str(self.workdir),
            "isolation": self.isolation,
            "network": self._runtime.network if self.isolation == "sandbox" or self.profile == "enterprise" else "host",
            "approval_policy": self.policy.mode,
            "tools": sorted(self.tools),
            "delegation": "synchronous" if "spawn_agent" in self.tools and self._depth < 2 else "unavailable",
            "child_depth": self._depth,
            "policy_fingerprint": self.policy_fingerprint,
        })

    def _validate_profile(self, isolation):
        if self.profile == "enterprise":
            if self._extras:
                raise ValueError("Enterprise profile does not permit host extra_tools.")
            if self.sandbox_network != "deny":
                raise ValueError("Enterprise shell networking stays offline; IT-approved URLs use the managed gateway.")
            if isolation == "sandbox" and self.sandbox_image != self._enterprise.configuration.get("sandbox_image"):
                raise PermissionError("IT must approve the pinned local Sandbox image before activation.")
            system_policy.validate_installation(self.workdir)
            from .enterprise_policy import trusted_path
            installed = system_policy.installation_path()
            trusted_path(installed, directory=installed.is_dir())
            if installed.is_dir():
                for resource in (*installed.glob("*.py"), installed / "SYSTEM_PROMPT.md"):
                    trusted_path(resource)
            validate_session(self.workdir, self.session_path)

    def _check_tool(self, call):
        # Enforce invariants outside the model, including after an approval callback.
        self._validate_execution()
        reason = self.policy.check(call)
        self._validate_execution()
        return reason

    def _validate_execution(self):
        if self.profile == "enterprise":
            self._enterprise.refresh()
        self._validate_profile(self.isolation)
        if self.profile == "enterprise":
            from .enterprise_jail import EnterpriseJail
            expected = SandboxRuntime if self.isolation == "sandbox" else EnterpriseJail
            if not isinstance(self._runtime, expected) or self._runtime.root != self.workdir:
                raise RuntimeError("Enterprise execution requires the validated managed runtime.")
            self.backend.facts = {"isolation": self.isolation, "workspace": str(self.workdir),
                                  "approval": self.policy.mode, "depth": self._depth}

    def isolation_description(self, isolation=None):
        selected = isolation or self.isolation
        if selected == "sandbox":
            return (f"Sandbox: Linux container; workspace /workspace; network {self.sandbox_network}. "
                    "Each tool runs in a fresh container; /tmp and background processes do not persist. "
                    "Project edits persist. Protected files are hidden; .git is read-only.")
        if self.profile == "enterprise":
            return (f"Jail: native OS boundary at {self.workdir}; shell networking offline; "
                    "IT-approved URLs use the managed gateway. Protected files are restricted.")
        return (f"Jail: file tools restricted to {self.workdir}; shell commands run on the host "
                "with host permissions and host network access.")

    def _prepare_isolation(self, isolation):
        self._validate_profile(isolation)
        if isolation == "sandbox":
            if self._extras:
                raise ValueError("Sandbox v1 does not permit host extra_tools.")
            validate_session(self.workdir, self.session_path)
            runtime = SandboxRuntime(self.workdir, self.sandbox_image, self.sandbox_network)
        elif self.profile == "enterprise":
            from .enterprise_jail import EnterpriseJail
            runtime = EnterpriseJail(self.workdir, os.getuid(), os.getgid())
        else:
            runtime = JailRuntime(self.workdir)
        environment = runtime.environment()
        tools = {item.name: item for item in runtime.tools()}

        @tool("Remember a trusted, non-secret project fact", note="Fact to append to project memory")
        def remember(note):
            return runtime.call("remember", {"note": note})

        @tool("Load a project's skill instructions", name="Skill name from the catalog")
        def use_skill(name):
            return runtime.call("use_skill", {"name": name})

        def make_child(depth):
            return Harness(self.workdir, model=self.model, policy=self.policy, extra_tools=self._extras,
                           profile=self.profile, on_event=self.on_event,
                           budget_tokens=self.budget_tokens, max_turns=self.max_turns,
                           enable_subagents=self._enable_subagents, persist=False, _depth=depth,
                           activity=self.activity, reasoning_summary=self.reasoning_summary,
                           provider=self.provider_name, max_output_tokens=self._max_output_override,
                           isolation=self.isolation, sandbox_network=self.sandbox_network,
                           sandbox_image=self.sandbox_image, enterprise_session=self._enterprise)

        tools[remember.name] = remember
        if environment["catalog"]:
            tools[use_skill.name] = use_skill
        if self._enable_subagents:
            tools["spawn_agent"] = subagent_tool(make_child, depth=self._depth)
        if self.profile == "enterprise":
            @tool("Read an HTTPS URL approved by company IT", url="Approved HTTPS URL")
            def fetch_url(url):
                return self._enterprise.request("fetch", {"url": url})
            tools[fetch_url.name] = fetch_url
        tools.update({item.name: item for item in self._extras})
        return runtime, tools, environment

    def set_isolation(self, isolation):
        """Atomically replace execution between runs, retaining conversation and policy."""
        if isolation not in {"jail", "sandbox"}:
            raise ValueError("isolation must be jail or sandbox")
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("Isolation can only change between runs.")
        try:
            if self.profile == "enterprise":
                self._enterprise.refresh()
            self._validate_profile(isolation)
            if isolation == self.isolation:
                return False
            runtime, tools, environment = self._prepare_isolation(isolation)
            notice = {"role": "user", "text": "[Environment changed] " + self.isolation_description(isolation),
                      "isolation": isolation, "environment_change": True, "profile": self.profile}
            if self.provider_name == "openrouter" or self.profile == "enterprise":
                notice.update(provider=self.provider_name, model=self.model)
            # Journal before committing configuration. Failed preparation never changes history.
            self._flush()
            if self.persist:
                path = self.session_path or session.new_session(self.workdir, "environment-change")
                session.append(path, notice)
                self.session_path = path
            self.messages.append(notice)
            self._recorded = len(self.messages)
            self._isolation, self._runtime, self.tools, self._environment = isolation, runtime, tools, environment
            return True
        finally:
            self._run_lock.release()

    def _flush(self):
        """Record each newly appended message once; do not mark failed writes as recorded."""
        if self.isolation == "sandbox" or self.profile == "enterprise":
            validate_session(self.workdir, self.session_path)
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
        if self.isolation == "sandbox" or self.profile == "enterprise":
            validate_session(self.workdir, selected)
        loaded = session.load(selected)
        if self.profile != "enterprise" and any(item.get("profile") == "enterprise" for item in loaded):
            raise RuntimeError("This session requires --profile enterprise; standard-profile resume is not permitted.")
        self.backend.validate_replay(loaded)
        if loaded:
            saved_provider = next((item["provider"] for item in loaded if item.get("provider")), None)
            if saved_provider is None:
                saved_provider = "openrouter" if any("openrouter_message" in item for item in loaded) else "foundry"
            if saved_provider != self.provider_name:
                raise RuntimeError(f"This session uses {saved_provider}; resume with --provider {saved_provider} "
                                   "or start a new session without --resume.")
            if saved_provider == "openrouter" or self.profile == "enterprise":
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
        saved_isolation = next((item["isolation"] for item in reversed(loaded) if item.get("isolation")), None)
        if saved_isolation and saved_isolation != self.isolation:
            self.messages.append({"role": "user", "text": "[Environment changed on resume] " + self.isolation_description(),
                                  "isolation": self.isolation, "environment_change": True, "profile": self.profile})
            self._flush()
        return bool(loaded)

    def run(self, task) -> str:
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("Only one run may be active on a Harness.")
        try:
            self._validate_execution()
            return self._run(task)
        finally:
            self._run_lock.release()

    def _run(self, task) -> str:
        """Append a task and run the loop with durable events and transient compaction.

        Only one run may be active on a Harness/session at a time. Compaction
        changes the model view; the journal keeps the original complete history.
        """
        if self.persist and self.session_path is None:
            self.session_path = session.new_session(self.workdir, task[:32])
        message = {"role": "user", "text": task, "profile": self.profile}
        if self.isolation == "sandbox":
            message["isolation"] = self.isolation
        if self.provider_name == "openrouter" or self.profile == "enterprise":
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
            if self.profile == "enterprise":
                self._validate_execution()
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

        return loop.run_loop(self.model, lambda: self.system, self.messages, self.tools, on_event,
                             self._check_tool, max_turns=self.max_turns, before_turn=before_turn,
                             activity=self.activity, reasoning_summary=self.reasoning_summary,
                             backend=self.backend, max_output_tokens=self._max_output_override,
                             context_messages=system_policy.project_messages(self._environment))
