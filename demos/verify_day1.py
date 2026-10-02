"""Day 1: verify the real Gemini dice and coffee flows and save their transcripts.

This command requires credentials and makes live API calls. Only write a passing
report after both acceptance checks succeed; mocks belong in the offline tests.
"""

import ast
import contextlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

from chiikawa.loop import run_loop
from chiikawa.provider import DEFAULT_MODEL, api_key
from demos.day1_dice import RollDice, TASK, before_tool, on_event


def verify():
    """Run both specified prompts, validate history, and persist live evidence."""
    api_key()
    reports = []
    for prompt in (TASK, "Build a landing page for a coffee shop"):
        messages = [{"role": "user", "text": prompt}]
        transcript = io.StringIO()
        with contextlib.redirect_stdout(transcript):
            print(f"user: {prompt}")
            answer = run_loop(
                DEFAULT_MODEL, "You are Chiikawa, a helpful assistant. Use tools when needed.",
                messages, {"roll_dice": RollDice()}, on_event, before_tool,
            )
        print(transcript.getvalue(), end="", flush=True)
        if not answer.strip():
            raise RuntimeError(f"No answer for: {prompt}")
        calls = [call for message in messages if message["role"] == "assistant"
                 for call in message["tool_calls"]]
        if prompt == TASK:
            if [message["role"] for message in messages] != ["user", "assistant", "tool", "assistant"]:
                raise RuntimeError("Dice transcript did not follow user/call/result/answer order.")
            if len(calls) != 1 or calls[0]["name"] != "roll_dice" or calls[0]["args"] != {"count": "3"}:
                raise RuntimeError("Dice prompt did not request exactly three dice.")
            rolls = ast.literal_eval(messages[2]["text"])
            if not isinstance(rolls, list) or len(rolls) != 3 or any(
                    type(roll) is not int or not 1 <= roll <= 6 for roll in rolls):
                raise RuntimeError("Dice tool did not return three six-sided rolls.")
        elif calls:
            raise RuntimeError("Coffee prompt unexpectedly requested a tool.")
        reports.append({"prompt": prompt, "transcript": transcript.getvalue(),
                        "tool_call_count": len(calls), "passed": True})
    destination = Path("docs/day1-live-verification.json")
    destination.write_text(json.dumps({
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "model": DEFAULT_MODEL, "checks": reports,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Both live checks passed. Evidence: {destination}")


if __name__ == "__main__":
    verify()
