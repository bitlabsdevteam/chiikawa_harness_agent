"""Day 2: explicit approval policy, with command denials before every mode.

Regex checks provide a small teaching guardrail, not a shell sandbox. Read-only
and safe modes restrict tool dispatch; yolo still honors the command denylist.
"""

import re
from enum import Enum
from threading import RLock


class Approval(Enum):
    """Explicit callback decision; ordinary truthy values never grant access."""

    ALL = "all"

READ_TOOLS = {"read_file", "list_files", "grep"}
DENY_PATTERNS = [
    # Both recursive and force flags, in either order or long-option spelling.
    r"\brm\b(?=[^;\n|&]*(?:-[a-zA-Z]*[rR]|--recursive))"
    r"(?=[^;\n|&]*(?:-[a-zA-Z]*f|--force))[^;\n|&]*\s[\"']?"
    r"(?:/+(?:\./)*(?:\*|\.)?|~[^\s;|&]*|\$(?:HOME|\{HOME\})[^\s;|&]*)"
    r"[\"']?(?=\s|[;|&]|$)",
    r"\bsudo\b",
    r"\bmkfs(?:\.[\w-]+)?\b",
    r"\bdd\b[^;\n|&]*\bif\s*=",
    r"\bcurl\b[^;\n]*\|\s*(?:(?:/usr)?/bin/)?(?:ba|da|z|k)?sh\b",
    r"\bgit\b[^;\n|&]*\bpush\b[^;\n|&]*(?:--force\b|-[a-zA-Z]*f\b)",
    r">{1,2}\s*[\"']?/dev/sd[a-z][a-z0-9]*\b",
]


class Policy:
    """Decide whether a neutral tool call may execute, without executing it."""

    def __init__(self, mode="safe", approver=None):
        """Choose a known mode and an optional (call, reason) approval callback."""
        self._lock = RLock()
        self.mode = mode
        self.approver = approver if approver is not None else lambda call, reason: False

    @property
    def mode(self):
        with self._lock:
            return self._mode

    @mode.setter
    def mode(self, value):
        if value not in {"ask", "read-only", "safe", "yolo"}:
            raise ValueError(f"Unknown policy mode: {value}")
        with self._lock:
            self._mode = value

    def check(self, call):
        """Return None to allow or an explanatory reason string to block.

        Denials precede mode checks, so even yolo and an approving callback
        cannot override a dangerous-command match. True permits one call;
        Approval.ALL selects yolo for this shared Policy's lifetime, until revoked
        by assigning another mode. No grant is saved to disk. Serialize decisions
        so concurrent callers sharing a policy never display overlapping prompts.
        """
        with self._lock:
            return self._check(call)

    def _check(self, call):
        name = call["name"]
        if name == "bash":
            command = call.get("args", {}).get("command", "")
            if any(re.search(pattern, command) for pattern in DENY_PATTERNS):
                return "dangerous bash command denied by policy"
        if self.mode == "yolo" or (name in READ_TOOLS and self.mode != "ask"):
            return None
        if self.mode == "read-only":
            return "read-only mode permits only read_file, list_files, and grep"
        reason = f"{self.mode} mode requires approval for {name}"
        decision = self.approver(call, reason)
        if decision is Approval.ALL:
            self.mode = "yolo"
            return None
        if decision is True:
            return None
        return reason
