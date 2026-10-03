"""Day 3: live acceptance for mid-run compaction, fresh memory, and file-only skills.

Verify actual filesystem state and ordered tool events, not model claims alone.
Use a new process for memory recall and keep credentials out of saved evidence.
"""

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from chiikawa import memory, provider, system_policy
from demos.day1_dice import load_credentials
from demos.day3_context import TASK, display, run_task

FILES = ["one.txt", "two.txt", "three.txt", "four.txt", "five.txt"]
WRITING_TASK = "Write a two-sentence welcome for new customers of our coffee shop. Reply with just the copy."


def code_digest():
    """Hash the harness and demo source to prove the skill test changes only data."""
    digest = hashlib.sha256()
    for path in sorted([*Path("chiikawa").glob("*.py"), *Path("demos").glob("*.py")]):
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def record_task(model, root, prompt):
    """Run the real Day 3 demo at a 1,500-token trigger and record visible events."""
    events = []

    def observe(kind, payload):
        """Save factual summaries and visible dialogue without raw provider output."""
        events.append({"kind": kind, "payload": {
            key: value for key, value in payload.items() if key != "provider_output"}})
        display(kind, payload)

    print(f"user: {prompt}", flush=True)
    answer, messages = run_task(model, root, prompt, budget_tokens=1500, on_event=observe)
    if not answer.strip():
        raise RuntimeError("The live run did not finish with a visible answer.")
    return {"prompt": prompt, "answer": answer, "events": events}


def verify_files(model, root):
    """Check file contents, alternating writes/reads, wc output, and real compactions."""
    report = record_task(model, root, TASK)
    events = report["events"]
    calls = [event["payload"] for event in events if event["kind"] == "tool_start"]
    file_calls = [(call["name"], call["args"].get("path")) for call in calls
                  if call["name"] in {"write_file", "read_file"}
                  and call["args"].get("path") in FILES]
    expected = [(name, path) for path in FILES for name in ("write_file", "read_file")]
    if file_calls != expected:
        raise RuntimeError(f"Expected one write followed by one read for each file; got {file_calls}")
    for event in events:
        if event["kind"] == "assistant":
            writes = [call for call in event["payload"]["tool_calls"] if call["name"] == "write_file"]
            if len(writes) > 1:
                raise RuntimeError("The model must request one write_file at a time.")
    artifacts = {name: (root / name).read_text(encoding="utf-8") for name in [*FILES, "MANIFEST.md"]}
    if any(artifacts[name] != "ping\n" * 20 for name in FILES):
        raise RuntimeError("Each text file must contain exactly 20 newline-terminated ping lines.")
    independent = subprocess.run(["wc", "-l", *FILES], cwd=root, capture_output=True,
                                 text=True, check=True, timeout=10).stdout
    counts = {line.split()[1]: int(line.split()[0]) for line in independent.splitlines()}
    if any(counts.get(name) != 20 for name in FILES):
        raise RuntimeError("Independent wc verification failed.")
    for name in FILES:
        if not any(name in line and re.search(r"\b20\b", line)
                   for line in artifacts["MANIFEST.md"].splitlines()):
            raise RuntimeError(f"Manifest lacks the verified line count for {name}.")
    wc_calls = [call for call in calls if call["name"] == "bash"
                and re.search(r"\bwc\s+-l\b", call["args"].get("command", ""))]
    wc_ids = {call["call_id"] for call in wc_calls}
    outputs = "\n".join(event["payload"]["text"] for event in events
                        if event["kind"] == "tool_end" and event["payload"].get("call_id") in wc_ids)
    for name in FILES:
        if not re.search(rf"\b20\s+(?:\./)?{re.escape(name)}\b", outputs):
            raise RuntimeError(f"The model must see a successful wc -l result for {name}.")
    compactions = [event["payload"] for event in events if event["kind"] == "compaction"]
    if not compactions or any(item["tokens_before"] <= 1500 for item in compactions):
        raise RuntimeError("Expected actual over-budget compaction events.")
    first = next(index for index, event in enumerate(events) if event["kind"] == "compaction")
    if not any(event["kind"] == "tool_start" for event in events[first + 1:]):
        raise RuntimeError("Compaction must fire mid-run, before more tool work.")
    report.update(passed=True, budget_tokens=1500, compaction_count=len(compactions),
                  artifacts=artifacts, independent_wc=independent)
    return report


def memory_probe(model, root):
    """Answer using rebuilt project context and one fresh user message, with no tools."""
    question = "What is the project's verification code? Reply with only the code."
    system = memory.build_system_prompt(root)
    messages = [{"role": "user", "text": question}]
    project_context = system_policy.project_messages({"memory": memory.read_memory(root), "catalog": ""})
    response = provider.complete(model, system, project_context + messages, [])
    return {"question": question, "system_prompt": system, "initial_messages": messages, "project_context": project_context,
            "tools_available": 0, "answer": response["text"], "tool_calls": response["tool_calls"]}


def verify_memory(model, root):
    """Remember a fresh random fact and recall it in a separate process without history."""
    expected = "coral-" + secrets.token_hex(6)
    note = f"The project's verification code is {expected}."
    memory.remember(root, note)
    print("memory: recalling the persisted fact in a fresh process with no tools", flush=True)
    process = subprocess.run([sys.executable, "-m", "demos.verify_day3", "--memory-probe", str(root),
                              "--model", model], capture_output=True, text=True, timeout=600, check=True)
    report = json.loads(process.stdout)
    if expected not in report["answer"] or report["tool_calls"]:
        raise RuntimeError("The fresh conversation did not recall the fact from project context.")
    if any(expected in message["text"] for message in report["initial_messages"]):
        raise RuntimeError("The recall question must not contain the answer.")
    report.update(passed=True, note=note, fresh_process=True, expected=expected)
    print(f"memory: fresh conversation recalled {expected}", flush=True)
    return report


def verify_skills(model, root):
    """Compare the same writing task before and after installing only a SKILL.md file."""
    before_hash = code_digest()
    baseline = record_task(model, root, WRITING_TASK)
    source = Path(__file__).parent / "fixtures/day3/skills/brand-voice/SKILL.md"
    destination = root / "skills/brand-voice/SKILL.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    styled = record_task(model, root, WRITING_TASK)
    loaded = [event for event in styled["events"] if event["kind"] == "tool_end"
              and event["payload"]["name"] == "use_skill" and "friendly pirate speech" in event["payload"]["text"]]
    if not loaded or "arrr" not in styled["answer"].lower() or "matey" not in styled["answer"].lower():
        raise RuntimeError("The model must load the brand-voice skill and follow its pirate voice.")
    if "arrr" in baseline["answer"].lower() or code_digest() != before_hash:
        raise RuntimeError("Expected a file-only voice change from a non-pirate baseline.")
    return {"passed": True, "baseline": baseline, "with_skill": styled,
            "skill_text": source.read_text(), "code_sha256_before": before_hash,
            "code_sha256_after": code_digest()}


def verify(model=provider.DEFAULT_MODEL):
    """Execute all acceptance cases and save their independently validated evidence."""
    root = Path(tempfile.mkdtemp(prefix="chiikawa-day3-verification-"))
    print(f"workspace: {root}", flush=True)
    roots = {name: root / name for name in ("files", "memory", "skills")}
    for directory in roots.values():
        directory.mkdir()
    reports = {"compaction": verify_files(model, roots["files"]),
               "memory": verify_memory(model, roots["memory"]),
               "skills": verify_skills(model, roots["skills"])}
    destination = Path("docs/day3-live-verification.json")
    destination.write_text(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat(),
        "model": model, "endpoint": provider.api_root(), "workspace": str(root), "checks": reports},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"All three Day 3 live checks passed. Evidence: {destination}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--memory-probe", help=argparse.SUPPRESS)
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else provider.DEFAULT_MODEL
    if args.memory_probe:
        print(json.dumps(memory_probe(args.model or model, args.memory_probe)))
    else:
        verify(args.model or model)
