"""Day 5: a small CLI with explicit policy defaults and resumable interruption.

Keep argument parsing outside Harness, print bounded tool activity, and refuse
approval on empty input or EOF. Noninteractive failures return nonzero status.
"""

import argparse
import json
import os
import sys

from .harness import Harness
from .security import Approval, Policy
from ._version import __version__
from .terminal import TerminalDisplay, safe_text
from .transcript import TranscriptDisplay
from . import context, providers, session
from .commands import Commands
from .prompt import Prompt
from .runtime import DEFAULT_IMAGE, setup_image


LOGO = r"""           .--------.
          /    >_    \
      .--/____________\--.
     /   \____________/   \
     \  .-'          '-.  /
      '/    _      _    \'
      /    (o)    (o)    \
     |   ///   w    ///   |    C H I I K A W A
     |         '         |
      \                 /     >_ tiny harness
       '._           _.'
      _/  '         '  \_
     (  /             \  )
      '-|             |-'
        \             /
         '._       _.'
           (_)___(_)"""


def print_logo():
    """Render the offline mascot; honor NO_COLOR and plain terminal output.

    Redrawn from the user's image-1.png reference, with a baseball cap added.
    Chiikawa is by Nagano; this is an unofficial terminal-art adaptation.
    """
    logo = LOGO
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        for piece in (".--------.", "/    >_    \\", "/____________\\", "\\____________/"):
            logo = logo.replace(piece, f"\033[94m{piece}\033[0m")
        logo = logo.replace("///", "\033[95m///\033[0m")
        logo = logo.replace("C H I I K A W A", "\033[1;95mC H I I K A W A\033[0m")
        logo = logo.replace(">_ tiny harness", "\033[2m>_ tiny harness\033[0m")
    print(logo, flush=True)


def _call_text(call):
    """Render one bounded line without exposing opaque provider output."""
    arguments = json.dumps(call.get("args", {}), ensure_ascii=False)
    return f"{call['name']} {arguments[:180]}" + ("…" if len(arguments) > 180 else "")


def print_event(kind, payload):
    """Print assistant text, one-line tool calls, and dimmed first-line results."""
    if kind == "assistant" and payload.get("text"):
        print(payload["text"], flush=True)
    elif kind == "tool_start":
        print(f"› {_call_text(payload)}", flush=True)
    elif kind == "tool_end":
        lines = payload.get("text", "").splitlines()
        first = lines[0][:240] if lines else "(empty result)"
        dim, reset = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")
        print(f"  {dim}{first}{reset}", flush=True)


def approve(call, reason, stream=None):
    """Offer one-call or process-lifetime approval; empty input/EOF refuses."""
    arguments = json.dumps(call.get("args", {}), ensure_ascii=False)
    output = stream if stream is not None else sys.stdout
    print(safe_text(f"{reason}\n{call['name']} {arguments}"), file=output, flush=True)
    print("y: allow once · a: allow all actions until exit (including subagents) · N: deny\n"
          "All keeps isolation and IT restrictions. Revoke with /permissions ask.", file=output, flush=True)
    try:
        print(safe_text(f"approve {call['name']}? [y/a/N] "), end="", file=output, flush=True)
        answer = input().strip().lower()
        if answer == "a":
            print("All actions approved until exit; isolation and IT restrictions still apply.", file=output, flush=True)
            return Approval.ALL
        return answer == "y"
    except EOFError:
        return False


def main(argv=None):
    """Run one headless task or an interactive session, returning a process status."""
    parser = argparse.ArgumentParser(description="Chiikawa: a small, resumable coding-agent harness")
    parser.add_argument("--version", action="version", version=f"chiikawa {__version__}")
    parser.add_argument("-p", "--prompt", help="Run one headless task")
    parser.add_argument("-d", "--workdir", default=".", help="Working directory")
    parser.add_argument("--provider", help="Standard: foundry/openrouter; enterprise: IT-approved provider name")
    parser.add_argument("-m", "--model", help="Foundry deployment name or qualified OpenRouter model ID")
    parser.add_argument("--max-output-tokens", type=int, metavar="TOKENS",
                        help="Per-response output cap (Foundry: 65536; OpenRouter: 16384)")
    parser.add_argument("--mode", choices=("ask", "safe", "yolo", "read-only"),
                        help="ask: approve every tool; safe: approve non-read tools; yolo: approve all; read-only: reads only")
    parser.add_argument("--profile", choices=("standard", "enterprise"),
                        help="Enterprise is required when company IT policy is installed")
    parser.add_argument("--isolation", choices=("jail", "sandbox"),
                        help="Default: jail; enterprise uses an enforced native OS Jail")
    parser.add_argument("--sandbox-network", choices=("deny", "allow"), default="deny")
    parser.add_argument("--sandbox-image", default=DEFAULT_IMAGE, help="Trusted, locally available sandbox image")
    parser.add_argument("--sandbox-setup", action="store_true", help="Explicitly build the bundled sandbox image and exit")
    parser.add_argument("--resume", action="store_true", help="Resume this directory's latest session")
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--display", choices=("compact", "verbose"),
                        help="Activity layout (default: compact in interactive terminals, verbose otherwise)")
    parser.add_argument("--context-threshold", "--budget-tokens", type=int,
                        default=context.DEFAULT_BUDGET_TOKENS, metavar="TOKENS",
                        help="Compact above this estimated history size (default: 600000 tokens)")
    parser.add_argument("--no-reasoning", action="store_true",
                        help="Disable public reasoning summaries (for models that do not support them)")
    args = parser.parse_args(argv)
    from .enterprise_policy import managed_policy_present
    managed = managed_policy_present()
    if managed and args.profile == "standard":
        parser.error("Company IT manages this machine; --profile standard is disabled.")
    args.profile = args.profile or ("enterprise" if managed else "standard")
    if args.max_turns < 0:
        parser.error("--max-turns must be nonnegative")
    if args.context_threshold < 0:
        parser.error("--context-threshold must be nonnegative")
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be positive")
    mode = args.mode or ("yolo" if args.prompt is not None else "safe")
    interactive_terminal = (sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()
                            and os.environ.get("TERM") != "dumb")
    compact = args.display == "compact" or (args.display is None and args.prompt is None and interactive_terminal)
    display = TranscriptDisplay() if compact else TerminalDisplay()
    try:
        if args.sandbox_setup:
            if args.profile == "enterprise":
                from .enterprise_admin import require_admin
                require_admin()
            setup_image(args.sandbox_image)
            print(f"Sandbox image ready: {args.sandbox_image}", flush=True)
            return 0
        enterprise = None
        if args.profile == "enterprise":
            from .enterprise_client import EnterpriseClient
            enterprise = EnterpriseClient()
            enterprise.login(lambda url, code: display.line(f"Sign in with Microsoft Entra ID: {url} · code: {code}"))
            backend = enterprise.backend(args.provider)
        else:
            backend = providers.select(args.provider)
        def approver(call, reason):
            with display.approval():
                return approve(call, reason, stream=sys.stderr)

        policy = Policy(mode, approver)

        def make_harness(provider_name, model, **isolation_options):
            options = dict(profile=args.profile, isolation=args.isolation, sandbox_network=args.sandbox_network, sandbox_image=args.sandbox_image)
            options.update(isolation_options)
            if enterprise is not None:
                options["enterprise_session"] = enterprise
            return Harness(args.workdir, model=model, policy=policy,
                           on_event=display, max_turns=args.max_turns, activity=True,
                           budget_tokens=args.context_threshold,
                           reasoning_summary=not args.no_reasoning,
                           provider=provider_name, max_output_tokens=args.max_output_tokens,
                           **options)

        harness = make_harness(backend.NAME, args.model)
        if args.resume and not harness.resume():
            parser.error("No nonempty session found in the selected working directory.")
        if args.prompt is not None:
            with display.task(args.prompt):
                harness.run(args.prompt)
            return 0
        print_logo()
        print(f"Chiikawa · provider: {backend.NAME} · model: {harness.model} · mode: {mode} · isolation: {harness.isolation.title()} · {'jail directory' if harness.isolation == 'jail' else 'workspace'}: {harness.workdir}", flush=True)
        display("context", context.context_status(harness.messages, args.context_threshold))
        output_limit = backend.MAX_OUTPUT_TOKENS if args.max_output_tokens is None else args.max_output_tokens
        display.line(f"Output limit: {output_limit:,} tokens per response", "2")
        print("Type / for commands · /model · /provider · /status · ctrl+t history", flush=True)

        def history():
            if compact:
                display.show_history()
            else:
                viewer = TranscriptDisplay()
                agent = commands.harness
                viewer.restore(session._read(agent.session_path) if agent.session_path else agent.messages)
                viewer.show_history()

        commands = Commands(harness, make_harness, on_history=history,
                            extra_status=display.status if compact else None)
        prompt = Prompt(commands.candidates, on_history=history)
        if args.resume and compact:
            display.restore(harness.messages)
            display.resume_preview()
        while True:
            try:
                task = prompt.read()
            except EOFError:
                print()
                return 0
            if task.strip():
                previous = commands.harness
                if commands.handle(task):
                    if commands.exiting:
                        return 0
                    if compact and commands.harness is not previous:
                        display.restore([])
                else:
                    with display.task(task):
                        commands.harness.run(task)
    except KeyboardInterrupt:
        # Durable callbacks precede execution; exit rather than reusing an unfinished in-memory turn.
        print("\nInterrupted. The session log is safe; --resume continues it.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        display.close()
