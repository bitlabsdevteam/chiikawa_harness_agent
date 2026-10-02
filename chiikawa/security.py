"""Day 2: explicit approval policy, with command denials before every mode.

Regex checks provide a small teaching guardrail, not a shell sandbox. Read-only
and safe modes restrict tool dispatch; yolo still honors the command denylist.
"""

import re

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
        if mode not in {"read-only", "safe", "yolo"}:
            raise ValueError(f"Unknown policy mode: {mode}")
        self.mode = mode
        self.approver = approver if approver is not None else lambda call, reason: False

    def check(self, call):
        """Return None to allow or an explanatory reason string to block.

        Denials precede mode checks, so even yolo and an approving callback
        cannot override a dangerous-command match. Only literal True approves.
        Read tools never invoke the approver. Other safe-mode calls require a
        fresh callback decision; this policy does not cache earlier approvals.
        """
        name = call["name"]
        if name == "bash":
            command = call.get("args", {}).get("command", "")
            if any(re.search(pattern, command) for pattern in DENY_PATTERNS):
                return "dangerous bash command denied by policy"
        if name in READ_TOOLS or self.mode == "yolo":
            return None
        if self.mode == "read-only":
            return "read-only mode permits only read_file, list_files, and grep"
        reason = f"safe mode requires approval for {name}"
        if self.approver(call, reason) is True:
            return None
        return reason
