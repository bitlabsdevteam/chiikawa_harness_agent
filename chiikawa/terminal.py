"""Live terminal activity with bounded previews and a separate answer stream."""

import os
import math
import random
import shutil
import sys
import textwrap
import threading
import time
from contextlib import contextmanager

from .prompt import clip


AI_JOKES = (
    "My neural net goes to the gym for the weights.",
    "AI snacks? Microchips and byte-sized cookies.",
    "My chatbot's vacation got lost in the cloud.",
    "I told my model a joke. It needed more context.",
    "This AI has trust issues. It validates everything.",
    "My model joined a band. It plays the algorithm.",
    "AI coffee comes with a little Java.",
    "My neural net is knot very good at knitting.",
    "The model's favorite shoes? Training sneakers.",
    "My chatbot tried yoga. It stretched the context.",
    "AI gardening: just add a random seed.",
    "The robot chef only makes neural noodles.",
    "My AI's favorite exercise is prompt-ups.",
    "AI campfire stories always end in hallucinations.",
    "AI dating advice: avoid overfitting your type.",
    "My chatbot's autobiography is mostly predictions.",
    "My model's favorite sport? Gradient descent.",
    "AI weather: cloudy with a chance of tokens.",
    "My model writes cliffhangers. Token limit reached.",
    "The robot librarian keeps losing its context.",
)
JOKE_INTERVAL = 5.0


def safe_text(value):
    """Escape terminal control characters in provider, command, and file output."""
    return "".join(character if character in "\n\t" or
                   (ord(character) >= 32 and not 127 <= ord(character) <= 159)
                   else f"\\x{ord(character):02x}" for character in str(value))


def activity_text(kind, payload):
    """Describe public activity without inspecting reasoning state or file contents."""
    if kind == "tool_start":
        name, args = payload.get("name"), payload.get("args", {})
        if name in {"read_file", "write_file", "edit_file"}:
            verb = {"read_file": "Reading", "write_file": "Writing", "edit_file": "Editing"}[name]
            return f"{verb} {args.get('path', 'file')}"
        if name == "bash":
            return f"Running {args.get('command', 'command')}"
        return {"list_files": "Listing project files", "grep": "Searching project files",
                "remember": "Updating project memory", "use_skill": "Loading project instructions",
                "spawn_agent": "Delegating work"}.get(name, "Running tool")
    if kind == "tool_end":
        return {"error": "Reviewing tool failure", "blocked": "Reviewing blocked action"}.get(
            payload.get("status"), "Reviewing tool result")
    return {"model_start": "Thinking...", "model_end": "Processing model response",
            "compaction_start": "Compacting context", "compaction": "Continuing with compacted context",
            "reasoning": "Reviewing response summary", "assistant": "Preparing next action"}.get(kind)


class TerminalDisplay:
    """Render synchronous tool events and a TTY-only waiting animation.

    Activity goes to stderr; final answers go to stdout. No opaque provider
    objects are inspected. close() joins the animation before returning to input.
    """

    def __init__(self, stream=None, answer_stream=None):
        self.stream = stream if stream is not None else sys.stderr
        self.answer_stream = answer_stream if answer_stream is not None else sys.stdout
        self.tty = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self.color = self.tty and "NO_COLOR" not in os.environ
        self.pending = {}
        self.lock = threading.RLock()
        self.stopped = threading.Event()
        self.worker = None
        self.started = None
        self.jokes = []
        self.joke = None
        self.wait_label = "Thinking"
        self.task_running = False
        self.activity = "Starting task"
        self.model_started = None
        self.stream_text = ""
        self.stream_plain = False

    def _stream_delta(self, text):
        with self.lock:
            self.stream_text = (self.stream_text + text)[-12000:]
            self.activity = "Receiving response"
            if not self.tty:
                if not self.stream_plain:
                    self.line("Streaming response (preview)", "2")
                    self.stream_plain = True
                print(safe_text(text), end="", file=self.stream, flush=True)
            else:
                self._paint_working()

    def _finish_stream(self, ok=True):
        with self.lock:
            had_text = bool(self.stream_text)
            if self.stream_plain:
                print(file=self.stream, flush=True)
            self.stream_text, self.stream_plain = "", False
            if had_text and not ok:
                self.line("Stream interrupted; partial response was not saved.", "2")

    def _working_text(self):
        elapsed = max(0, time.monotonic() - self.started) if self.started is not None else 0
        return safe_text(f"Working... {elapsed:.1f}s · {self.activity}").expandtabs(4).replace("\n", " ")

    @staticmethod
    def thinking_frame():
        return int(time.monotonic() / 0.12) % 16

    def thinking(self, text, frame=None):
        """Style an already escaped/clipped status, keeping all three dots in place."""
        if not self.tty or not self.color or "Thinking..." not in text:
            return text
        frame = self.thinking_frame() if frame is None else frame
        dots = []
        for index in range(3):
            # Stagger a smooth brightness wave without moving or adding dots.
            brightness = round(45 + 170 * (1 + math.cos((frame - index * 3) * math.tau / 16)) / 2)
            dots.append(f"\033[38;2;0;{brightness};0m.\033[0m")
        return text.replace("Thinking...", self.style("Thinking", "32") + "".join(dots), 1)

    def _paint_working(self):
        if not self.task_running:
            return
        with self.lock:
            text = self._working_text()
            if self.tty:
                if self.stream_text:
                    text += " · " + safe_text(self.stream_text[-200:]).expandtabs(4).replace("\n", " ")
                if self.activity == "Thinking..." and self.joke:
                    text += "  " + self.joke
                columns = max(1, shutil.get_terminal_size((88, 24)).columns - 1)
                text = clip(text, columns)
                if self.activity == "Thinking...":
                    text = self.thinking(text)
                print("\r\033[2K" + text, end="", file=self.stream, flush=True)
            else:
                self.line(text, "2")

    def _start_animation(self):
        if self.tty and self.worker is None:
            if self.joke is None:
                self._next_joke()
            self.stopped.clear()
            self.worker = threading.Thread(target=self._animate, daemon=True)
            self.worker.start()

    def _stop_animation(self):
        self.stopped.set()
        if self.worker is not None:
            self.worker.join()
            self.worker = None
            with self.lock:
                print("\r\033[2K", end="", file=self.stream, flush=True)

    @contextmanager
    def task(self, text):
        self.started = time.monotonic()
        self.task_running, self.activity = True, "Starting task"
        try:
            self._start_animation()
            self._paint_working()
            yield
        finally:
            self.close()

    @contextmanager
    def approval(self):
        previous = self.activity
        self._stop_animation()
        self.activity = "Waiting for approval"
        self.line(self._working_text(), "2")
        try:
            yield
        finally:
            self.activity = previous
            if self.task_running:
                self._start_animation()
                self._paint_working()

    def _next_joke(self):
        """Shuffle each full deck; avoid repeating at the boundary between decks."""
        if not self.jokes:
            self.jokes = list(AI_JOKES)
            random.shuffle(self.jokes)
            if self.jokes[-1] == self.joke:
                self.jokes[0], self.jokes[-1] = self.jokes[-1], self.jokes[0]
        self.joke = self.jokes.pop()

    def style(self, text, code):
        return f"\033[{code}m{text}\033[0m" if self.color else text

    def line(self, text="", code=None):
        with self.lock:
            if self.worker is not None:
                print("\r\033[2K", end="", file=self.stream, flush=True)
            rendered = safe_text(text)
            print(self.style(rendered, code) if code else rendered, file=self.stream, flush=True)

    def preview(self, text, max_lines=6, max_chars=1600):
        clean = safe_text(text)
        clipped = len(clean) > max_chars
        clean = clean[:max_chars]
        width = max(20, min(shutil.get_terminal_size((88, 24)).columns - 6, 120))
        lines = []
        for line in clean.splitlines():
            lines.extend(textwrap.wrap(line, width=width, replace_whitespace=False) or [""])
        for line in lines[:max_lines]:
            self.line(f"    {line}", "2")
        if clipped or len(lines) > max_lines:
            self.line("    ... preview truncated ...", "2")

    def _animate(self):
        frames = "|/-\\"
        index = 0
        next_joke_at = JOKE_INTERVAL
        while not self.stopped.wait(0.12):
            if self.task_running:
                elapsed = time.monotonic() - self.started
                if self.activity == "Thinking..." and elapsed >= next_joke_at:
                    with self.lock:
                        self._next_joke()
                    next_joke_at = elapsed + JOKE_INTERVAL
                self._paint_working()
                continue
            with self.lock:
                elapsed = time.monotonic() - self.started
                if elapsed >= next_joke_at:
                    self._next_joke()
                    next_joke_at = elapsed + JOKE_INTERVAL
                status = f"{frames[index % len(frames)]} {self.wait_label}... {elapsed:.1f}s"
                text = f"{status}  {self.joke}"
                # Reserve the final column to avoid wrapping and leaving stale lines.
                width = max(1, shutil.get_terminal_size((88, 24)).columns - 1)
                if len(text) > width:
                    text = text[:width - 3] + "..." if width >= 3 else text[:width]
                if self.wait_label == "Thinking":
                    text = self.thinking(text)
                print(f"\r\033[2K{text}",
                      end="", file=self.stream, flush=True)
            index += 1

    def close(self):
        self._stop_animation()
        self.task_running = False
        self._finish_stream()

    @staticmethod
    def describe(call):
        name, args = call.get("name", "tool"), call.get("args", {})
        if name in ("read_file", "write_file", "edit_file"):
            action = {"read_file": "Read", "write_file": "Write", "edit_file": "Edit"}[name]
            return f"{action} {args.get('path', '(missing path)')}"
        if name == "bash":
            return "Run shell command"
        if name == "list_files":
            return f"List files {args.get('pattern', '**/*')}"
        if name == "grep":
            return f"Search {args.get('pattern', '*')} for {args.get('regex', '')}"
        if name == "use_skill":
            return f"Load skill {args.get('name', '')}"
        if name == "remember":
            return "Update CHIIKAWA.md memory"
        if name == "spawn_agent":
            return "Delegate to sub-agent"
        return f"Use {name}"

    def __call__(self, kind, payload):
        if kind == "assistant_delta":
            self._stream_delta(payload["text"])
            return
        if kind == "model_end":
            self._finish_stream(payload.get("ok", True))
        if self.task_running:
            self.activity = activity_text(kind, payload) or self.activity
            if kind == "assistant" and not payload.get("tool_calls") and not self.pending:
                self.close()
        self._event(kind, payload)
        self._paint_working()

    def _event(self, kind, payload):
        if kind == "context":
            estimated, threshold = payload["estimated_tokens"], payload["threshold"]
            ratio = f" ({estimated / threshold:.1%})" if threshold else ""
            note = ""
            if estimated > threshold:
                note = " · compaction pending" if payload["can_compact"] else " · waiting for more history"
            self.line(f"Context history (est.): ~{math.ceil(estimated):,} tokens · "
                      f"compact above {threshold:,}{ratio}{note}", "2")
        elif kind == "usage":
            incoming, outgoing = payload.get("input"), payload.get("output")
            input_text = f"{incoming:,}" if isinstance(incoming, int) else "not reported"
            output_text = f"{outgoing:,}" if isinstance(outgoing, int) else "not reported"
            label = "Compaction tokens" if payload.get("source") == "compaction" else "Response tokens"
            self.line(f"{label} (API): input {input_text} · output {output_text} / "
                      f"{payload['max_output_tokens']:,} limit", "2")
        elif kind == "compaction_start":
            self.wait_label = "Compacting context"
            self.line(f"Compacting context above {payload['threshold']:,} estimated tokens...", "1;35")
        elif kind == "compaction":
            self.wait_label = "Thinking"
            self.line(f"Compacted context: ~{math.ceil(payload['tokens_before']):,} -> "
                      f"~{math.ceil(payload['tokens_after']):,} tokens", "1;35")
        elif kind == "model_start":
            if self.task_running:
                self.model_started = time.monotonic()
                return
            self.close()
            self.wait_label = "Thinking"
            self.started = time.monotonic()
            if self.tty:
                self._next_joke()
                self.stopped.clear()
                self.worker = threading.Thread(target=self._animate, daemon=True)
                self.worker.start()
            else:
                self.line("Thinking...", "2")
        elif kind == "model_end":
            if not self.task_running:
                self.close()
            started = self.model_started if self.task_running else self.started
            if started is not None:
                label = "Model response received" if payload.get("ok", True) else "Model request stopped"
                self.line(f"{label} ({time.monotonic() - started:.1f}s)", "2")
                if not self.task_running:
                    self.started = None
        elif kind == "reasoning":
            self.line("Reasoning summary", "1;35")
            self.preview(payload.get("text", ""), max_lines=14, max_chars=2400)
        elif kind == "assistant" and payload.get("text"):
            if payload.get("tool_calls"):
                self.line("Progress", "1")
                self.preview(payload["text"], max_lines=8)
            else:
                print(safe_text(payload["text"]), file=self.answer_stream, flush=True)
        elif kind == "tool_start":
            key = payload.get("call_id", payload.get("name"))
            self.pending[key] = payload
            title = self.describe(payload).replace("\n", " ")[:200]
            self.line(f"> {title} [{payload.get('name', 'tool')}]", "1;36")
            args = payload.get("args", {})
            if payload.get("name") == "bash":
                self.preview(args.get("command", ""), max_lines=4, max_chars=800)
            elif payload.get("name") == "spawn_agent":
                self.preview(args.get("task", ""), max_lines=3, max_chars=500)
        elif kind == "tool_end":
            key = payload.get("call_id", payload.get("name"))
            call = self.pending.pop(key, {"name": payload.get("name"), "args": {}})
            details = payload.get("details", {})
            status = payload.get("status", "done")
            elapsed = payload.get("elapsed", 0)
            label = {"done": "Done", "error": "Failed", "blocked": "Blocked"}.get(status, status)
            title = self.describe(call).replace("\n", " ")[:200]
            extras = ""
            if "exit_code" in details:
                extras += f" · exit {details['exit_code']}"
            if "lines" in details:
                extras += f" · {details['lines']} {'line' if details['lines'] == 1 else 'lines'}"
            if "added" in details:
                extras += f" · {details['operation']} +{details['added']} -{details['removed']}"
            self.line(f"  {label}: {title}{extras} ({elapsed:.1f}s)", "32" if status == "done" else "31")
            if status == "done" and "diff" in details:
                for line in details["diff"]:
                    color = "32" if line.startswith("+") else "31" if line.startswith("-") else "2"
                    self.line("    " + line, color)
                if not details["diff"]:
                    self.line("    No text changes.", "2")
            elif status == "done" and details.get("diff_note"):
                self.line("    " + details["diff_note"], "2")
            else:
                self.preview(payload.get("text", "") or "(empty result)")
