"""Day 5: route module execution to the CLI and preserve its process exit status.

Keep argument parsing and interactive behavior in the dedicated CLI module.
"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
