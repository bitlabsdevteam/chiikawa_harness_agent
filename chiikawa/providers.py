"""Select an explicit backend; Foundry remains the default."""

import os

from . import openrouter, provider


def select(name=None):
    """Resolve configuration without reading API keys or performing network access."""
    selected = name if name is not None else os.environ.get("CHIIKAWA_PROVIDER", "foundry")
    backends = {"foundry": provider, "openrouter": openrouter}
    if selected not in backends:
        raise ValueError("Provider must be foundry or openrouter (check --provider / CHIIKAWA_PROVIDER).")
    return backends[selected]
