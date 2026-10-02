"""Day 3: connect compaction, durable memory, and lazy skills through existing hooks.

Keep provider and loop contracts unchanged. Emit visible compaction measurements
without exposing encrypted reasoning, and rebuild project context for each run.
"""

import argparse
import tempfile
from pathlib import Path

from chiikawa import memory, skills
from chiikawa.context import compact, estimate_tokens
from chiikawa.loop import run_loop
from chiikawa.provider import DEFAULT_MODEL
from chiikawa.security import Policy
from chiikawa.tools import core_tools, tool
from demos.day1_dice import load_credentials, on_event

TASK = ("Create five files one.txt through five.txt, each with 20 lines of the word ping, "
        "one write_file at a time with a read back after each; "
        "then MANIFEST.md listing each file and its line count verified with wc -l")


def display(kind, payload):
    """Print compaction counts alongside the existing visible dialogue events."""
    if kind == "compaction":
        print(f"compaction: {payload['messages_before']} -> {payload['messages_after']} messages; "
              f"estimated tokens {payload['tokens_before']:.0f} -> {payload['tokens_after']:.0f}", flush=True)
    else:
        on_event(kind, payload)


def run_task(model, workdir, prompt=TASK, budget_tokens=1500, on_event=display, policy=None):
    """Build fresh context and run a task with observable before-turn compaction.

    This scratch demo defaults to yolo; applications can supply Policy('safe',
    approver=...) or Policy('read-only') without changing the loop or these tools.
    """
    @tool("Load a project's skill instructions", name="Skill name from the catalog")
    def use_skill(name):
        """Return full skill text through a tool result only when requested."""
        return skills.read_skill(workdir, name)

    @tool("Remember a trusted, non-secret project fact", note="Fact to append to project memory")
    def remember(note):
        """Persist a fact for a future conversation, subject to the caller's policy."""
        return memory.remember(workdir, note)

    def before_turn(messages):
        """Measure successful history replacement before the next model request."""
        replacement = compact(model, messages, budget_tokens)
        if replacement is not messages:
            on_event("compaction", {"messages_before": len(messages), "messages_after": len(replacement),
                     "tokens_before": estimate_tokens(messages), "tokens_after": estimate_tokens(replacement),
                     "summary": replacement[0]["text"]})
        return replacement

    system = memory.build_system_prompt(workdir, skills.catalog_prompt(workdir))
    tools = {item.name: item for item in [*core_tools(workdir), use_skill, remember]}
    messages = [{"role": "user", "text": prompt}]
    policy = policy if policy is not None else Policy("yolo")
    answer = run_loop(model, system, messages, tools, on_event, policy.check, before_turn=before_turn)
    return answer, messages


def main():
    """Run the five-file task in a scratch directory using the selected Foundry model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--workdir", help="Scratch directory (default: a new temporary directory)")
    parser.add_argument("--prompt", default=TASK)
    parser.add_argument("--budget-tokens", type=int, default=1500)
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else DEFAULT_MODEL
    root = Path(args.workdir or tempfile.mkdtemp(prefix="chiikawa-day3-")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    print(f"workspace: {root}\nuser: {args.prompt}", flush=True)
    run_task(args.model or model, root, args.prompt, args.budget_tokens)


if __name__ == "__main__":
    main()
