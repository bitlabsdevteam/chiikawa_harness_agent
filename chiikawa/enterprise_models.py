"""Approved text/function-call model protocols for the IT-owned service.

Credentials, system policy, destination checks and quota reservations belong to
the service. This module does not execute provider tools or follow remote URLs.
Opaque assistant state is preserved for replay but never exposed as public text.
"""

import copy
import json
import re

from . import provider as responses, openrouter


def _history(messages):
    if not isinstance(messages, list):
        raise ValueError("Managed conversation must be a list.")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant", "tool"}:
            raise ValueError("Managed conversations cannot supply system/developer roles.")
        if not isinstance(message.get("text", ""), str):
            raise ValueError("Managed conversation text must be a string.")
        if message.get("tool_calls") and message["role"] != "assistant":
            raise ValueError("Only assistant messages can contain tool calls.")
        if message.get("managed_output") is not None and message["role"] != "assistant":
            raise ValueError("Only assistant messages can contain native replay.")
    return messages


def _native(message, protocol):
    native = message.get("managed_output")
    if native is None:
        return None
    if not isinstance(native, dict) or set(native) != {"protocol", "content"} or native["protocol"] != protocol:
        raise ValueError("Managed native replay belongs to a different provider protocol.")
    content = copy.deepcopy(native["content"])
    if not isinstance(content, list) or any(not isinstance(part, dict) for part in content):
        raise ValueError("Invalid managed native replay.")
    if protocol == "responses":
        responses.validate_replay([{"role": "assistant", "provider_output": content}])
    elif protocol == "anthropic":
        if any(part.get("type") not in {"text", "tool_use", "thinking", "redacted_thinking"} for part in content):
            raise ValueError("Unsupported Anthropic replay content.")
    elif protocol == "google":
        if any(set(part) - {"text", "functionCall", "thought", "thoughtSignature"} for part in content):
            raise ValueError("Unsupported Google replay content.")
    elif protocol == "chat-completions":
        if len(content) != 1 or content[0].get("role") != "assistant":
            raise ValueError("Managed chat replay must contain only an assistant message.")
        if set(content[0]) - {"role", "content", "tool_calls", "reasoning", "reasoning_details"}:
            raise ValueError("Unsupported chat replay fields.")
    return content


def build_request(protocol, model, system, messages, tools, cap):
    _history(messages)
    if protocol == "responses":
        neutral = []
        for original in messages:
            message = dict(original)
            native = _native(message, protocol)
            if native is not None:
                message["provider_output"] = native
            neutral.append(message)
        result = {"model": model, "instructions": system, "input": responses._to_wire(neutral),
                  "max_output_tokens": cap, "store": False, "include": ["reasoning.encrypted_content"]}
        if tools:
            result["tools"] = [{"type": "function", **item["schema"], "strict": False} for item in tools]
        return result
    if protocol == "chat-completions":
        neutral = []
        for original in messages:
            message = dict(original)
            native = _native(message, protocol)
            if native is not None:
                message["openrouter_message"] = native[0]
            neutral.append(message)
        result = {"model": model, "messages": [{"role": "system", "content": system}, *openrouter._to_wire(neutral)],
                  "max_tokens": cap}
        if tools:
            result["tools"] = [{"type": "function", "function": item["schema"]} for item in tools]
        return result
    history = []
    for message in messages:
        role = message["role"]
        parts = _native(message, protocol)
        if parts is None:
            parts = []
            if role == "tool":
                if protocol == "anthropic":
                    parts.append({"type": "tool_result", "tool_use_id": message["call_id"], "content": message["text"]})
                else:
                    parts.append({"functionResponse": {"name": message["name"], "id": message["call_id"],
                                                       "response": {"result": message["text"]}}})
            else:
                if message.get("text"):
                    parts.append({"type": "text", "text": message["text"]} if protocol == "anthropic" else {"text": message["text"]})
                for call in message.get("tool_calls", []):
                    parts.append({"type": "tool_use", "id": call["call_id"], "name": call["name"], "input": call["args"]}
                                 if protocol == "anthropic" else {"functionCall": {
                                     "name": call["name"], "args": call["args"], "id": call["call_id"]}})
        if not parts:
            continue
        wire_role = ("assistant" if protocol == "anthropic" else "model") if role == "assistant" else "user"
        key = "content" if protocol == "anthropic" else "parts"
        if history and history[-1]["role"] == wire_role:
            history[-1][key].extend(parts)
        else:
            history.append({"role": wire_role, key: parts})
    if protocol == "anthropic":
        result = {"model": model, "system": system, "messages": history, "max_tokens": cap}
        if tools:
            result["tools"] = [{"name": item["schema"]["name"], "description": item["schema"]["description"],
                                "input_schema": item["schema"]["parameters"]} for item in tools]
        return result
    if protocol == "google":
        result = {"contents": history, "systemInstruction": {"parts": [{"text": system}]},
                  "generationConfig": {"maxOutputTokens": cap}}
        if tools:
            result["tools"] = [{"functionDeclarations": [item["schema"] for item in tools]}]
        return result
    raise ValueError("Unsupported managed model protocol.")


def input_request(protocol, model, body):
    """Request the provider's input count, using exactly the same model input."""
    if protocol == "responses":
        return {key: value for key, value in body.items() if key not in {"max_output_tokens", "store", "include"}}
    if protocol == "anthropic":
        return {key: value for key, value in body.items() if key != "max_tokens"}
    if protocol == "google":
        return {"generateContentRequest": {"model": "models/" + model, **body}}
    return {key: value for key, value in body.items() if key != "max_tokens"}


def endpoint(template, model):
    if "{model}" in template:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
            raise ValueError("A model interpolated into an approved URL must be a simple model identifier.")
        return template.replace("{model}", model)
    return template


def parse_response(protocol, response):
    if not isinstance(response, dict):
        raise RuntimeError("Invalid managed provider response.")
    calls, texts, native = [], [], []

    def call(name, arguments, identifier):
        if (not isinstance(name, str) or not name or not isinstance(arguments, dict)
                or not isinstance(identifier, str) or not identifier
                or any(item["call_id"] == identifier for item in calls)):
            raise RuntimeError("Managed provider returned malformed tool calls.")
        calls.append({"name": name, "args": arguments, "call_id": identifier})

    if protocol == "responses":
        if response.get("status") != "completed":
            raise RuntimeError("Managed Responses request did not complete.")
        native = response.get("output", [])
        for part in native:
            if part["type"] == "message":
                texts.extend(p.get("text", p.get("refusal", "")) for p in part.get("content", [])
                             if p.get("type") in {"output_text", "refusal"})
            elif part["type"] == "function_call":
                call(part["name"], json.loads(part["arguments"]), part["call_id"])
        usage = response.get("usage") or {}
        tokens = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens")}
    elif protocol == "chat-completions":
        choices = response.get("choices") or []
        if len(choices) != 1 or choices[0].get("finish_reason") not in {"stop", "tool_calls"}:
            raise RuntimeError("Managed chat request did not complete.")
        message = choices[0]["message"]
        if message.get("role") != "assistant":
            raise RuntimeError("Managed chat response is not an assistant message.")
        text = message.get("content") or message.get("refusal") or ""
        if not isinstance(text, str):
            raise RuntimeError("Managed chat currently accepts only text completions.")
        texts.append(text)
        for item in message.get("tool_calls") or []:
            if item.get("type") != "function":
                raise RuntimeError("Unsupported managed chat tool call.")
            call(item["function"]["name"], json.loads(item["function"]["arguments"]), item["id"])
        native = [{key: copy.deepcopy(value) for key, value in message.items()
                   if key in {"role", "content", "tool_calls", "reasoning", "reasoning_details"}}]
        usage = response.get("usage") or {}
        tokens = {"input": usage.get("prompt_tokens"), "output": usage.get("completion_tokens")}
    elif protocol == "anthropic":
        if response.get("role") != "assistant" or response.get("stop_reason") not in {"end_turn", "tool_use", "stop_sequence", "refusal"}:
            raise RuntimeError("Managed Anthropic request did not complete.")
        native = response.get("content", [])
        for part in native:
            if part["type"] == "text":
                texts.append(part["text"])
            elif part["type"] == "tool_use":
                call(part["name"], part["input"], part["id"])
        usage = response.get("usage") or {}
        counts = [usage.get("input_tokens"), usage.get("cache_read_input_tokens", 0), usage.get("cache_creation_input_tokens", 0)]
        tokens = {"input": sum(counts) if all(type(n) is int and n >= 0 for n in counts) else None,
                  "output": usage.get("output_tokens")}
    elif protocol == "google":
        candidates = response.get("candidates") or []
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise RuntimeError("Managed Google request did not complete.")
        content = candidates[0].get("content") or {}
        if content.get("role") != "model":
            raise RuntimeError("Managed Google response is not a model message.")
        native = content.get("parts", [])
        for index, part in enumerate(native):
            if "text" in part and not part.get("thought"):
                texts.append(part["text"])
            if "functionCall" in part:
                item = part["functionCall"]
                call(item["name"], item.get("args", {}), item.get("id") or f"google-{index}")
        usage = response.get("usageMetadata") or {}
        prompt, total = usage.get("promptTokenCount"), usage.get("totalTokenCount")
        tokens = {"input": prompt, "output": total - prompt if type(total) is int and type(prompt) is int and total >= prompt else None}
    else:
        raise ValueError("Unsupported managed provider protocol.")
    replay = {"protocol": protocol, "content": native}
    _native({"managed_output": replay}, protocol)
    if any(not isinstance(text, str) for text in texts) or not (any(texts) or calls):
        raise RuntimeError("Managed provider returned no usable text or function call.")
    return {"text": "".join(texts), "tool_calls": calls, "usage": tokens, "managed_output": replay}
