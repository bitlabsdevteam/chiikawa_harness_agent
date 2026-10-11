"""Explicit, opt-in loading of provider settings from a dotenv-style file.

Nothing here runs at import or by default; the CLI calls it only for --env-file.
Only known provider variable families are accepted, variables already set in the
real environment win, and values are never returned, printed, or logged.
"""

import os
import re

ALLOWED_PREFIXES = ("AZURE_OPENAI_", "CHIIKAWA_", "OPENROUTER_", "JEVMODEL_")
LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(text):
    """Return {name: value} for KEY=VALUE lines; strip matching quotes and unquoted trailing comments."""
    values = {}
    for raw in text.splitlines():
        if raw.lstrip().startswith("#"):
            continue
        match = LINE.match(raw)
        if not match:
            continue
        name, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[name] = value
    return values


def load(path, environ=None):
    """Export allowed, nonempty settings not already set; return the sorted names loaded."""
    environ = os.environ if environ is None else environ
    with open(path, encoding="utf-8") as source:
        values = parse(source.read())
    loaded = []
    for name, value in values.items():
        if name.startswith(ALLOWED_PREFIXES) and value and not environ.get(name):
            environ[name] = value
            loaded.append(name)
    return sorted(loaded)
