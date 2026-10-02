"""Day 1: a sequential, observable model/tool loop with explicit approval hooks.

History uses provider-neutral dictionaries. Tool failures become model-visible
results; provider and application-hook failures remain visible to the caller.
"""

from . import provider


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None):
    """Run tools in order until the model answers or the turn budget expires.

    Mutate the caller's history in place, including after optional compaction.
    Events carry the assistant message, tool call, or completed tool message.
    Approval precedes execution; every attempted call receives a result.

    The optional before_turn hook also runs before the final wrap-up request.
    The turn budget counts model replies, not individual tool executions.
    Tool calls in a single reply share that turn and execute sequentially.
    """
    specs = [tool.spec for tool in tools.values()]

    def reply(available_tools):
        """Apply the history hook, record a model reply, and notify observers."""
        if before_turn is not None:
            messages[:] = before_turn(messages)
        response = provider.complete(model, system, messages, available_tools)
        message = {"role": "assistant", "text": response["text"],
                   "tool_calls": response["tool_calls"]}
        messages.append(message)
        on_event("assistant", message)
        return message

    for _ in range(max_turns):
        message = reply(specs)
        if not message["tool_calls"]:
            return message["text"]

        for call in message["tool_calls"]:
            on_event("tool_start", call)
            reason = before_tool(call)
            if reason is not None:
                # Even an empty reason blocks: only None grants permission.
                result = f"BLOCKED: {reason}"
            elif call["name"] not in tools:
                result = f"ERROR: unknown tool {call['name']}"
            else:
                try:
                    result = str(tools[call["name"]].run(**call["args"]))
                except Exception as exc:
                    # A tool failure belongs in history, allowing model recovery.
                    result = f"ERROR: {type(exc).__name__}: {exc}"
            tool_message = {"role": "tool", "name": call["name"],
                            "text": str(result)}
            messages.append(tool_message)
            on_event("tool_end", tool_message)

    # One final tool-free request gives the model a chance to summarize progress.
    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    return reply([])["text"]
