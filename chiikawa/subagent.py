"""Day 4: bounded delegation through the existing Tool interface.

Children receive a self-contained task and fresh context. The factory owns
workspace and policy inheritance; depth is checked before constructing a child.
"""

from .tools import tool


def subagent_tool(make_harness, depth=0, max_depth=2):
    """Build a spawn_agent tool with a fixed depth ceiling and an injected factory.

    A factory keeps this module independent of Harness and makes child lifecycle
    behavior directly testable. Only the final report crosses back to the parent.
    """
    @tool(
        "Delegate a self-contained task to a fresh sub-agent with its own clean context. "
        "The child cannot see this conversation. Include all necessary context in the task. "
        "Returns the child's final report.",
        task="Self-contained task, including context and expected deliverables",
    )
    def spawn_agent(task):
        """Run one child, or ask the caller to work directly at the depth ceiling."""
        if depth >= max_depth:
            return "ERROR: sub-agent depth limit reached; do this task yourself"
        return make_harness(depth + 1).run(task)
    return spawn_agent
