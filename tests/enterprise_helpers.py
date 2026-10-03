"""Configuration-only doubles; real OS, IPC, transport, and quota tests are separate."""

from pathlib import Path
from types import SimpleNamespace

from chiikawa import provider, openrouter
from chiikawa.runtime import JailRuntime


class FakeNative(JailRuntime):
    network = "deny"

    def __init__(self, root, uid, gid):
        super().__init__(root)


class FakeSandbox(JailRuntime):
    def __init__(self, root, image, network):
        super().__init__(root)
        self.network = network


class FakeEnterprise:
    def __init__(self, image="sha256:" + "a" * 64):
        self.configuration = {"profile": "enterprise", "default_isolation": "jail", "network": "offline",
                              "sandbox_image": image, "quota": {"limit": None, "charged": 0},
                              "providers": {item.NAME: {"models": [item.DEFAULT_MODEL, "next"],
                                                        "max_output_tokens": item.MAX_OUTPUT_TOKENS}
                                            for item in (provider, openrouter)}}

    def refresh(self):
        return self.configuration

    def login(self, on_code):
        return self

    def backend(self, name=None):
        module = {"foundry": provider, "openrouter": openrouter}[name or "foundry"]
        return SimpleNamespace(NAME=module.NAME, MODEL_ENV="", DEFAULT_MODEL=module.DEFAULT_MODEL,
                               MAX_OUTPUT_TOKENS=module.MAX_OUTPUT_TOKENS,
                               models=(module.DEFAULT_MODEL, "next"), facts={},
                               validate_replay=module.validate_replay,
                               complete=lambda *args, **kwargs: module.complete(*args, **kwargs))

    def request(self, operation, arguments=None):
        raise PermissionError("No destination is approved in this test policy.")
