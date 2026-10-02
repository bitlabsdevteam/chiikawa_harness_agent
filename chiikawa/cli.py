"""Day 5: a small CLI with explicit policy defaults and resumable interruption.

Keep argument parsing outside Harness, print bounded tool activity, and refuse
approval on empty input or EOF. Noninteractive failures return nonzero status.
"""

import argparse
import json
import sys

from .harness import Harness
from .security import Policy


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


def approve(call, reason):
    """Show the requested operation and require an explicit yes from the user."""
    print(f"{reason}\n{_call_text(call)}", flush=True)
    try:
        return input(f"approve {call['name']}? [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def main(argv=None):
    """Run one headless task or an interactive session, returning a process status."""
    parser = argparse.ArgumentParser(description="Chiikawa: a small, resumable coding-agent harness")
    parser.add_argument("-p", "--prompt", help="Run one headless task")
    parser.add_argument("-d", "--workdir", default=".", help="Working directory")
    parser.add_argument("-m", "--model", help="Foundry deployment name")
    parser.add_argument("--mode", choices=("safe", "yolo", "read-only"))
    parser.add_argument("--resume", action="store_true", help="Resume this directory's latest session")
    parser.add_argument("--max-turns", type=int, default=120)
    args = parser.parse_args(argv)
    if args.max_turns < 0:
        parser.error("--max-turns must be nonnegative")
    mode = args.mode or ("yolo" if args.prompt is not None else "safe")
    try:
        harness = Harness(args.workdir, model=args.model, policy=Policy(mode, approve),
                          on_event=print_event, max_turns=args.max_turns)
        if args.resume and not harness.resume():
            parser.error("No nonempty session found in the selected working directory.")
        if args.prompt is not None:
            harness.run(args.prompt)
            return 0
        print(f"Chiikawa · model: {harness.model} · mode: {mode} · jail directory: {harness.workdir}", flush=True)
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
