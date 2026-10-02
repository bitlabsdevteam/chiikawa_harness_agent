"""Verify delivered fleet products without making model calls or modifying them.

Source counts are structural checks; browser-visible text and interactions are
recorded separately in docs/day5-browser-verification.json after UI testing.
"""

import ast
from collections import Counter
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys

from chiikawa import session
from demos.day5_products import REVIEW, TASKS


class Structure(HTMLParser):
    """Count semantic sections and inline SVG elements in an HTML document."""

    def __init__(self):
        """Initialize the element counter."""
        super().__init__()
        self.tags = Counter()

    def handle_starttag(self, tag, attrs):
        """Record every opening tag, without counting tags embedded in scripts."""
        self.tags[tag] += 1


def verify(root):
    """Collect independent product evidence and return a JSON-compatible report."""
    specification = (root / "docs/day5-spec.txt").read_text()
    bar = specification.split("quality bar (verbatim): ", 1)[1].split("\n\nThen run", 1)[0]
    results = []
    for name in TASKS:
        project = root / "products" / name
        required = ["DESIGN.md", "QUALITY.md", "REVIEW.md", "skills/design-engineering/SKILL.md"]
        required += ["taskman.py", "test_taskman.py", "README.md"] if name == "taskman" else ["index.html"]
        checks = {"required_files": all((project / path).is_file() for path in required)}
        skill = project / "skills/design-engineering/SKILL.md"
        checks["verbatim_skill_bar"] = skill.is_file() and bar in skill.read_text()
        path = session.latest(project)
        messages = session.load(path) if path else []
        prompts = [message.get("text") for message in messages if message["role"] == "user"]
        checks["same_session_build_and_review"] = TASKS[name] in prompts and REVIEW in prompts
        checks["one_session"] = len(list((project / ".chiikawa/sessions").glob("*.jsonl"))) == 1
        evidence = {}
        if name == "taskman" and checks["required_files"]:
            tree = ast.parse((project / "test_taskman.py").read_text())
            count = sum(isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
                        for node in ast.walk(tree))
            completed = subprocess.run([sys.executable, "-m", "unittest", "-v"],
                                       cwd=project, capture_output=True, text=True, timeout=120)
            checks["ten_or_more_tests"] = count >= 10
            checks["tests_pass"] = completed.returncode == 0
            evidence.update(test_methods=count, test_output=completed.stdout + completed.stderr)
        elif (project / "index.html").is_file():
            html = (project / "index.html").read_text()
            structure = Structure()
            structure.feed(html)
            checks.update(sections=structure.tags["section"] >= 9,
                          inline_svgs=structure.tags["svg"] >= 4,
                          css_tokens=bool(re.search(r"--[\w-]+\s*:", html)),
                          local_storage="localStorage" in html)
            if name == "artisan-coffee":
                checks["accordion"] = structure.tags["details"] > 0 or "accordion" in html.lower()
            else:
                checks["animation_frame"] = "requestAnimationFrame" in html
                checks["canvas"] = structure.tags["canvas"] > 0
            evidence.update(sections=structure.tags["section"], inline_svgs=structure.tags["svg"])
        results.append({"project": name, "pass": all(checks.values()), "checks": checks,
                        "turns": sum(m["role"] == "assistant" for m in messages), "evidence": evidence})
    return {"scope": "Offline artifact and journal checks; see separate browser evidence.", "results": results}


def main():
    """Write reproducible verification evidence and exit nonzero on any shortfall."""
    root = Path(__file__).resolve().parents[1]
    report = verify(root)
    (root / "docs/day5-mechanical-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    for result in report["results"]:
        print(result["project"], "PASS" if result["pass"] else "FAIL", result["checks"])
    return 0 if all(result["pass"] for result in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
