"""Live terminal activity with bounded previews and a separate answer stream."""

import os
import math
import random
import shutil
import sys
import textwrap
import threading
import time


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
                print(f"\r\033[2K{text}",
                      end="", file=self.stream, flush=True)
            index += 1

    def close(self):
        self.stopped.set()
        if self.worker is not None:
            self.worker.join()
            self.worker = None
            with self.lock:
                print("\r\033[2K", end="", file=self.stream, flush=True)

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
            self.close()
            if self.started is not None:
                label = "Model response received" if payload.get("ok", True) else "Model request stopped"
                self.line(f"{label} ({time.monotonic() - self.started:.1f}s)", "2")
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
