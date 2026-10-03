"""OpenRouter chat completions with correlated tools and private replay state.

Keep this wire format separate from Foundry Responses. Only explicitly labeled
public reasoning summaries may reach the terminal; other reasoning stays opaque.
"""

import copy
import json
import os

from . import provider

NAME = "openrouter"
MODEL_ENV = "OPENROUTER_MODEL"
DEFAULT_MODEL = "openai/gpt-5.4"
MAX_OUTPUT_TOKENS = 16_384
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def api_key():
    """Use only OpenRouter's key, never a Foundry credential or shared fallback."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY to use OpenRouter.")
    return key


def _to_wire(messages):
    """Replay native assistant data and correlate every result with its tool call."""
    output = []
    for message in messages:
        if "provider_output" in message:
            raise ValueError("Foundry replay data cannot be sent to OpenRouter; start a new session.")
        role = message["role"]
        if role == "assistant" and "openrouter_message" in message:
            output.append(copy.deepcopy(message["openrouter_message"]))
        elif role in ("user", "assistant"):
            item = {"role": role, "content": message.get("text", "")}
            if role == "assistant" and message.get("tool_calls"):
                item["tool_calls"] = [{"id": call["call_id"], "type": "function", "function": {
                    "name": call["name"], "arguments": json.dumps(call["args"], ensure_ascii=False)}}
                    for call in message["tool_calls"]]
            output.append(item)
        elif role == "tool":
            if not message.get("call_id"):
                raise ValueError("OpenRouter tool results require a call_id.")
            output.append({"role": "tool", "tool_call_id": message["call_id"],
                           "content": message.get("text", "")})
        else:
            raise ValueError(f"Unknown message role: {role}")
    return output


def complete(model, system, messages, tools, reasoning_summary=False, max_output_tokens=None):
    """Return the harness-neutral response while preserving native reasoning/tool state."""
    if not isinstance(model, str) or "/" not in model or any(char.isspace() for char in model):
        raise ValueError("Use a qualified OpenRouter model ID, such as openai/gpt-5.4.")
    body = {"model": model, "messages": [{"role": "system", "content": system}, *_to_wire(messages)],
            "max_tokens": MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens}
    if tools:
        body["tools"] = [{"type": "function", "function": item["schema"]} for item in tools]
    if reasoning_summary:
        body["reasoning"] = {"effort": "medium", "exclude": False}
    response = _post(body)
    if not isinstance(response, dict):
        raise RuntimeError("OpenRouter returned an invalid response object.")
    if response.get("error"):
        error = response["error"]
        detail = error.get("message", "request failed") if isinstance(error, dict) else str(error)
        raise RuntimeError(f"OpenRouter error: {str(detail)[:400]}")
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise RuntimeError("OpenRouter returned no completion choices.")
    choice = choices[0]
    reason = choice.get("finish_reason")
    if reason not in ("stop", "tool_calls"):
        hint = " Increase --max-output-tokens or choose another model." if reason == "length" else ""
        raise RuntimeError(f"OpenRouter completion did not finish: {reason or 'missing finish_reason'}.{hint}")
    message = choice.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        raise RuntimeError("OpenRouter returned an invalid assistant message.")
    content = message.get("content")
    if isinstance(content, list):
        if any(not isinstance(part, dict) or not isinstance(part.get("text", ""), str)
               for part in content):
            raise RuntimeError("OpenRouter returned unsupported assistant content.")
        text = "".join(part.get("text", "") for part in content if part.get("type") == "text")
    elif content is None or isinstance(content, str):
        text = content or ""
    else:
        raise RuntimeError("OpenRouter returned unsupported assistant content.")
    if not text and isinstance(message.get("refusal"), str):
        text = message["refusal"]
    native_calls = message.get("tool_calls") or []
    if not isinstance(native_calls, list):
        raise RuntimeError("OpenRouter returned invalid tool calls.")
    calls, seen = [], set()
    for call in native_calls:
        try:
            identifier, function = call["id"], call["function"]
            name = function["name"]
            arguments = json.loads(function["arguments"])
            valid = (call.get("type") == "function" and isinstance(identifier, str) and identifier
                     and identifier not in seen and isinstance(name, str) and name and isinstance(arguments, dict))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("OpenRouter returned malformed function-call arguments.") from exc
        if not valid:
            raise RuntimeError("OpenRouter tool calls need unique IDs, names, and JSON-object arguments.")
        seen.add(identifier)
        calls.append({"name": name, "args": arguments, "call_id": identifier})
    if not text and not calls:
        raise RuntimeError("OpenRouter returned no visible text or function calls.")
    if reason == "tool_calls" and not calls:
        raise RuntimeError("OpenRouter reported tool_calls without any calls.")
    # Keep only assistant input fields accepted on replay; omit server metadata.
    native = {"role": "assistant", "content": content or text or None}
    for key in ("tool_calls", "reasoning_details", "reasoning"):
        if key in message:
            native[key] = copy.deepcopy(message[key])
    details = message.get("reasoning_details") or []
    if not isinstance(details, list):
        raise RuntimeError("OpenRouter returned invalid reasoning details.")
    summaries = [part["summary"] for part in details if isinstance(part, dict)
                 and part.get("type") == "reasoning.summary" and isinstance(part.get("summary"), str)]
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        raise RuntimeError("OpenRouter returned invalid usage data.")
    result = {"text": text, "tool_calls": calls, "openrouter_message": native,
              "usage": {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}}
    if summaries:
        result["reasoning_summary"] = "\n\n".join(summaries)
    return result


def _post(body):
    """Share retry behavior, but authenticate only to OpenRouter with its own key."""
    return provider._post(API_URL, body, headers={"Authorization": f"Bearer {api_key()}"}, label="OpenRouter")
