"""Day 1: translate neutral messages to Microsoft Foundry's Responses API.

Use only the standard library. Keep retries at the transport boundary, correlate
tools by call ID, and replay opaque reasoning items without displaying them.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODEL = "gpt-6-astra"


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


def _to_wire(messages):
    """Replay response items and correlate each tool result with its call ID."""
    items = []
    for message in messages:
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


def complete(model, system, messages, tools):
    """Return visible text, correlated calls, token usage, and replayable output."""
    body = {"model": model, "instructions": system, "input": _to_wire(messages),
            "max_output_tokens": 65536, "store": False,
            "include": ["reasoning.encrypted_content"]}
    # Astra reasoning does not accept temperature; use its default reasoning effort.
    if tools:
        body["tools"] = [{"type": "function", **t["schema"], "strict": False} for t in tools]
    response = _post(api_root() + "/responses", body)
    if response.get("status") != "completed":
        detail = response.get("error") or response.get("incomplete_details") or {}
        raise RuntimeError(f"Foundry response {response.get('status', 'missing status')}: {detail}")
    text, calls = [], []
    output = response.get("output", [])
    for item in output:
        if item["type"] == "message":
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
    return {"text": "".join(text), "tool_calls": calls, "provider_output": output,
            "usage": {"input": usage.get("input_tokens", 0),
                      "output": usage.get("output_tokens", 0)}}


def _post(url, body, retries=5):
    """POST JSON with a 600-second timeout and five transient-failure retries.

    Back off for 2, 4, 8, 16, and 32 seconds. Send the key only in a header.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "api-key": api_key()}, method="POST",
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            exc.close()
            if exc.code not in (429, 500, 502, 503) or attempt == retries:
                raise RuntimeError(f"Foundry HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Foundry request failed: {exc}") from exc
        time.sleep(2**attempt * 2)
