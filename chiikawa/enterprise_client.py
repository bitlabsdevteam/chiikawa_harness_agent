"""Unprivileged client of the local IT authority. Session tokens stay in memory."""

import json
import os
from pathlib import Path
import socket
import stat
import time

from .enterprise_policy import ADMIN_UID, trusted_path, _duplicates
from .enterprise_service import SOCKET_PATH, peer_uid
from . import context, enterprise_models


class EnterpriseClient:
    def __init__(self, socket_path=SOCKET_PATH):
        self.socket_path = Path(socket_path)
        self._session = None
        self.configuration = None

    def request(self, operation, arguments=None, on_delta=None):
        trusted_path(self.socket_path.parent, directory=True)
        info = self.socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != ADMIN_UID:
            raise PermissionError("Enterprise requires the local IT-owned management service.")
        request = {"operation": operation, "arguments": arguments or {}}
        if self._session is not None:
            request["session"] = self._session
        data = (json.dumps(request, allow_nan=False) + "\n").encode()
        if len(data) > 1_048_576:
            raise ValueError("Managed request exceeds the 1 MiB limit; compact the conversation.")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(300)
            connection.connect(str(self.socket_path))
            if peer_uid(connection) != ADMIN_UID:
                raise PermissionError("The management service is not running as company IT.")
            connection.sendall(data)
            with connection.makefile("rb") as stream:
                received = 0
                while True:
                    encoded = stream.readline(16_777_217)
                    received += len(encoded)
                    if len(encoded) > 16_777_216 or received > 67_108_864 or not encoded.endswith(b"\n"):
                        raise RuntimeError("Invalid, incomplete, or oversized management response.")
                    response = json.loads(encoded, object_pairs_hook=_duplicates)
                    if not isinstance(response, dict):
                        raise RuntimeError("Invalid management response.")
                    if set(response) == {"delta"} and on_delta is not None and isinstance(response["delta"], str):
                        on_delta(response["delta"])
                        continue
                    if set(response) not in ({"result"}, {"error"}):
                        raise RuntimeError("Invalid management response.")
                    if "error" in response:
                        raise PermissionError(str(response["error"]))
                    return response["result"]

    def login(self, on_code):
        flow = self.request("login")
        on_code(flow["verification_uri"], flow["user_code"])
        expires = time.monotonic() + flow["expires_in"]
        interval = flow["interval"]
        while time.monotonic() < expires:
            time.sleep(interval)
            result = self.request("poll", {"flow": flow["flow"]})
            if result.get("authenticated"):
                self._session = result["session"]
                self.refresh()
                return self
            interval = result.get("interval", interval)
        raise PermissionError("Entra sign-in expired.")

    def refresh(self):
        self.configuration = self.request("status")
        return self.configuration

    def backend(self, name=None):
        providers = self.configuration["providers"] if self.configuration else self.refresh()["providers"]
        name = name or next(iter(providers), None)
        if name not in providers:
            raise PermissionError("IT must approve a provider before model tasks can run.")
        return ManagedBackend(self, name, providers[name])


class ManagedBackend:
    MODEL_ENV = ""  # Environment variables do not set enterprise provider policy.

    def __init__(self, client, name, configuration):
        self.client, self.NAME = client, name
        self.DEFAULT_MODEL = configuration["models"][0]
        self.MAX_OUTPUT_TOKENS = configuration["max_output_tokens"]
        self.models = tuple(configuration["models"])
        self.protocol = configuration["protocol"]
        self.SUPPORTS_STREAMING = self.protocol == "responses"
        self.facts = {}

    def validate_replay(self, messages):
        enterprise_models._history(messages)
        for message in messages:
            enterprise_models._native(message, self.protocol)

    def complete(self, model, system, messages, tools, reasoning_summary=False, max_output_tokens=None, on_delta=None):
        # The service supplies the installed core system policy. Caller-supplied
        # system text is never transmitted; the only special operation is the
        # application's exact, fixed compaction instruction.
        purpose = "compaction" if system == context.SUMMARY_SYSTEM else "response"
        options = {"on_delta": on_delta} if on_delta is not None else {}
        return self.client.request("model_stream" if on_delta is not None else "model", {"provider": self.NAME, "model": model, "messages": messages,
                                             "tools": [tool["schema"]["name"] for tool in tools],
                                             "max_output_tokens": max_output_tokens, "purpose": purpose,
                                             **self.facts}, **options)
