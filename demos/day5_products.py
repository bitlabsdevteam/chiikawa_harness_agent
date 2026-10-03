"""Day 5: build and critique three real products through the public concurrent fleet.

Each phase uses one independent Harness per project. Review resumes the same
journal; ordered results and turn counts come from recorded model responses.
"""

import argparse
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from chiikawa import Harness, run_fleet
from chiikawa import provider, session
from demos.day1_dice import load_credentials

REVIEW = "Review every file you produced against the skill bar as a demanding design director; list 12 concrete deficiencies; fix them all; verify again."
TASK_GUIDANCE = """Build a finished, usable product in this directory. Load the design-engineering
skill before working. Use only standard libraries; no package installs or external
assets. Do not create git repositories or commits. Keep all source self-contained.
For each HTML product, meet the full skill bar, with more than 1,400 visible prose
words outside script/style and initially hidden controls, at least 9 meaningful
sections and 4 original inline SVG illustrations. For the CLI project, CSS/SVG and
landing-page requirements do not apply; usability, substantive docs and testing do.
Before writing UI code, put a compact subject-specific design plan in DESIGN.md:
4-6 color tokens, type roles, layout/wireframe, and one memorable visual signature.
Critique the plan for generic styling and revise it before implementation.
Always verify concrete outputs using available tools. Save self-review counts,
test commands and limitations in QUALITY.md. During the second design-director
review, write REVIEW.md with exactly 12 numbered concrete deficiencies, fixes and
verification evidence; implement all fixes. Do not merely promise future work.
Python files need module/function docstrings. Keep product copy about the user's
task, not implementation or this harness. Finish with a short factual report.
"""

TASKS = {
    "artisan-coffee": """Build a self-contained index.html for an original specialty coffee roaster in Goa.
Required: sticky nav; hero with a hand-drawn inline SVG product artifact; six origin
cards with prices in INR; three subscription tiers with a working monthly/annual
toggle that updates all prices and savings; accessible brew-guide tabs; an FAQ
accordion; dark-mode toggle persisted in localStorage. Use at least 9 distinct
sections, over 1,400 visible words of specific copy, and at least 4 substantial
original SVG illustrations. No lorem ipsum or external image/font dependencies.
Art direction: a coastal Goan roaster identity with cobalt tile blue, sea-glass,
roasted orange and crisp pale paper; an illustrated coffee bag as the hero's
signature. Give the origins and sourcing specific, honest descriptions; avoid
inventing endorsements or fake checkout claims. Design for 360, 768 and 1280px.
All buttons must work, including a usable mobile nav. Verify counts and behaviors
you can execute, and save your self-review. Deliver the actual complete files.""",
    "taskman": """Build taskman.py, a Python standard-library CLI task manager with argparse
subcommands add, list, done, rm, stats; JSON persistence; and readable aligned
table output. Support --store PATH so every test can use a temporary isolated
store. Use stable task IDs, clear validation errors, atomic writes, and preserve
existing data on malformed input. Handle empty lists, duplicate completion,
unknown IDs, blank descriptions, Unicode, corrupt storage and permission errors
deliberately. Add test_taskman.py with at least 10 meaningful unittest cases that
invoke the CLI using subprocess and temporary stores. Run all tests, fix failures,
and ship them green. Provide README.md with runnable examples and clear storage
semantics. Apply the design skill's usability and self-review bar to this CLI;
do not add an unrelated webpage to satisfy web-only criteria.""",
    "viper": """Build a finished canvas snake game in one self-contained index.html called Viper.
Required: grid movement driven by requestAnimationFrame; food; speed increases
after every 5 foods; visible score; pause/resume; restart; persistent high score
in localStorage. Provide keyboard arrows/WASD and touch controls, prevent reverse
direction and accidental double-turn collisions, and handle visibility changes
and storage failures gracefully. Make game state and controls accessible.
Give it a distinctive indigo-and-mango arcade cabinet identity, with a large
playable canvas in the hero, tactile controls and a friendly illustrated viper.
The surrounding game page must also meet the whole design skill: at least 9
meaningful sections, more than 1,400 visible words of useful real instructions,
strategy and game explanations, 4 original inline SVG illustrations (including
a hero product artifact), a CSS token system, focus states and responsiveness at
360, 768 and 1280px. No external scripts, fonts, or assets. Include a self-review
and executable logic tests where practical. Do not start gameplay before the
player chooses Start. Deliver actual complete files and verify your work.""",
}


def run_phase(model, phase, names=None, review_prompt=None):
    """Run one fleet phase, resuming existing sessions for any review phase."""
    names = names or list(TASKS)
    lock = threading.Lock()
    roots = {name: (Path("products") / name).resolve() for name in names}

    def make_harness(workdir):
        """Construct an isolated project agent and attach bounded progress output."""
        name = Path(workdir).name

        def observe(kind, payload):
            """Print short project-tagged progress without source blobs or raw reasoning."""
            if kind == "tool_start":
                args = payload.get("args", {})
                detail = args.get("path") or args.get("name") or args.get("command", "")[:100]
                line = f"[{name}/{phase}] {payload['name']} {detail}"
            elif kind == "assistant" and payload.get("text"):
                line = f"[{name}/{phase}] {payload['text'][:700]}"
            else:
                return
            with lock:
                print(line, flush=True)

        harness = Harness(workdir, model=model, on_event=observe, enable_subagents=False,
                          max_turns=120)
        if phase != "build" and not harness.resume():
            raise RuntimeError(f"No prior session to review for {name}")
        if phase == "build" and session.latest(workdir) is not None:
            raise RuntimeError(f"Build phase requires a fresh session for {name}")
        return harness

    jobs = [{"name": name, "workdir": roots[name],
             "task": TASK_GUIDANCE + "\n\n" + (TASKS[name] if phase == "build" else (review_prompt or REVIEW))} for name in names]
    results = run_fleet(jobs, make_harness, max_workers=3)
    for result in results:
        path = session.latest(roots[result["name"]])
        messages = session.load(path) if path else []
        result["turns"] = sum(message["role"] == "assistant" for message in messages)
        result["session_path"] = str(path) if path else None
    destination = Path("docs/day5-fleet-runs.json")
    history = json.loads(destination.read_text()) if destination.exists() else []
    history.append({"phase": phase, "model": model, "at": datetime.now(timezone.utc).isoformat(), "results": results})
    destination.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"phase": phase, "results": results}, ensure_ascii=False, indent=2), flush=True)
    if not all(result["ok"] for result in results):
        raise RuntimeError("One or more fleet jobs failed; inspect their reports and resume them.")


def main():
    """Run build and review phases, or a targeted additional review without losing state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials")
    parser.add_argument("--model")
    parser.add_argument("--phase", choices=("all", "build", "review", "followup"), default="all")
    parser.add_argument("--project", choices=list(TASKS), action="append")
    parser.add_argument("--review-file", help="UTF-8 file containing additional review instructions")
    args = parser.parse_args()
    model = load_credentials(args.credentials) if args.credentials else provider.DEFAULT_MODEL
    for phase in ("build", "review") if args.phase == "all" else (args.phase,):
        run_phase(args.model or model, phase, args.project,
                  Path(args.review_file).read_text() if args.review_file else None)


if __name__ == "__main__":
    main()
