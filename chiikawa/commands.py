"""Local slash commands and completion data for the interactive CLI."""

import os

from . import context, providers
from .terminal import safe_text

COMMANDS = (
    ("/model", "Show or change the model", "[model-id]"),
    ("/provider", "Show or switch the provider", "[foundry|openrouter]"),
    ("/status", "Show session, model, and context usage", ""),
    ("/new", "Start a fresh conversation", ""),
    ("/help", "Show all commands", ""),
    ("/exit", "Exit Chiikawa", ""),
    ("/sandbox", "Switch to Docker Sandbox isolation", ""),
    ("/jail", "Switch to host execution with file-tool Jail", ""),
)


class Commands:
    """Handle commands without sending them to a model or changing shell settings."""

    def __init__(self, harness, make_harness):
        self.harness = harness
        self.make_harness = make_harness
        self.exiting = False

    def models(self):
        backend = self.harness.backend
        values = [self.harness.model, os.environ.get(backend.MODEL_ENV), backend.DEFAULT_MODEL]
        return list(dict.fromkeys(value for value in values if value))

    def candidates(self, text):
        """Return insertion text and labels; arbitrary model IDs remain accepted."""
        if not text.startswith("/"):
            return []
        command, space, argument = text.partition(" ")
        if space:
            command = command.rstrip("/")
            if command == "/provider":
                return [(f"/provider {name}", f"{name}{' (current)' if name == self.harness.provider_name else ''}")
                        for name in ("foundry", "openrouter") if name.startswith(argument)]
            if command == "/model":
                return [(f"/model {name}", f"{name}{' (current)' if name == self.harness.model else ''}")
                        for name in self.models() if name.startswith(argument)]
            return []
        prefix = command.rstrip("/") if command != "/" else "/"
        return [(name, f"{name:<11} {description}") for name, description, _ in COMMANDS if name.startswith(prefix)]

    def say(self, text):
        print(safe_text(text), flush=True)

    def handle(self, text):
        """Return true for every slash command, including unknown/invalid commands."""
        text = text.strip()
        if not text.startswith("/"):
            return False
        parts = text.split(maxsplit=1)
        name = parts[0].rstrip("/") or "/help"
        argument = parts[1].strip() if len(parts) == 2 else ""
        names = {row[0] for row in COMMANDS}
        if name not in names:
            self.say(f"Unknown command: {name}. Type / for all commands.")
            return True
        if argument and name not in ("/model", "/provider"):
            self.say(f"Usage: {name}")
            return True
        agent = self.harness
        if name == "/help":
            self.say("Commands (↑/↓ select · Tab complete · Enter choose · Esc dismiss):")
            for command, description, usage in COMMANDS:
                self.say(f"  {command + (' ' + usage if usage else ''):<34} {description}")
        elif name == "/status":
            status = context.context_status(agent.messages, agent.budget_tokens)
            self.say(f"Provider: {agent.provider_name}\nModel: {agent.model}\nMode: {agent.policy.mode}\n"
                     f"Profile: {agent.profile}\nCore policy SHA-256: {agent.policy_fingerprint}\n"
                     f"Isolation: {agent.isolation.title()}\nNetwork: {agent.sandbox_network if agent.isolation == 'sandbox' else 'host'}\n"
                     f"Workspace: {agent.workdir}\nSession: {agent.session_path or '(new; no messages yet)'}\n"
                     f"Context history (est.): ~{status['estimated_tokens']:,.0f} / {agent.budget_tokens:,} tokens\n"
                     f"Output limit: {agent.max_output_tokens:,} tokens per response\n"
                     f"Turn limit: {agent.max_turns}")
        elif name in ("/sandbox", "/jail"):
            try:
                changed = agent.set_isolation(name[1:])
            except (OSError, ValueError, RuntimeError) as exc:
                self.say(f"Isolation switch failed; still in {agent.isolation.title()}: {exc}")
            else:
                self.say(("Switched to " if changed else "Already in ") + agent.isolation_description())
        elif name == "/exit":
            self.exiting = True
        elif name in ("/model", "/provider") and not argument:
            if name == "/model":
                self.say(f"Current model: {agent.model}\nConfigured models: {', '.join(self.models())}\n"
                         "Use /model <model-id> to select any model available to your provider.\n"
                         "Foundry uses deployment names; OpenRouter uses provider/model IDs.")
            else:
                self.say(f"Current provider: {agent.provider_name}\nAvailable: foundry, openrouter\n"
                         "Use /provider <name> to switch providers.")
        else:
            selected_provider, selected_model = agent.provider_name, agent.model
            if name == "/provider":
                if argument not in ("foundry", "openrouter"):
                    self.say("Usage: /provider foundry|openrouter")
                    return True
                selected_provider = argument
                if selected_provider != agent.provider_name:
                    backend = providers.select(selected_provider)
                    selected_model = os.environ.get(backend.MODEL_ENV) or backend.DEFAULT_MODEL
            elif name == "/model":
                if any(char.isspace() for char in argument) or (selected_provider == "openrouter" and "/" not in argument):
                    self.say("Use a single model ID (OpenRouter example: openai/gpt-5.4).")
                    return True
                selected_model = argument
            if name != "/new" and (selected_provider, selected_model) == (agent.provider_name, agent.model):
                self.say(f"Already using {selected_provider} · {selected_model}.")
                return True
            # Construct first: a configuration failure must leave the active session intact.
            try:
                replacement = self.make_harness(selected_provider, selected_model,
                                                profile=agent.profile, isolation=agent.isolation,
                                                sandbox_network=agent.sandbox_network,
                                                sandbox_image=agent.sandbox_image)
                replacement.policy = agent.policy
            except (OSError, ValueError, RuntimeError) as exc:
                self.say(f"Could not start a new conversation: {exc}")
                return True
            self.harness = replacement
            self.say(f"New conversation · provider: {replacement.provider_name} · model: {replacement.model}")
            if agent.session_path:
                self.say(f"Previous session saved: {agent.session_path}")
        return True
