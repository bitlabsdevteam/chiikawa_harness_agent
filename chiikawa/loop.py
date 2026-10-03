"""Day 1: a sequential, observable model/tool loop with explicit approval hooks.

History uses provider-neutral dictionaries. Tool failures become model-visible
results; provider and application-hook failures remain visible to the caller.
"""

from . import provider
from time import monotonic


def run_loop(model, system, messages, tools, on_event, before_tool,
             max_turns=80, before_turn=None, activity=False, reasoning_summary=True,
             backend=None, max_output_tokens=None, context_messages=None):
    """Run tools in order until the model answers or the turn budget expires.

    Mutate the caller's history in place, including after optional compaction.
    Events carry the assistant message, tool call, or completed tool message.
    Approval precedes execution; every attempted call receives a result.

    The optional before_turn hook also runs before the final wrap-up request.
    The turn budget counts model replies, not individual tool executions.
    Tool calls in a single reply share that turn and execute sequentially.
    A callable system supplier refreshes runtime facts before each model request.
    """
    backend = backend if backend is not None else provider
    output_limit = backend.MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens
    specs = [tool.spec for tool in tools.values()]

    def reply(available_tools):
        """Apply the history hook, record a model reply, and notify observers."""
        if activity:
            on_event("model_start", {"model": model})
        succeeded = False
        try:
            if before_turn is not None:
                messages[:] = before_turn(messages)
            options = {"reasoning_summary": True} if activity and reasoning_summary else {}
            if max_output_tokens is not None:
                options["max_output_tokens"] = max_output_tokens
            if activity and getattr(backend, "SUPPORTS_STREAMING", False) is True:
                options["on_delta"] = lambda text: on_event("assistant_delta", {"text": text})
            request_messages = [dict(item) for item in context_messages] + messages if context_messages else messages
            current_system = system() if callable(system) else system
            response = backend.complete(model, current_system, request_messages, available_tools, **options)
            succeeded = True
        finally:
            if activity:
                on_event("model_end", {"model": model, "ok": succeeded})
        message = {"role": "assistant", "text": response["text"],
                   "tool_calls": response["tool_calls"]}
        if "provider_output" in response:
            message["provider_output"] = response["provider_output"]
        if "openrouter_message" in response:
            message["openrouter_message"] = response["openrouter_message"]
        if "managed_output" in response:
            message["managed_output"] = response["managed_output"]
        if "reasoning_summary" in response:
            message["reasoning_summary"] = response["reasoning_summary"]
        messages.append(message)
        if activity and reasoning_summary and response.get("reasoning_summary"):
            on_event("reasoning", {"text": response["reasoning_summary"]})
        on_event("assistant", message)
        if activity:
            on_event("usage", {**(response.get("usage") or {}), "source": "response",
                               "max_output_tokens": output_limit})
        return message

    for _ in range(max_turns):
        message = reply(specs)
        if not message["tool_calls"]:
            return message["text"]

        for call in message["tool_calls"]:
            on_event("tool_start", call)
            started = monotonic()
            reason = before_tool(call)
            if reason is not None:
                # Even an empty reason blocks: only None grants permission.
                result = f"BLOCKED: {reason}"
            elif call["name"] not in tools:
                result = f"ERROR: unknown tool {call['name']}"
            else:
                try:
                    result = tools[call["name"]].run(**call["args"])
                except Exception as exc:
                    # A tool failure belongs in history, allowing model recovery.
                    result = f"ERROR: {type(exc).__name__}: {exc}"
            tool_message = {"role": "tool", "name": call["name"],
                            "text": str(result)}
            if "call_id" in call:
                tool_message["call_id"] = call["call_id"]
            if activity:
                details = getattr(result, "details", {})
                status = ("blocked" if reason is not None else "error" if
                          str(result).startswith("ERROR:") or details.get("exit_code", 0) != 0 else "done")
                tool_message.update(status=status, details=details, elapsed=monotonic() - started)
            messages.append(tool_message)
            on_event("tool_end", tool_message)

    # One final tool-free request gives the model a chance to summarize progress.
    messages.append({"role": "user", "text": "Turn limit reached; wrap up now."})
    return reply([])["text"]
