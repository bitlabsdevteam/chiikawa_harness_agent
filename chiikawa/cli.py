"""Day 5: a small CLI with explicit policy defaults and resumable interruption.

Keep argument parsing outside Harness, print bounded tool activity, and refuse
approval on empty input or EOF. Noninteractive failures return nonzero status.
"""

import argparse
import json
import os
import sys

from .harness import Harness
from .security import Policy
from ._version import __version__
from .terminal import PROGRESS_PROMPT, TerminalDisplay, safe_text
from . import context, providers


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
    """Show the requested operation and require an explicit yes from the user."""
    arguments = json.dumps(call.get("args", {}), ensure_ascii=False)
    output = stream if stream is not None else sys.stdout
    print(safe_text(f"{reason}\n{call['name']} {arguments}"), file=output, flush=True)
    try:
        print(safe_text(f"approve {call['name']}? [y/N] "), end="", file=output, flush=True)
        return input().strip().lower() == "y"
    except EOFError:
        return False


def main(argv=None):
    """Run one headless task or an interactive session, returning a process status."""
    parser = argparse.ArgumentParser(description="Chiikawa: a small, resumable coding-agent harness")
    parser.add_argument("--version", action="version", version=f"chiikawa {__version__}")
    parser.add_argument("-p", "--prompt", help="Run one headless task")
    parser.add_argument("-d", "--workdir", default=".", help="Working directory")
    parser.add_argument("--provider", choices=("foundry", "openrouter"),
                        help="Model provider (default: CHIIKAWA_PROVIDER or foundry)")
    parser.add_argument("-m", "--model", help="Foundry deployment name or qualified OpenRouter model ID")
    parser.add_argument("--max-output-tokens", type=int, metavar="TOKENS",
                        help="Per-response output cap (Foundry: 65536; OpenRouter: 16384)")
    parser.add_argument("--mode", choices=("safe", "yolo", "read-only"))
    parser.add_argument("--resume", action="store_true", help="Resume this directory's latest session")
    parser.add_argument("--max-turns", type=int, default=120)
    parser.add_argument("--context-threshold", "--budget-tokens", type=int,
                        default=context.DEFAULT_BUDGET_TOKENS, metavar="TOKENS",
                        help="Compact above this estimated history size (default: 600000 tokens)")
    parser.add_argument("--no-reasoning", action="store_true",
                        help="Disable public reasoning summaries (for models that do not support them)")
    args = parser.parse_args(argv)
    if args.max_turns < 0:
        parser.error("--max-turns must be nonnegative")
    if args.context_threshold < 0:
        parser.error("--context-threshold must be nonnegative")
    if args.max_output_tokens is not None and args.max_output_tokens <= 0:
        parser.error("--max-output-tokens must be positive")
    mode = args.mode or ("yolo" if args.prompt is not None else "safe")
    display = TerminalDisplay()
    try:
        backend = providers.select(args.provider)
        approver = lambda call, reason: approve(call, reason, stream=sys.stderr)
        harness = Harness(args.workdir, model=args.model, policy=Policy(mode, approver),
                          on_event=display, max_turns=args.max_turns, activity=True,
                          budget_tokens=args.context_threshold,
                          reasoning_summary=not args.no_reasoning, system_extra=PROGRESS_PROMPT,
                          provider=backend.NAME, max_output_tokens=args.max_output_tokens)
        if args.resume and not harness.resume():
            parser.error("No nonempty session found in the selected working directory.")
        if args.prompt is not None:
            harness.run(args.prompt)
            return 0
        print_logo()
        print(f"Chiikawa · provider: {backend.NAME} · model: {harness.model} · mode: {mode} · jail directory: {harness.workdir}", flush=True)
        display("context", context.context_status(harness.messages, args.context_threshold))
        output_limit = backend.MAX_OUTPUT_TOKENS if args.max_output_tokens is None else args.max_output_tokens
        display.line(f"Output limit: {output_limit:,} tokens per response", "2")
        while True:
            try:
                task = input("chiikawa> ")
            except EOFError:
                print()
                return 0
            if task.strip():
                harness.run(task)
    except KeyboardInterrupt:
        # Durable callbacks precede execution; exit rather than reusing an unfinished in-memory turn.
        print("\nInterrupted. The session log is safe; --resume continues it.", file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        display.close()
