"""Day 1: translate neutral messages to Gemini using only the standard library.

Keep HTTP retries at the transport boundary and provider details out of the
loop. Preserve tool thought signatures exactly; omit private thought text.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
DEFAULT_MODEL = "gemini-3.1-pro-preview"


def api_key():
    """Read Chiikawa's key, falling back to the conventional Gemini variable."""
    key = os.environ.get("CHIIKAWA_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Set CHIIKAWA_API_KEY or GEMINI_API_KEY to use Gemini.")
    return key


def _to_wire(messages):
    """Translate the three neutral roles without mutating the stored history."""
    contents = []
    for message in messages:
        role = message["role"]
        if role == "user":
            parts = [{"text": message["text"]}]
        elif role == "assistant":
            parts = [{"text": message["text"]}] if message.get("text") else []
            for call in message.get("tool_calls", []):
                part = {"functionCall": {"name": call["name"], "args": call["args"]}}
                # Gemini 3 requires the original signature on its call part.
                if call.get("signature") is not None:
                    part["thoughtSignature"] = call["signature"]
                parts.append(part)
        elif role == "tool":
            parts = [{"functionResponse": {
                "name": message["name"], "response": {"result": message["text"]}
            }}]
        else:
            raise ValueError(f"Unknown message role: {role}")
        contents.append({"role": "model" if role == "assistant" else "user",
                         "parts": parts})
    return contents


def complete(model, system, messages, tools):
    """Return visible text, signed tool calls, and input/output token counts."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": _to_wire(messages),
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 65536},
    }
    if tools:
        body["tools"] = [{"functionDeclarations": [t["schema"] for t in tools]}]
    url = f"{API_ROOT}/{urllib.parse.quote(model, safe='')}:generateContent"
    response = _post(url, body)
    candidates = response.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates; check prompt safety or model access.")
    text, calls = [], []
    for part in candidates[0].get("content", {}).get("parts", []):
        if "text" in part and not part.get("thought"):
            text.append(part["text"])
        if "functionCall" in part:
            call = part["functionCall"]
            calls.append({"name": call["name"], "args": call.get("args", {}),
                          "signature": part.get("thoughtSignature")})
    usage = response.get("usageMetadata", {})
    return {"text": "".join(text), "tool_calls": calls,
            "usage": {"input": usage.get("promptTokenCount", 0),
                      "output": usage.get("candidatesTokenCount", 0)}}


def _post(url, body, retries=5):
    """POST JSON with up to five retries after the initial request.

    Retry only transient failures, with delays of 2, 4, 8, 16, and 32 seconds.
    Send credentials in a header so they never appear in request URLs.
    """
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key()},
        method="POST",
    )
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            exc.close()
            if exc.code not in (429, 500, 502, 503) or attempt == retries:
                raise RuntimeError(f"Gemini HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Gemini request failed: {exc}") from exc
        time.sleep(2**attempt * 2)
