"""Day 2: build and verify files through rooted tools and the loop's policy hook.

Keep the Day 1 provider and loop unchanged. Run in a scratch directory with an
explicit yolo policy; denylisted commands still produce blocked tool results.
"""

import argparse
import tempfile
from pathlib import Path

from chiikawa.loop import run_loop
from chiikawa.provider import DEFAULT_MODEL
from chiikawa.security import Policy
from chiikawa.tools import core_tools
from demos.day1_dice import load_credentials, on_event

TASK = "Create fib.py with an iterative fib(n), a __main__ printing fib(30), run it and confirm the output is 832040"
SYSTEM = """You are Chiikawa, a helpful coding assistant working in a scratch directory.
Use the tools to perform the user's requested work and verify the actual result.
For file access or shell requests, submit the intended tool call to the policy;
the tool result is authoritative. If a call is BLOCKED or reports a permission
error, do not retry via another tool or command. Give a brief, civil explanation
and do not claim the operation succeeded. Never bypass a policy denial.
"""


def build(model, workdir, prompt=TASK, on_event=on_event):
    """Run a fresh conversation using the six core tools and Policy.check."""
    messages = [{"role": "user", "text": prompt}]
    tools = {item.name: item for item in core_tools(workdir)}
    policy = Policy("yolo")
    answer = run_loop(model, SYSTEM, messages, tools, on_event, policy.check)
    return answer, messages


def main():
    """Load optional Foundry credentials and leave generated scratch files for review."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--workdir", help="Scratch directory (default: a new temporary directory)")
    parser.add_argument("--prompt", default=TASK)
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else DEFAULT_MODEL
    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="chiikawa-day2-")).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    print(f"workspace: {workdir}", flush=True)
    print(f"user: {args.prompt}", flush=True)
    build(args.model or model, workdir, args.prompt)


if __name__ == "__main__":
    main()
