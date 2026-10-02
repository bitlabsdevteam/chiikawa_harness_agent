"""Day 1: connect a hand-written dice tool to Chiikawa's observable agent loop.

Run from the repository root with ``python3 -m demos.day1_dice``. Credentials
come from the environment; this demo allows every requested tool call.
"""

import argparse
import json
import random

from chiikawa.loop import run_loop
from chiikawa.provider import DEFAULT_MODEL

TASK = "Roll 3 dice and tell me whether the total beats 10"


class RollDice:
    """Expose an explicit Gemini schema and a keyword-callable implementation."""

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
    """Print visible dialogue and tool activity without opaque signatures."""
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


def main():
    """Run the dice task or a supplied prompt against the selected Gemini model."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=TASK)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    print(f"user: {args.prompt}", flush=True)
    messages = [{"role": "user", "text": args.prompt}]
    run_loop(args.model, "You are Chiikawa, a helpful assistant. Use tools when needed.",
             messages, {"roll_dice": RollDice()}, on_event, before_tool)


if __name__ == "__main__":
    main()
