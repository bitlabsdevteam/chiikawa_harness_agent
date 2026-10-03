#!/usr/bin/env python3
"""Live Foundry streaming check. Uses Azure quota; sends only synthetic text.

Run from the source checkout. Credentials are read as data, never printed or
included in the verification report. No project contents are sent to the model.
"""

import argparse
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from chiikawa import provider
from demos.day1_dice import load_credentials


def verify(model):
    started, chunks, times = time.monotonic(), [], []

    def delta(text):
        chunks.append(text)
        times.append(time.monotonic() - started)
        if len(chunks) == 1:
            print("Live Foundry text arrived before completion.", flush=True)

    result = provider.complete(model, "You are a concise coding assistant.",
        [{"role": "user", "text": "Write 20 numbered short tips for naming variables clearly in Python. One sentence per tip."}],
        [], max_output_tokens=2048, on_delta=delta)
    elapsed = time.monotonic() - started
    if len(chunks) < 2 or "".join(chunks) != result["text"] or not times[0] < times[-1] < elapsed:
        raise RuntimeError("Live deltas did not establish incremental, complete text delivery.")
    report = {"provider": "foundry", "model": model, "deltas": len(chunks),
              "first_delta_seconds": round(times[0], 3), "last_delta_seconds": round(times[-1], 3),
              "completed_seconds": round(elapsed, 3), "text_matches_completed_response": True,
              "usage": result["usage"]}

    # Verify the real CLI flushes its preview while its final answer is pending.
    with tempfile.TemporaryDirectory(prefix="chiikawa-live-stream-") as workspace:
        command = [sys.executable, "-m", "chiikawa", "--provider", "foundry", "-m", model,
                   "-d", workspace, "--max-turns", "0", "--max-output-tokens", "2048",
                   "--mode", "read-only", "-p",
                   "Explain incremental model output in 15 short numbered sentences. Do not use tools."]
        env = dict(os.environ, NO_COLOR="1")
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        started = time.monotonic()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        first_preview = first_final = None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if time.monotonic() - started > 600:
                        raise RuntimeError("Live CLI streaming verification timed out.")
                    for key, _ in selector.select(timeout=1):
                        data = os.read(key.fileobj.fileno(), 65536)
                        if not data:
                            selector.unregister(key.fileobj)
                            continue
                        output[key.data].extend(data)
                        if key.data == "stdout" and first_final is None:
                            first_final = time.monotonic() - started
                        if first_preview is None and b"Streaming response (preview)\n" in output["stderr"]:
                            first_preview = time.monotonic() - started
            code = process.wait(timeout=5)
            if code or first_preview is None or first_final is None or first_preview >= first_final:
                raise RuntimeError("CLI did not display a streamed preview before its final answer.")
            final = bytes(output["stdout"]).decode().rstrip("\n")
            logs = list(Path(workspace, ".chiikawa/sessions").glob("*.jsonl"))
            messages = [json.loads(line) for line in logs[0].read_text().splitlines()]
            answers = [m["text"] for m in messages if m["role"] == "assistant"]
            if answers != [final]:
                raise RuntimeError("CLI output and its single completed journal entry disagree.")
            report["cli"] = {"preview_seconds": round(first_preview, 3), "final_answer_seconds": round(first_final, 3),
                             "preview_before_final": True, "single_completed_journal_entry": True}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Optional local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--report", type=Path, default=ROOT / "output/foundry-streaming-verification.json")
    args = parser.parse_args()
    configured = load_credentials(args.credentials) if args.credentials else os.environ.get(provider.MODEL_ENV, provider.DEFAULT_MODEL)
    report = verify(args.model or configured)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
