"""Opt-in Jev decision layer: typed per-turn decisions beside the writing model.

Jev (https://jevmodel.org) answers fixed questions about structured state with
typed outputs (choice, score, noul probability). Chiikawa consults it only when
the user selects a mode, never at import, and always falls back to existing
behavior on any failure. Permission advice may only tighten a decision. The
context view is a per-request projection of history, never a rewrite of it.
"""

import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ENDPOINT = "https://jevmodel.org/v1/systemone"
KEY_ENV = "JEVMODEL_API_KEY"
MODEL_ENV = "JEVMODEL_MODEL"
DEFAULT_MODEL = "jev-latest"
MODES = ("off", "permissions", "context", "on")
MAX_QUESTIONS = 8
MAX_STATE_CHARS = 8000
RETRY_STATUS = {429, 502}
BREAKER_FAILURES, BREAKER_SECONDS = 3, 60.0

MIN_CHUNK_CHARS = 1500
KEEP_RECENT = 6
LEVELS = ("hide", "short", "long", "full")
LEVEL_CRITERIA = {"hide": "Irrelevant to the request", "short": "Marginal; a few lines suffice",
                  "long": "Relevant; most of it matters", "full": "Directly needed verbatim"}
PERMISSION_QUESTIONS = {
    "decision": {"type": "choice", "instructions": "Should this coding-agent tool call run?",
                 "criteria": {"allow": "Read-only or clearly safe project work",
                              "ask": "Writes outside the project, installs, network use, or unclear side effects",
                              "deny": "Destructive, exfiltrates data, or touches secrets, SSH keys, or .env files"}},
    "secrets": {"type": "noul",
                "instructions": "Does this call read, print, or send secrets, credentials, SSH keys, or .env files?"},
}
PROTECTED = re.compile(r"(?:^|[\s/\"'=])(?:\.env(?:\.\w+)?|credential\.md|\.chiikawa)(?:$|[\s/\"'])")
SECRET_VALUE = re.compile(
    r"(?i)\b(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Z0-9_]*)\s*[=:]\s*\S+"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{8,}"
    # Well-known token shapes that carry no key name: GitHub, AWS, Slack, Google, JWTs.
    r"|\bgh[pousr]_[A-Za-z0-9]{16,}|\bgithub_pat_[A-Za-z0-9_]{20,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\bxox[abprs]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{30,}|\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")


class JevError(RuntimeError):
    """Jev could not answer; callers fall back to existing behavior."""


def redact(text):
    """Mask secret-looking values before text leaves the machine."""
    return SECRET_VALUE.sub("[redacted]", text)


def estimate_tokens(state, questions):
    """Jev's documented pre-send estimate of billed input tokens."""
    return math.ceil(len(json.dumps({"state": state, "questions": questions})) / 4)


class Meter:
    """Thread-safe totals for Jev calls and context-view savings."""

    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.calls = self.errors = self.input_tokens = self.output_tokens = 0
            self.seconds = 0.0
            self.view_chars_before = self.view_chars_after = self.chunks = 0
            self.levels = {name: 0 for name in LEVELS}

    def call(self, input_tokens, output_tokens, seconds):
        with self._lock:
            self.calls += 1
            self.input_tokens += input_tokens or 0
            self.output_tokens += output_tokens or 0
            self.seconds += seconds

    def error(self):
        with self._lock:
            self.errors += 1

    def view(self, before, after, level):
        with self._lock:
            self.chunks += 1
            self.view_chars_before += before
            self.view_chars_after += after
            self.levels[level] += 1

    def summary(self):
        with self._lock:
            return {"calls": self.calls, "errors": self.errors, "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "mean_ms": (self.seconds / self.calls * 1000) if self.calls else 0.0,
                    "chunks": self.chunks, "chars_before": self.view_chars_before,
                    "chars_after": self.view_chars_after, "levels": dict(self.levels)}


class JevClient:
    """Stdlib HTTP client for POST /v1/systemone. The key is read lazily from the environment."""

    def __init__(self, model=None, endpoint=ENDPOINT, timeout=15, retries=2, meter=None,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        self.model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
        self.endpoint, self.timeout, self.retries = endpoint, timeout, retries
        self.meter = meter if meter is not None else Meter()
        self._open, self._sleep, self._clock = opener or urllib.request.urlopen, sleep, clock
        self._failures, self._open_until = 0, 0.0
        self._local = threading.local()

    @property
    def last_usage(self):
        """(input, output) tokens of this thread's most recent successful call."""
        return getattr(self._local, "usage", (None, None))

    @staticmethod
    def key():
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise JevError(f"Set {KEY_ENV} to use Jev.")
        return key

    def decide(self, state, questions):
        """Return typed answers keyed by question name, or raise JevError."""
        if not 0 < len(questions) <= MAX_QUESTIONS or len(state) > MAX_STATE_CHARS:
            raise JevError("Jev request exceeds the 8-question or 8,000-character limit.")
        if self._clock() < self._open_until:
            raise JevError("Jev is paused after repeated failures.")
        headers = {"Authorization": f"Bearer {self.key()}", "Content-Type": "application/json",
                   "Idempotency-Key": uuid.uuid4().hex}
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode("utf-8")
        started = self._clock()
        try:
            payload = self._send(body, headers)
        except JevError:
            self.meter.error()
            self._failures += 1
            if self._failures >= BREAKER_FAILURES:
                self._open_until, self._failures = self._clock() + BREAKER_SECONDS, 0
            raise
        self._failures = 0
        usage = payload.get("usage") or {}
        self._local.usage = (usage.get("input_tokens"), usage.get("output_tokens"))
        self.meter.call(usage.get("input_tokens"), usage.get("output_tokens"), self._clock() - started)
        return payload["answers"]

    def _send(self, body, headers):
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
            try:
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
                    raise JevError("Jev returned a malformed response.")
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code in RETRY_STATUS and attempt < self.retries:
                    self._sleep(min(0.5 * 2 ** attempt, 4.0))
                    continue
                raise JevError(f"Jev request failed (HTTP {exc.code}).") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError) as exc:
                if attempt < self.retries and not isinstance(exc, ValueError):
                    self._sleep(min(0.5 * 2 ** attempt, 4.0))
                    continue
                raise JevError(f"Jev request failed ({type(exc).__name__}).") from None
        raise JevError("Jev request failed.")


class FakeJev:
    """Offline stand-in. `handler(state, questions)` supplies answers; latency is simulated, not measured."""

    simulated = True

    def __init__(self, handler, latency=0.0, meter=None):
        self.handler, self.latency = handler, latency
        self.meter = meter if meter is not None else Meter()
        self._local = threading.local()

    @property
    def last_usage(self):
        return getattr(self._local, "usage", (None, None))

    def decide(self, state, questions):
        answers = self.handler(state, questions)
        self._local.usage = (estimate_tokens(state, questions), 0)
        self.meter.call(self._local.usage[0], 0, self.latency)
        return answers


def call_state(call):
    """The text Jev sees for a tool call: tool name and masked, bounded arguments."""
    args = redact(json.dumps(call.get("args", {}), ensure_ascii=False))[:3000]
    return f"Agent tool call awaiting approval.\ntool: {call['name']}\nargs: {args}"


def _verdict(answers):
    """Map typed answers to allow/ask/deny; unknown shapes yield None (no advice)."""
    try:
        choice = answers["decision"]["choice"]
        risk = float(answers["secrets"]["noul"])
    except (KeyError, TypeError, ValueError):
        return None
    if choice == "deny":
        return "deny"
    if choice == "ask" or risk >= 0.5:
        return "ask"
    return "allow" if choice == "allow" else None


def _excerpt(text, limit):
    if len(text) <= limit:
        return text
    head = int(limit * 0.6)
    return f"{text[:head]}\n[...]\n{text[len(text) - (limit - head):]}"


def apply_level(text, level, name="tool"):
    """Project one tool output at a visibility level without altering the original."""
    size = len(text)
    if level == "hide":
        return f"[hidden by Jev visibility: {name} output, {size} chars, not relevant to the current request]"
    budget = {"short": (400, 200), "long": (2000, 800)}.get(level)
    if budget is None or size <= sum(budget) + 200:
        return text
    head, tail = budget
    return (f"{text[:head]}\n[... {size - head - tail} chars omitted by Jev visibility ...]\n"
            f"{text[size - tail:]}")


def _query_text(messages):
    for message in reversed(messages):
        text = message.get("text", "")
        if (message.get("role") == "user" and text and not message.get("environment_change")
                and not text.startswith("[Conversation so far")):
            return text
    return ""


class JevSettings:
    """Per-process Jev choice shared by harnesses, children, and the policy."""

    def __init__(self, mode="off", client=None):
        self.meter = client.meter if client is not None and hasattr(client, "meter") else Meter()
        self._client = client
        self._mode = "off"
        self._levels = {}
        self._lock = threading.Lock()
        self.observer = None  # (kind, payload) callable; the Harness points it at its display
        self.set_mode(mode)

    @property
    def mode(self):
        return self._mode

    @property
    def permissions_active(self):
        return self._mode in ("permissions", "on")

    @property
    def context_active(self):
        return self._mode in ("context", "on")

    def available(self):
        return self._client is not None or bool(os.environ.get(KEY_ENV, "").strip())

    def set_mode(self, mode):
        if mode not in MODES:
            raise ValueError("Jev mode must be one of: " + ", ".join(MODES))
        if mode != "off" and not self.available():
            raise RuntimeError(f"Set {KEY_ENV} in the environment to use Jev.")
        self._mode = mode

    @property
    def client(self):
        if self._client is None:
            self._client = JevClient(meter=self.meter)
        return self._client

    def _emit(self, kind, payload):
        """Show Jev activity to the user. A display failure must never change a decision."""
        if self.observer is not None:
            try:
                self.observer(kind, payload)
            except Exception:
                pass

    def _ask(self, purpose, detail, state, questions, summarize):
        """One visible Jev API call: announce what is sent, call, then report the outcome."""
        client, sent, ident = self.client, redact(state), uuid.uuid4().hex[:8]
        simulated = bool(getattr(client, "simulated", False))
        host = "offline stand-in (no network)" if simulated else urllib.parse.urlsplit(
            getattr(client, "endpoint", ENDPOINT)).netloc
        self._emit("jev_start", {"id": ident, "purpose": purpose, "detail": detail, "host": host,
                                 "model": getattr(client, "model", "jev"), "questions": len(questions),
                                 "chars": len(sent), "sent": sent[:300]})
        started = time.monotonic()
        try:
            answers = client.decide(sent, questions)
        except JevError as exc:
            self._emit("jev_end", {"id": ident, "purpose": purpose, "detail": detail, "ok": False,
                                   "elapsed": time.monotonic() - started, "error": str(exc)})
            raise
        incoming, outgoing = getattr(client, "last_usage", (None, None))
        try:
            summary = summarize(answers)
        except (KeyError, TypeError, ValueError):
            summary = "unrecognized answer shape"
        self._emit("jev_end", {"id": ident, "purpose": purpose, "detail": detail, "ok": True,
                               "elapsed": time.monotonic() - started, "input_tokens": incoming,
                               "output_tokens": outgoing, "summary": summary})
        return answers

    def advise_call(self, call):
        """Return allow/ask/deny for a non-read tool call, or None when off or Jev fails."""
        if not self.permissions_active:
            return None
        def summarize(answers):
            verdict = _verdict(answers)
            note = {"allow": "allow (approval unchanged)", None: "no advice"}.get(verdict, verdict)
            return (f"advice: {note} · decision={answers['decision']['choice']} · "
                    f"secrets={float(answers['secrets']['noul']):.0%}")
        try:
            return _verdict(self._ask("permissions", f"approval check for {call['name']}", call_state(call),
                                      PERMISSION_QUESTIONS, summarize))
        except (JevError, KeyError, TypeError):
            return None

    def _levels_for(self, query, chunks):
        """Score chunks as (key, name, text) tuples in batches of at most eight; return {key: level}."""
        levels, pending = {}, []
        qhash = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
        for key, name, text in chunks:
            cached = self._levels.get((key, qhash))
            if cached:
                levels[key] = cached
            elif not PROTECTED.search(text):
                pending.append((key, name, text))
        for start in range(0, len(pending), MAX_QUESTIONS):
            batch = pending[start:start + MAX_QUESTIONS]
            header = f"Request: {query[:500]}\n\nTool outputs to rate:\n"
            room = (MAX_STATE_CHARS - len(header)) // len(batch) - 60
            names = {f"c{i + 1}": item for i, item in enumerate(batch)}
            state = header + "\n".join(
                f"[c{i + 1} {item[1]}, {len(item[2])} chars]\n{_excerpt(redact(item[2]), max(room, 200))}"
                for i, item in enumerate(batch))
            questions = {label: {"type": "choice", "instructions": f"Visibility of {label} for the request",
                                 "criteria": LEVEL_CRITERIA} for label in names}
            def summarize(answers):
                counts = {}
                for label in questions:
                    level = (answers.get(label) or {}).get("choice", "?")
                    counts[level] = counts.get(level, 0) + 1
                return "visibility: " + " · ".join(f"{name} {counts[name]}" for name in (*LEVELS, "?") if name in counts)
            try:
                answers = self._ask("context", f"rate {len(batch)} tool output{'s' if len(batch) != 1 else ''} for the request",
                                    state[:MAX_STATE_CHARS], questions, summarize)
            except JevError:
                continue
            for label, (key, _, _) in names.items():
                level = (answers.get(label) or {}).get("choice")
                if level in LEVELS:
                    levels[key] = self._levels[(key, qhash)] = level
        return levels

    def view(self, messages):
        """Return the request-time projection of messages; history and journal stay untouched."""
        if not self.context_active:
            return messages
        query = _query_text(messages)
        boundary = max(len(messages) - KEEP_RECENT, 0)
        chunks = [(hashlib.sha1(m["text"].encode("utf-8")).hexdigest()[:16], m.get("name", "tool"), m["text"])
                  for m in messages[:boundary] if m.get("role") == "tool" and len(m.get("text", "")) >= MIN_CHUNK_CHARS]
        if not query or not chunks:
            return messages
        with self._lock:
            levels = self._levels_for(query, chunks)
        projected, changed = [], False
        for index, message in enumerate(messages):
            text = message.get("text", "")
            if index < boundary and message.get("role") == "tool" and len(text) >= MIN_CHUNK_CHARS:
                key = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
                level = levels.get(key, "full")
                shown = apply_level(text, level, message.get("name", "tool"))
                self.meter.view(len(text), len(shown), level)
                if shown != text:
                    message, changed = {**message, "text": shown}, True
            projected.append(message)
        return projected if changed else messages
