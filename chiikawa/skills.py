"""Day 3: discover skill metadata cheaply and load full instructions on demand.

Only workspace-contained SKILL.md files are eligible. Descriptions come from
front matter, never body text; discovery and reading execute no skill scripts.
"""

from pathlib import Path

SKILLS_DIR = "skills"


def _description(text):
    """Read a scalar description or a simple YAML folded/literal block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---" or "---" not in [line.strip() for line in lines[1:]]:
        return ""
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if value in {">", ">-", "|", "|-"}:
                block = []
                for continuation in lines[index + 1:]:
                    if continuation and not continuation[0].isspace():
                        break
                    block.append(continuation.strip())
                value = " ".join(block).strip()
            return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value
    return ""


def catalog(workdir):
    """Return a stable name-to-description/path mapping of local skill files."""
    root = Path(workdir).resolve()
    result = {}
    for path in sorted(root.glob(f"{SKILLS_DIR}/*/SKILL.md")):
        resolved = path.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            result[path.parent.name] = {"description": _description(resolved.read_text(encoding="utf-8")),
                                        "path": str(resolved)}
    return result


def catalog_prompt(workdir):
    """Advertise names and descriptions; leave full skill bodies out of the prompt."""
    skills = catalog(workdir)
    return ("Skills available (load one with the use_skill tool when relevant):\n" +
            "\n".join(f"- {name}: {entry['description']}" for name, entry in skills.items())) if skills else ""


def read_skill(workdir, name):
    """Load a catalogued skill by exact name, or list available names on a miss."""
    skills = catalog(workdir)
    return (Path(skills[name]["path"]).read_text(encoding="utf-8") if name in skills else
            f"ERROR: no skill named {name}. Available: {', '.join(skills) or '(none)'}")
