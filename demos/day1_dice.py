"""Day 1: connect a hand-written dice tool to Chiikawa's observable agent loop.

Run from the repository root with ``python3 -m demos.day1_dice``. Credentials
come from the environment or an explicitly selected file; every tool is allowed.
"""

import argparse
import json
import os
import random
from pathlib import Path

from chiikawa.loop import run_loop
from chiikawa.provider import DEFAULT_MODEL

TASK = "Roll 3 dice and tell me whether the total beats 10"


class RollDice:
    """Expose a JSON function schema and a keyword-callable implementation."""

    spec = {"schema": {
        "name": "roll_dice", "description": "Roll count six-sided dice",
        "parameters": {"type": "object", "properties": {
            "count": {"type": "string", "description": "How many dice"}
        }, "required": ["count"]},
    }}

    def run(self, count):
        """Return one independent six-sided roll for each requested die."""
        return [random.randint(1, 6) for _ in range(int(count))]


def on_event(kind, payload):
    """Print visible dialogue and tool activity without opaque reasoning items."""
    if kind == "assistant":
        if payload["text"]:
            print(f"assistant: {payload['text']}", flush=True)
        for call in payload["tool_calls"]:
            print(f"assistant tool call: {call['name']} {json.dumps(call['args'])}",
                  flush=True)
    elif kind == "tool_start":
        print(f"tool start: {payload['name']}", flush=True)
    elif kind == "tool_end":
        print(f"tool result ({payload['name']}): {payload['text']}", flush=True)


def before_tool(call):
    """Allow each call in this intentionally minimal Day 1 demonstration."""
    return None


def load_credentials(filename):
    """Load an explicitly selected local endpoint/model/API_KEY configuration.

    Parse plain key=value lines as data, never as shell code. The selected file
    overrides environment credentials for this process and returns its model.
    """
    values = {}
    for line in Path(filename).read_text(encoding="utf-8").splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            values[name.strip().lower()] = value.strip()
    if not all(values.get(name) for name in ("endpoint", "model", "api_key")):
        raise RuntimeError("Credentials file must define endpoint, model, and API_KEY.")
    os.environ["AZURE_OPENAI_ENDPOINT"] = values["endpoint"]
    os.environ["CHIIKAWA_API_KEY"] = values["api_key"]
    return values["model"]


def main():
    """Run the dice task or a supplied prompt against a Foundry deployment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=TASK)
    parser.add_argument("--model", help=f"Foundry deployment name (default: {DEFAULT_MODEL})")
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else DEFAULT_MODEL
    print(f"user: {args.prompt}", flush=True)
    messages = [{"role": "user", "text": args.prompt}]
    run_loop(args.model or model, "You are Chiikawa, a helpful assistant. Use tools when needed.",
             messages, {"roll_dice": RollDice()}, on_event, before_tool)


if __name__ == "__main__":
    main()
