"""Day 1: translate neutral messages to Microsoft Foundry's Responses API.

Use only the standard library. Keep retries at the transport boundary, correlate
tools by call ID, and replay opaque reasoning items without displaying them.
"""

import json
import http.client
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODEL = "gpt-6-astra"
MAX_OUTPUT_TOKENS = 65_536
NAME = "foundry"
MODEL_ENV = "CHIIKAWA_MODEL"
SUPPORTS_STREAMING = True


def api_key():
    """Read Chiikawa's override or the standard Azure OpenAI API key."""
    key = os.environ.get("CHIIKAWA_API_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Set CHIIKAWA_API_KEY or AZURE_OPENAI_API_KEY to use Foundry.")
    return key


def api_root():
    """Normalize an Azure resource endpoint or its OpenAI v1 base URL."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
    parsed = urllib.parse.urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/openai/v1")):
        raise RuntimeError("Set AZURE_OPENAI_ENDPOINT to an HTTPS resource URL or /openai/v1 URL.")
    return endpoint if parsed.path else endpoint + "/openai/v1"


def validate_replay(messages):
    """Native assistant replay cannot introduce privileged or user message roles."""
    for message in messages:
        if "provider_output" not in message:
            continue
        output = message["provider_output"]
        if message.get("role") != "assistant" or not isinstance(output, list):
            raise ValueError("Invalid Foundry assistant replay data.")
        for item in output:
            if (not isinstance(item, dict) or item.get("type") not in {"message", "reasoning", "function_call"}
                    or item.get("role", "assistant") != "assistant"):
                raise ValueError("Foundry replay must contain only assistant output; privileged roles are prohibited.")


def _to_wire(messages):
    """Replay response items and correlate each tool result with its call ID."""
    validate_replay(messages)
    items = []
    for message in messages:
        if "openrouter_message" in message:
            raise ValueError("OpenRouter replay data cannot be sent to Foundry; start a new session.")
        role = message["role"]
        if role == "assistant" and "provider_output" in message:
            # Reasoning and function items must travel together on continuation.
            items.extend(message["provider_output"])
        elif role in ("user", "assistant"):
            if message.get("text"):
                items.append({"role": role, "content": message["text"]})
            for call in message.get("tool_calls", []):
                items.append({"type": "function_call", "call_id": call["call_id"],
                              "name": call["name"], "arguments": json.dumps(call["args"])})
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": message["call_id"],
                          "output": message["text"]})
        else:
            raise ValueError(f"Unknown message role: {role}")
    return items


def complete(model, system, messages, tools, reasoning_summary=False, max_output_tokens=None, on_delta=None):
    """Return visible text, correlated calls, token usage, and replayable output."""
    body = {"model": model, "instructions": system, "input": _to_wire(messages),
            "max_output_tokens": MAX_OUTPUT_TOKENS if max_output_tokens is None else max_output_tokens,
            "store": False,
            "include": ["reasoning.encrypted_content"]}
    if reasoning_summary:
        body["reasoning"] = {"effort": "medium", "summary": "auto"}
    # Astra reasoning does not accept temperature; use its default reasoning effort.
    if tools:
        body["tools"] = [{"type": "function", **t["schema"], "strict": False} for t in tools]
    if on_delta is not None:
        body["stream"] = True
        response = _post_stream(api_root() + "/responses", body, on_delta)
    else:
        response = _post(api_root() + "/responses", body)
    return parse_response(response)


def parse_response(response):
    """Only a completed response authorizes replay, accounting, and tool dispatch."""
    if response.get("status") != "completed":
        detail = response.get("error") or response.get("incomplete_details") or {}
        raise RuntimeError(f"Foundry response {response.get('status', 'missing status')}: {detail}")
    text, calls, summaries = [], [], []
    output = response.get("output", [])
    for item in output:
        if item["type"] == "reasoning":
            summaries.extend(part["text"] for part in item.get("summary", [])
                             if part.get("type") == "summary_text" and isinstance(part.get("text"), str))
        elif item["type"] == "message":
            for part in item.get("content", []):
                if part["type"] == "output_text":
                    text.append(part["text"])
                elif part["type"] == "refusal":
                    text.append(part["refusal"])
        elif item["type"] == "function_call":
            args = json.loads(item["arguments"])
            if not isinstance(args, dict):
                raise RuntimeError("Foundry function arguments must be a JSON object.")
            calls.append({"name": item["name"], "args": args, "call_id": item["call_id"]})
    if not text and not calls:
        raise RuntimeError("Foundry returned no visible text or function calls.")
    usage = response.get("usage") or {}
    result = {"text": "".join(text), "tool_calls": calls, "provider_output": output,
              "usage": {"input": usage.get("input_tokens"),
                        "output": usage.get("output_tokens")}}
    if summaries:
        result["reasoning_summary"] = "\n\n".join(summaries)
    return result


def read_stream(stream, on_delta, max_bytes=67_108_864):
    """Read bounded UTF-8 SSE frames; expose only public text/refusal deltas.

    The completed response contains authoritative tools, usage, and opaque replay.
    EOF, [DONE], failure, and incomplete events cannot substitute for completion.
    """
    data, event_name, used, frame_bytes = [], None, 0, 0
    while True:
        line = stream.readline(16_777_217)
        used += len(line)
        frame_bytes += len(line)
        if used > max_bytes or frame_bytes > 16_777_216:
            raise RuntimeError("Foundry stream exceeded its size limit.")
        if not line:
            raise RuntimeError("Foundry stream ended before response.completed.")
        line = line.decode("utf-8").rstrip("\r\n")
        if line:
            field, separator, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data" and separator:
                data.append(value)
            elif field == "event" and separator:
                event_name = value
            continue
        if not data:
            event_name, frame_bytes = None, 0
            continue
        payload = "\n".join(data)
        if payload == "[DONE]":
            raise RuntimeError("Foundry stream ended before response.completed.")
        event = json.loads(payload)
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise RuntimeError("Invalid Foundry stream event.")
        kind = event["type"]
        if event_name is not None and event_name != kind:
            raise RuntimeError("Mismatched Foundry stream event type.")
        data, event_name, frame_bytes = [], None, 0
        if kind in {"response.output_text.delta", "response.refusal.delta"}:
            delta = event.get("delta")
            if not isinstance(delta, str):
                raise RuntimeError("Invalid Foundry text delta.")
            if delta:
                on_delta(delta)
        elif kind == "response.completed":
            response = event.get("response")
            if not isinstance(response, dict) or response.get("status") != "completed":
                raise RuntimeError("Invalid Foundry stream completion.")
            return response
        elif kind in {"error", "response.failed", "response.incomplete"}:
            # Stream errors may contain reflected prompt/credential data.
            raise RuntimeError(f"Foundry stream reported {kind}.")


def _post_stream(url, body, on_delta, retries=5):
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                                              "api-key": api_key()}, method="POST")
    for attempt in range(retries + 1):
        try:
            response = urllib.request.urlopen(request, timeout=600)
            break
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise RuntimeError(f"Foundry HTTP {exc.code} while opening stream.") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise RuntimeError("Foundry could not open response stream.") from exc
        time.sleep(2**attempt * 2)
    # Never retry after receiving headers: emitted text and billing cannot be undone.
    with response:
        if response.headers.get_content_type() != "text/event-stream":
            raise RuntimeError("Foundry did not return an SSE response.")
        try:
            return read_stream(response, on_delta)
        except http.client.HTTPException as exc:
            raise RuntimeError("Foundry response stream was interrupted.") from exc


def _post(url, body, retries=5, *, headers=None, label="Foundry"):
    """POST JSON with a 600-second timeout and five transient-failure retries.

    Back off for 2, 4, 8, 16, and 32 seconds. Send the key only in a header.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **(headers if headers is not None else {"api-key": api_key()})}, method="POST",
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            exc.close()
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise RuntimeError(f"{label} HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise RuntimeError(f"{label} request failed: {exc}") from exc
        time.sleep(2**attempt * 2)
