"""Day 4: run and resume the public Harness using the existing Foundry configuration.

Keep command-line setup small while the package CLI remains a Day 5 stub. A
scratch workspace persists both generated artifacts and its resumable journal.
"""

import argparse
import tempfile
from pathlib import Path

from chiikawa import Harness
from chiikawa.provider import DEFAULT_MODEL
from chiikawa.session import INTERRUPTED
from demos.day1_dice import load_credentials, on_event

TASK = "Create part1.txt through part5.txt one at a time, then SUMMARY.md describing each"


def main():
    """Choose a workspace, optionally resume, and run one task through Harness."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", help="Local endpoint/model/API_KEY file")
    parser.add_argument("--model", help="Foundry deployment name")
    parser.add_argument("--workdir", help="Workspace (required to reuse an earlier run)")
    parser.add_argument("--resume", action="store_true", help="Load this workspace's latest session")
    parser.add_argument("--prompt", help="Task (defaults to creating five parts, or continuing on resume)")
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else DEFAULT_MODEL
    root = Path(args.workdir or tempfile.mkdtemp(prefix="chiikawa-day4-")).resolve()
    harness = Harness(root, model=args.model or model, on_event=on_event)
    print(f"workspace: {root}", flush=True)
    if args.resume:
        if not harness.resume():
            parser.error("No nonempty session found in the selected workspace.")
        for message in harness.messages:
            if message.get("text") == INTERRUPTED:
                print(f"restored tool ({message['name']}): {message['text']}", flush=True)
    task = args.prompt or ("continue the task" if args.resume else TASK)
    print(f"user: {task}", flush=True)
    harness.run(task)
    print(f"session: {harness.session_path}", flush=True)


if __name__ == "__main__":
    main()
