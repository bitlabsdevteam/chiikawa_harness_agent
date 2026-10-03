"""Compact public activity, expandable history, and a two-line working footer.

The normal screen is append-only scrollback plus one replaceable activity block.
Ctrl+T opens an alternate-screen public transcript; it never exposes provider
replay fields. Only the run context owns terminal input, and releases it before
approval prompts. No provider, tool, or journal behavior lives in this module.
"""

from contextlib import contextmanager
import os
import select
import signal
import sys
import threading
import time

from .prompt import clip, width, enter_cbreak
from .terminal import TerminalDisplay, safe_text, activity_text


READ_TOOLS = {"read_file", "list_files", "grep"}


def wrapped(text, columns):
    """Wrap terminal-safe text by display cells, including CJK and combining marks."""
    rows = []
    for line in safe_text(text).expandtabs(4).split("\n"):
        row, used = "", 0
        for char in line:
            cells = width(char)
            if row and used + cells > columns:
                rows.append(row)
                row, used = "", 0
            row += char
            used += cells
        rows.append(row)
    return rows


class TranscriptDisplay(TerminalDisplay):
    """Keep readable scrollback and inspectable public history without dependencies."""

    def __init__(self, stream=None, answer_stream=None):
        super().__init__(stream, answer_stream)
        self.tty = self.tty and sys.stdin.isatty() and self.answer_stream.isatty()
        self.entries = []
        self.active = None
        self.painted = 0
        self.last_frame = None
        self.running = False
        self.viewing = False
        self.offset = 0
        self.fd = None
        self.saved = None
        self.pasting = False
        self.deferred = []
        self.metrics = {}
        self.total_input = self.total_output = 0
        self.usage_seen = False
        self.missing_usage = False

    def size(self):
        try:
            columns, lines = os.get_terminal_size(self.stream.fileno())
        except (AttributeError, OSError, ValueError):
            columns, lines = 88, 24
        return max(4, columns - 1), max(4, lines)

    def _erase(self):
        self.last_frame = None
        if self.painted:
            self.stream.write(f"\r\033[{self.painted}A\033[J")
            self.painted = 0

    def _print_rows(self, rows):
        for text, code in rows:
            if code and code.startswith("thinking:"):
                rendered = self.thinking(text, int(code.partition(":")[2]))
            elif text.startswith("• ") and code in ("32", "31"):
                action, separator, rest = text[2:].partition(" ")
                rendered = self.style("•", code) + " " + self.style(action, "1") + separator + rest
            elif text.startswith("  └ ") and code == "36":
                action, separator, rest = text[4:].partition(" ")
                rendered = self.style("  └ ", "2") + self.style(action, "36") + separator + rest
            else:
                rendered = self.style(text, code) if code else text
            self.stream.write(rendered + "\n")
        self.stream.flush()

    def line(self, text="", code=None):
        with self.lock:
            self._erase()
            rows = [(row, code) for row in wrapped(text, self.size()[0])]
            if not self.viewing:
                self._print_rows(rows)
            self._paint()

    @staticmethod
    def title(call, status="done"):
        args, name = call.get("args", {}), call.get("name", "tool")
        if name == "bash":
            verb = "Running" if status == "running" else "Ran"
            return f"{verb} {args.get('command', '')}"
        return TerminalDisplay.describe(call)

    def _tool_rows(self, entry, expanded, columns, nested=False):
        call, status = entry["call"], entry["status"]
        code = "31" if status in ("error", "blocked", "interrupted") else "2" if status == "recorded" else "32"
        title = self.title(call, status)
        if status in ("error", "blocked", "interrupted"):
            title = {"error": "Failed", "blocked": "Blocked", "interrupted": "Interrupted"}[status] + ": " + title
        details = entry.get("details", {})
        if "exit_code" in details and details["exit_code"]:
            title += f" · exit {details['exit_code']}"
        if "added" in details and status == "done":
            title += f" · +{details['added']} -{details['removed']}"
        if status == "recorded" and call["name"] == "bash":
            title += " (exit status not recorded)"
        prefix = "  └ " if nested else "• "
        single_line = safe_text(title).expandtabs(4).replace("\n", " ")
        title_rows = wrapped(title, columns - len(prefix)) if expanded else [clip(single_line, columns - len(prefix))]
        rows = [((prefix if index == 0 else " " * len(prefix)) + row,
                 "36" if nested and status == "done" else code) for index, row in enumerate(title_rows)]
        if not expanded and width(single_line) > columns - len(prefix):
            rows.append(("    + full command/details (ctrl+t)", "2"))
        if status == "running":
            return rows
        if status == "done" and "diff" in details:
            output = "\n".join(details["diff"]) or "No text changes."
        else:
            output = entry.get("text", "").rstrip("\n") or "(no output)"
        body = wrapped(output, max(1, columns - 4))
        shown = body if expanded else body[:3]
        for index, row in enumerate(shown):
            color = "2"
            if status == "done" and "diff" in details:
                color = "32" if row.startswith("+") else "31" if row.startswith("-") else "2"
            rows.append((("  └ " if index == 0 and not nested else "    ") + row, color))
        if len(body) > len(shown):
            rows.append((f"    + {len(body) - len(shown)} lines (ctrl+t to expand)", "2"))
        return rows

    def rows(self, entries, expanded=False):
        columns, _ = self.size()
        rows = []
        for entry in entries:
            kind = entry["kind"]
            if kind == "explore":
                children = entry["children"]
                busy = any(child["status"] == "running" for child in children)
                rows.append(("• " + ("Exploring" if busy else "Explored"), "1"))
                shown = children if expanded else children[-3:]
                for child in shown:
                    if expanded or child["status"] not in ("done", "recorded"):
                        rows.extend(self._tool_rows(child, expanded, columns, nested=True))
                    else:
                        rows.append(("  └ " + clip(safe_text(self.title(child["call"])).expandtabs(4).replace("\n", " "), columns - 4), "36"))
                rows.append(("    " + (f"+ {len(children) - len(shown)} more · " if len(children) > len(shown) else "") +
                             ("ctrl+t to collapse" if expanded else "+ Show details (ctrl+t)"), "2"))
            elif kind == "tool":
                rows.extend(self._tool_rows(entry, expanded, columns))
            else:
                prefix = "› " if kind == "user" else "• "
                body = wrapped(entry.get("text", ""), columns - 2)
                shown = body[:3] if kind == "reasoning" and not expanded else body
                code = "2" if kind == "reasoning" else "1" if kind == "user" else None
                rows.extend(((prefix if index == 0 else "  ") + row, code) for index, row in enumerate(shown))
                if len(body) > len(shown):
                    rows.append((f"  + {len(body) - len(shown)} lines (ctrl+t to expand)", "2"))
            rows.append(("", None))
        return rows

    def _footer(self):
        elapsed = max(0, int(time.monotonic() - (self.started or time.monotonic())))
        duration = f"{elapsed // 60}m {elapsed % 60:02d}s" if elapsed >= 60 else f"{elapsed}s"
        activity = safe_text(self.activity).expandtabs(4).replace("\n", " ")
        code = f"thinking:{self.thinking_frame()}" if self.activity == "Thinking..." and self.tty and self.color else "2"
        return [(f"• Working... ({duration} · esc to interrupt)", "2"),
                (f"  {activity} · ctrl+t history · /status for usage", code)]

    def _paint(self):
        if not self.tty:
            if self.running:
                self._print_rows([(self._working_text(), "2")])
            return
        columns, height = self.size()
        if self.viewing:
            rows = self.rows(self.entries, expanded=True)
            rows.extend(self._stream_rows(columns))
            header = [("Transcript · public messages and tool output", "1")]
            if self.running:
                status = self._footer()
                # In the viewer Esc returns to work; it does not interrupt.
                header.extend([(status[0][0].replace(" · esc to interrupt", ""), "2"), status[1]])
            else:
                header.append(("", None))
            # Leave the final row unused: a newline there scrolls the title away.
            available = max(1, height - len(header) - 2)
            self.offset = min(self.offset, max(0, len(rows) - available))
            end = max(0, len(rows) - self.offset)
            visible = rows[max(0, end - available):end]
            footer = [("↑/↓ scroll · PgUp/PgDn · Home/End · ctrl+t / esc back", "2")]
            frame = [(clip(text, columns), code) for text, code in header + visible + footer]
            if self.last_frame == (columns, height, frame):
                return
            self.stream.write("\033[H\033[2J")
            self._print_rows(frame)
            self.last_frame = (columns, height, frame)
            return
        rows = self.rows([self.active]) if self.active is not None else []
        rows.extend(self._stream_rows(columns))
        if self.running:
            rows.extend(self._footer())
        # Never address rows that have scrolled off-screen, including on resize.
        if len(rows) > height - 2:
            rows = [("  … earlier activity in ctrl+t history", "2")] + rows[-(height - 3):]
        rows = [(clip(text, columns), code) for text, code in rows]
        if self.last_frame == (columns, height, rows):
            return
        self._erase()
        self._print_rows(rows)
        self.painted = len(rows)
        self.last_frame = (columns, height, rows)

    def _stream_rows(self, columns):
        if not self.stream_text:
            return []
        return [("Streaming response", "2"), *[(row, None) for row in wrapped(self.stream_text, columns)[-6:]]]

    def _stream_delta(self, text):
        if not self.tty:
            return super()._stream_delta(text)
        with self.lock:
            self.stream_text = (self.stream_text + text)[-12000:]
            self.activity = "Receiving response"
            self._paint()

    def _flush_active(self):
        self._erase()
        if self.active is not None:
            self._emit(self.rows([self.active]))
        self.active = None

    def _emit(self, rows, answer=None):
        if self.viewing:
            self.deferred.append((rows, answer))
        elif answer is not None:
            print(safe_text(answer), file=self.answer_stream, flush=True)
        else:
            self._print_rows(rows)

    def _leave_view(self):
        self.stream.write("\033[?1049l")
        self.last_frame = None
        self.viewing = False
        for rows, answer in self.deferred:
            self._emit(rows, answer)
        self.deferred.clear()

    def _record_text(self, kind, text, answer=False):
        self._flush_active()
        entry = {"kind": kind, "text": text}
        self.entries.append(entry)
        self._emit(self.rows([entry]), text if answer else None)

    def __call__(self, kind, payload):
        if kind == "assistant_delta":
            self._stream_delta(payload["text"])
            return
        if kind == "model_end":
            self._finish_stream(payload.get("ok", True))
        # Hand input back before publishing the top-level answer. Otherwise a
        # fast follow-up can be consumed by the working-view keyboard reader.
        if kind == "assistant" and not payload.get("tool_calls") and not self.pending:
            self._stop_input()
            self.running = False
        with self.lock:
            if self.running:
                self.activity = activity_text(kind, payload) or self.activity
            if kind == "context":
                self.metrics["context"] = dict(payload)
            elif kind == "usage":
                self.metrics["usage"] = dict(payload)
                incoming, outgoing = payload.get("input"), payload.get("output")
                self.usage_seen = True
                self.missing_usage |= not isinstance(incoming, int) or not isinstance(outgoing, int)
                self.total_input += incoming if isinstance(incoming, int) else 0
                self.total_output += outgoing if isinstance(outgoing, int) else 0
            elif kind == "compaction_start":
                self.wait_label = "Compacting context"
            elif kind in ("compaction", "model_start"):
                self.wait_label = "Working"
            elif kind == "reasoning" and payload.get("text"):
                self._record_text("reasoning", payload["text"])
            elif kind == "assistant" and payload.get("text"):
                self._record_text("assistant", payload["text"], answer=not payload.get("tool_calls"))
            elif kind == "tool_start":
                # Only presentation fields are retained; never copy provider replay objects.
                call = {"name": payload.get("name", "tool"), "args": dict(payload.get("args", {}))}
                entry = {"kind": "tool", "call": call, "status": "running"}
                if call["name"] in READ_TOOLS:
                    if self.active is None or self.active["kind"] != "explore":
                        self._flush_active()
                        self.active = {"kind": "explore", "children": []}
                        self.entries.append(self.active)
                    self.active["children"].append(entry)
                else:
                    self._flush_active()
                    self.active = entry
                    self.entries.append(entry)
                self.pending.setdefault(payload.get("call_id", payload.get("name")), []).append(entry)
            elif kind == "tool_end":
                key = payload.get("call_id", payload.get("name"))
                stack = self.pending.get(key, [])
                entry = stack.pop() if stack else None
                if not stack:
                    self.pending.pop(key, None)
                if entry is not None:
                    entry.update(text=payload.get("text", ""), status=payload.get("status", "done"),
                                 details=payload.get("details", {}))
            self._paint()

    def status(self):
        if not self.usage_seen:
            return "API token usage: not reported in this CLI session."
        qualifier = " (partial; some usage was not reported)" if self.missing_usage else ""
        return (f"API tokens in this CLI session: input {self.total_input:,} · "
                f"output {self.total_output:,}{qualifier}")

    def restore(self, messages):
        """Build public history from validated session messages without emitting events."""
        self.entries, self.pending, self.active = [], {}, None
        for message in messages:
            role = message.get("role")
            if role in ("user", "assistant"):
                if role == "assistant" and message.get("reasoning_summary"):
                    self.entries.append({"kind": "reasoning", "text": message["reasoning_summary"]})
                if message.get("text"):
                    self.entries.append({"kind": role, "text": message["text"]})
                for call in message.get("tool_calls", []):
                    entry = {"kind": "tool", "call": {"name": call["name"], "args": call.get("args", {})},
                             "status": "interrupted"}
                    if call["name"] in READ_TOOLS:
                        if not self.entries or self.entries[-1]["kind"] != "explore":
                            self.entries.append({"kind": "explore", "children": []})
                        self.entries[-1]["children"].append(entry)
                    else:
                        self.entries.append(entry)
                    self.pending.setdefault(call.get("call_id", call["name"]), []).append(entry)
            elif role == "tool":
                stack = self.pending.get(message.get("call_id", message.get("name")), [])
                entry = stack.pop() if stack else None
                if entry is not None:
                    text = message.get("text", "")
                    status = message.get("status") or ("blocked" if text.startswith("BLOCKED:") else "error" if text.startswith("ERROR:")
                              else "interrupted" if text.startswith("Interrupted before") else "recorded")
                    entry.update(text=text, status=status, details=message.get("details", {}))
        self.pending.clear()

    def resume_preview(self):
        self.line("Resumed conversation · ctrl+t or /history for the full public transcript", "2")
        self._print_rows(self.rows(self.entries[-6:]))

    def _toggle_view(self):
        if self.viewing:
            self._leave_view()
        else:
            self._erase()
            self.stream.write("\033[?1049h")
            self.viewing, self.offset = True, 0
        self._paint()
        self.stream.flush()

    def _read_key(self):
        if self.fd is None or not select.select([self.fd], [], [], 0)[0]:
            return None
        raw = os.read(self.fd, 1)
        if raw != b"\x1b":
            return raw
        sequence = raw
        while select.select([self.fd], [], [], .03)[0]:
            sequence += os.read(self.fd, 1)
            if len(sequence) > 2 and 64 <= sequence[-1] <= 126:
                break
            if len(sequence) > 20:
                break
        return sequence

    def _key(self, key):
        if key == b"\x1b[200~":
            self.pasting = True
        elif key == b"\x1b[201~":
            self.pasting = False
        elif self.pasting:
            return
        elif key == b"\x14" or (key in (b"\x1b", b"q") and self.viewing):
            with self.lock:
                self._toggle_view()
        elif self.viewing:
            with self.lock:
                page = max(1, self.size()[1] - (5 if self.running else 4))
                steps = {b"\x1b[A": 1, b"\x1b[B": -1, b"\x1b[5~": page, b"\x1b[6~": -page}
                if key in (b"\x1b[H", b"\x1b[1~"):
                    self.offset = len(self.rows(self.entries, expanded=True))
                elif key in (b"\x1b[F", b"\x1b[4~"):
                    self.offset = 0
                else:
                    self.offset = max(0, self.offset + steps.get(key, 0))
                self._paint()
        elif key == b"\x1b" and self.running:
            os.kill(os.getpid(), signal.SIGINT)

    def _animate(self):
        while not self.stopped.wait(.1):
            key = self._read_key()
            if key is not None:
                self._key(key)
            with self.lock:
                self._paint()

    def _start_input(self):
        if not self.tty or not sys.stdin.isatty():
            return
        import termios
        self.fd = sys.stdin.fileno()
        self.saved = termios.tcgetattr(self.fd)
        enter_cbreak(self.fd)
        self.stream.write("\033[?2004h\033[?25l")
        self.stopped.clear()
        self.worker = threading.Thread(target=self._animate, daemon=True)
        self.worker.start()

    def _stop_input(self):
        self.stopped.set()
        if self.worker is not None:
            self.worker.join()
            self.worker = None
        with self.lock:
            if self.viewing:
                self._leave_view()
            self._erase()
            if self.saved is not None:
                import termios
                termios.tcsetattr(self.fd, termios.TCSANOW, self.saved)
                self.saved, self.fd = None, None
                self.stream.write("\033[?2004l\033[?25h")
            self.stream.flush()

    @contextmanager
    def task(self, text):
        self.entries.append({"kind": "user", "text": text})
        self.started = time.monotonic()
        self.running = True
        self.activity = "Starting task"
        try:
            self._start_input()
            self._paint()
            yield
        finally:
            self.close()

    @contextmanager
    def approval(self):
        self._stop_input()
        if self.active is not None:
            self._print_rows(self.rows([self.active]))
        previous = self.activity
        self.activity = "Waiting for approval"
        self._print_rows([(self._working_text(), "2")])
        try:
            yield
        finally:
            self.activity = previous
            if self.running:
                self._start_input()
                self._paint()

    def show_history(self):
        if not self.entries:
            self.line("No conversation history yet.", "2")
            return
        if not self.tty or not sys.stdin.isatty():
            self._print_rows(self.rows(self.entries, expanded=True))
            return
        import termios
        fd = sys.stdin.fileno()
        saved, previous_fd = termios.tcgetattr(fd), self.fd
        try:
            self.fd = fd
            enter_cbreak(fd)
            self._toggle_view()
            while self.viewing:
                if select.select([fd], [], [], .1)[0]:
                    self._key(self._read_key())
                else:
                    self._paint()
        finally:
            if self.viewing:
                self._toggle_view()
            termios.tcsetattr(fd, termios.TCSANOW, saved)
            self.fd = previous_fd

    def close(self):
        self._stop_input()
        with self.lock:
            for stack in self.pending.values():
                for entry in stack:
                    entry.update(status="interrupted", text="Interrupted; inspect actual state before retrying.")
            self.pending.clear()
            self.running = False
            self._finish_stream()
            self._flush_active()
            self.started = None
