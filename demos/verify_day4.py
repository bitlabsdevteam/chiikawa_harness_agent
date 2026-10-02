"""Day 4: verify a real SIGKILL/restart and two ephemeral child model conversations.

Stop only the worker process created by this verifier, at an observer boundary
after pending calls are durable. Check journal records and filesystem artifacts.
"""

import argparse
import ast
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from chiikawa import Harness
from chiikawa import provider, session
from demos.day1_dice import load_credentials, on_event
from demos.day4_harness import TASK

DELEGATION_TASK = ("Use spawn_agent twice: delegate writing utils.py with a slugify(text) "
                   "function to one child, and test_utils.py with five asserts to another; "
                   "then run python3 test_utils.py yourself and report")
RECOVERY_GUIDANCE = ("An interruption notice means no durable result was recorded; a tool may have "
                     "had side effects. Inspect the workspace before deciding what to repeat.")


def visible(message):
    """Exclude opaque provider state from the reviewable acceptance report."""
    return {key: value for key, value in message.items() if key != "provider_output"}


def crash_worker(model, root):
    """Pause before the next write after two files exist, awaiting the verifier's SIGKILL."""
    def observe(kind, payload):
        """Expose the reproducible crash point after Harness has synced the pending call."""
        on_event(kind, payload)
        existing = list(root.glob("part[1-5].txt"))
        if kind == "tool_start" and payload["name"] == "write_file" and 2 <= len(existing) < 5:
            print("@@CRASH_READY@@", flush=True)
            while True:
                signal.pause()

    Harness(root, model=model, on_event=observe, enable_subagents=False,
            system_extra=RECOVERY_GUIDANCE).run(TASK)
    raise RuntimeError("The worker finished without reaching the required mid-run crash point.")


def verify_recovery(model, root):
    """Kill a live worker, restore its log, and verify completion without duplicate history."""
    print(f"crash test workspace: {root}", flush=True)
    command = [sys.executable, "-u", "-m", "demos.verify_day4", "--crash-worker", str(root), "--model", model]
    worker = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    transcript = bytearray()
    deadline = time.monotonic() + 600
    killed_at_boundary = False
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([worker.stdout], [], [], 1)
            if ready:
                chunk = os.read(worker.stdout.fileno(), 65536)
                if not chunk:
                    break
                transcript.extend(chunk)
                print(chunk.decode("utf-8", errors="replace"), end="", flush=True)
                if b"@@CRASH_READY@@" in transcript:
                    worker.kill()  # SIGKILL applies only to this verifier-owned worker.
                    killed_at_boundary = True
                    break
        if not killed_at_boundary:
            raise RuntimeError("Worker did not reach a persisted mid-task call before the deadline.")
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.wait(timeout=10)
        transcript.extend(worker.stdout.read())
        worker.stdout.close()
    if worker.returncode != -signal.SIGKILL:
        raise RuntimeError("Expected the worker to exit from SIGKILL.")
    path = session.latest(root)
    before = session._read(path)
    before_files = sorted(item.name for item in root.glob("part[1-5].txt"))
    if not 2 <= len(before_files) < 5:
        raise RuntimeError("The process must stop partway through writing the five files.")
    events = []

    def observe(kind, payload):
        """Capture only the resumed process's visible event stream."""
        events.append({"kind": kind, "payload": visible(payload)})
        on_event(kind, payload)

    resumed = Harness(root, model=model, on_event=observe, enable_subagents=False,
                      system_extra=RECOVERY_GUIDANCE)
    if not resumed.resume():
        raise RuntimeError("The new Harness could not resume the interrupted session.")
    repairs = [message for message in resumed.messages if message.get("text") == session.INTERRUPTED]
    if not repairs:
        raise RuntimeError("Expected the interruption notice in the restored conversation.")
    for message in repairs:
        print(f"restored tool ({message['name']}): {message['text']}", flush=True)
    print("user: continue the task", flush=True)
    answer = resumed.run("continue the task")
    artifacts = {name: (root / name).read_text(encoding="utf-8")
                 for name in [*(f"part{i}.txt" for i in range(1, 6)), "SUMMARY.md"]}
    if not all(text.strip() for text in artifacts.values()):
        raise RuntimeError("All six deliverables must be present and nonempty.")
    if not all(name in artifacts["SUMMARY.md"] for name in artifacts if name != "SUMMARY.md"):
        raise RuntimeError("SUMMARY.md must describe each part file.")
    after = session._read(path)
    if after[:len(before)] != before or sum(message.get("text") == TASK for message in after) != 1:
        raise RuntimeError("Resume duplicated or changed the already-recorded prefix.")
    if len(list((root / session.SESSION_DIR).glob("*.jsonl"))) != 1 or resumed.session_path != path:
        raise RuntimeError("Resume must continue the same single session.")
    return {"passed": True, "task": TASK, "worker_pid": worker.pid, "resume_pid": os.getpid(),
            "worker_exit_code": worker.returncode, "crash_files": before_files,
            "worker_transcript": transcript.decode("utf-8", errors="replace"),
            "repairs": repairs, "answer": answer, "events": events, "artifacts": artifacts,
            "session_path": str(path), "journal": [visible(message) for message in after]}


def verify_delegation(model, root):
    """Verify two parent spawn calls, generated assertions, parent execution, and one journal."""
    events = []

    def observe(kind, payload):
        """Record the visible parent/child stream; use the parent journal for attribution."""
        events.append({"kind": kind, "payload": visible(payload)})
        on_event(kind, payload)

    print(f"delegation workspace: {root}\nuser: {DELEGATION_TASK}", flush=True)
    parent = Harness(root, model=model, on_event=observe)
    answer = parent.run(DELEGATION_TASK)
    journal = session.load(parent.session_path)
    calls = [call for message in journal if message["role"] == "assistant"
             for call in message.get("tool_calls", [])]
    spawns = [call for call in calls if call["name"] == "spawn_agent"]
    if len(spawns) != 2:
        raise RuntimeError("The parent must delegate exactly twice.")
    tests = [call for call in calls if call["name"] == "bash"
             and "python3 test_utils.py" in call["args"].get("command", "")]
    if not tests:
        raise RuntimeError("The parent's own journal must show it running the tests.")
    test_ids = {call["call_id"] for call in tests}
    results = [message["text"] for message in journal
               if message["role"] == "tool" and message.get("call_id") in test_ids]
    if not results or any("Traceback" in result or result.startswith("ERROR:") for result in results):
        raise RuntimeError("The parent did not receive a successful test result.")
    artifacts = {name: (root / name).read_text(encoding="utf-8") for name in ("utils.py", "test_utils.py")}
    tree = ast.parse(artifacts["test_utils.py"])
    if sum(isinstance(node, ast.Assert) for node in ast.walk(tree)) != 5:
        raise RuntimeError("test_utils.py must contain five assertions.")
    if not any(isinstance(node, ast.FunctionDef) and node.name == "slugify"
               for node in ast.walk(ast.parse(artifacts["utils.py"]))):
        raise RuntimeError("utils.py must define slugify(text).")
    independent = subprocess.run([sys.executable, "test_utils.py"], cwd=root,
                                 capture_output=True, text=True, timeout=20, check=True)
    files = [path.resolve() for path in (root / session.SESSION_DIR).glob("*.jsonl")]
    if files != [parent.session_path] or session.latest(root) != parent.session_path:
        raise RuntimeError("Ephemeral children must not create session files or hijack latest().")
    return {"passed": True, "task": DELEGATION_TASK, "answer": answer, "events": events,
            "parent_journal": [visible(message) for message in journal], "parent_test_results": results,
            "artifacts": artifacts, "session_count": len(files), "session_path": str(parent.session_path),
            "independent_test_exit": independent.returncode, "independent_test_stdout": independent.stdout}


def verify(model=provider.DEFAULT_MODEL):
    """Run both real acceptance cases and save their inspected evidence."""
    root = Path(tempfile.mkdtemp(prefix="chiikawa-day4-verification-")).resolve()
    recovery = root / "recovery"
    delegation = root / "delegation"
    recovery.mkdir()
    delegation.mkdir()
    checks = {"recovery": verify_recovery(model, recovery), "delegation": verify_delegation(model, delegation)}
    destination = Path("docs/day4-live-verification.json")
    destination.write_text(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat(),
        "model": model, "endpoint": provider.api_root(), "workspace": str(root), "checks": checks},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Both Day 4 live checks passed. Evidence: {destination}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--crash-worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else provider.DEFAULT_MODEL
    if args.crash_worker:
        crash_worker(args.model or model, Path(args.crash_worker))
    else:
        verify(args.model or model)
