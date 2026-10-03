"""Trusted single-request worker. Invoked with python -I, never from project code."""

import json
from pathlib import Path
import sys

# Only this read-only, host-supplied directory is added to Python's isolated path.
sys.path.insert(0, str(Path(__file__).parent))
import memory
import skills
from tools import core_tools


def dispatch(request):
    root = "/workspace"
    name, args = request["name"], request.get("args", {})
    if name == "environment":
        catalog = skills.catalog_prompt(root)
        return {"catalog": catalog, "memory": memory.read_memory(root)}
    if name == "remember":
        return memory.remember(root, **args)
    if name == "use_skill":
        return skills.read_skill(root, **args)
    return {item.name: item for item in core_tools(root)}[name].run(**args)


if __name__ == "__main__":
    try:
        result = dispatch(json.load(sys.stdin))
        response = {"result": result, "details": getattr(result, "details", {})}
    except Exception as exc:
        response = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(response, ensure_ascii=False), flush=True)
