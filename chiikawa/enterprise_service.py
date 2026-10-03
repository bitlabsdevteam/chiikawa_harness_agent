"""Local IT authority for Entra sessions and managed policy/accounting.

Run from a protected installation as an OS service. Project execution and model
routing are deliberately not exposed by this management protocol until their
enforced runtimes are connected. Unknown operations fail closed.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import secrets
import socket
import socketserver
import struct
import sys
import threading
import time

from .enterprise_admin import require_admin
from .enterprise_device import device_fingerprint
from .enterprise_identity import EntraAuthentication, Identity
from .enterprise_gateway import ProviderGateway
from .enterprise_policy import EnterprisePolicy, POLICY_PATH, trusted_path, _duplicates, _fields
from .enterprise_quota import QuotaLedger
from .system_policy import installation_path


SOCKET_PATH = Path("/var/run").resolve() / "chiikawa/management.sock"
STATE_PATH = Path("/var/lib").resolve() / "chiikawa"
MAX_REQUEST = 1_048_576


def peer_uid(connection):
    """Read kernel-verified credentials; never accept a UID from request JSON."""
    if platform.system() == "Linux":
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", credentials)[1]
    if platform.system() == "Darwin":
        library = ctypes.CDLL(None, use_errno=True)
        function = library.getpeereid
        function.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        function.restype = ctypes.c_int
        uid, gid = ctypes.c_uint(), ctypes.c_uint()
        if function(connection.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
            raise PermissionError("Cannot determine the OS peer identity.")
        return uid.value
    raise RuntimeError("Managed local authentication requires macOS or Linux peer credentials.")


class ManagedAuthority:
    """Re-read IT policy before each operation; sessions are bounded and memory-only."""
    def __init__(self, policy_path, state_path, *, clock=time.time):
        require_admin()
        self.policy_path, self.clock = Path(policy_path), clock
        self.device = device_fingerprint()
        self.policy = EnterprisePolicy.load(self.policy_path)
        self.authentication = EntraAuthentication(self.policy, self.device, clock=clock)
        self.identities = {}
        self.state = Path(state_path)
        self.ledger = QuotaLedger(Path(state_path) / "usage.sqlite3")
        self._lock = threading.Lock()

    def _reload(self):
        self.identities = {token: identity for token, identity in self.identities.items() if identity.expires > self.clock()}
        current = EnterprisePolicy.load(self.policy_path)
        if (current.tenant_id, current.client_id) != (self.policy.tenant_id, self.policy.client_id):
            self.identities.clear()
            self.authentication = EntraAuthentication(current, self.device, clock=self.clock)
        self.policy = current
        self.authentication.policy = current
        self.authentication.transport.policy = current
        measured = device_fingerprint()
        if measured != self.device:
            self.identities.clear()
            self.authentication.flows.clear()
            raise PermissionError("Assigned device identity changed; IT must re-enroll this machine.")

    def _identity(self, uid, token):
        identity = self.identities.get(token) if isinstance(token, str) else None
        if identity is None or identity.uid != uid:
            raise PermissionError("Microsoft Entra sign-in is required.")
        if identity.expires <= self.clock():
            self.identities.pop(token, None)
            raise PermissionError("Microsoft Entra sign-in is required.")
        developer = self.policy.authorize(identity.object_id, uid, self.device)
        return identity, developer

    def dispatch(self, uid, request, on_delta=None):
        _fields(request, ("operation", "arguments"), ("session",))
        operation, arguments = request["operation"], request["arguments"]
        if not isinstance(operation, str) or not isinstance(arguments, dict):
            raise ValueError("Invalid management request.")
        with self._lock:
            self._reload()
            if operation == "login":
                _fields(arguments, ())
                return self.authentication.begin(uid)
            if operation == "poll":
                _fields(arguments, ("flow",))
                if not isinstance(arguments["flow"], str) or len(arguments["flow"]) > 128:
                    raise ValueError("Invalid sign-in handle.")
                result = self.authentication.poll(uid, arguments["flow"])
                if isinstance(result, Identity):
                    # Policy could have changed while the network request was in
                    # flight. Revalidate before accepting the signed-in principal.
                    previous_tenant = self.policy.tenant_id, self.policy.client_id
                    self._reload()
                    if previous_tenant != (self.policy.tenant_id, self.policy.client_id):
                        raise PermissionError("IT changed the authentication authority; sign in again.")
                    self.policy.authorize(result.object_id, uid, self.device)
                    if sum(identity.uid == uid for identity in self.identities.values()) >= 16:
                        raise PermissionError("Too many active enterprise sessions; log out of an existing session first.")
                    token = secrets.token_urlsafe(32)
                    self.identities[token] = result
                    return {"authenticated": True, "expires": result.expires, "session": token}
                return result
            _, developer = self._identity(uid, request.get("session"))
            if operation == "status":
                _fields(arguments, ())
                return {"profile": "enterprise", "default_isolation": self.policy.default_isolation,
                        "sandbox_image": self.policy.sandbox_image,
                        "network": "offline" if not self.policy.destinations else "allowlist",
                        "policy_fingerprint": self.policy.fingerprint,
                        "providers": {name: {"models": list(item.models), "max_output_tokens": item.max_output_tokens,
                                             "protocol": item.protocol}
                                      for name, item in self.policy.providers.items()},
                        "quota": self.ledger.status(developer)}
            if operation == "logout":
                _fields(arguments, ())
                self.identities.pop(request["session"], None)
                return {"authenticated": False}
            if operation in {"model", "model_stream"}:
                _fields(arguments, ("provider", "model", "messages", "tools"),
                        ("max_output_tokens", "isolation", "workspace", "approval", "depth", "purpose"))
                if operation == "model_stream":
                    if on_delta is None:
                        raise ValueError("Streaming requires a response channel.")
                    return ProviderGateway(self.policy, self.ledger, self.state).complete(developer, on_delta=on_delta, **arguments)
                return ProviderGateway(self.policy, self.ledger, self.state).complete(developer, **arguments)
            if operation == "fetch":
                _fields(arguments, ("url",))
                return ProviderGateway(self.policy, self.ledger, self.state).fetch(arguments["url"])
            raise PermissionError("Operation is not available through the managed authority.")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            uid = peer_uid(self.connection)
            line = self.rfile.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                raise ValueError("Invalid or oversized management request.")
            request = json.loads(line, object_pairs_hook=_duplicates)
            if isinstance(request, dict) and request.get("operation") == "model_stream":
                def on_delta(text):
                    self.wfile.write((json.dumps({"delta": text}, allow_nan=False) + "\n").encode())
                    self.wfile.flush()
                result = self.server.authority.dispatch(uid, request, on_delta=on_delta)
            else:
                result = self.server.authority.dispatch(uid, request)
            response = {"result": result}
        except PermissionError as exc:
            response = {"error": str(exc)}
        except (OSError, RuntimeError, ValueError, KeyError, TypeError):
            # Identity/provider error responses may embed sensitive context;
            # expose no raw exception or credential-bearing payload over IPC.
            response = {"error": "Managed request denied or failed; check IT policy, enrollment, and sign-in."}
        self.wfile.write((json.dumps(response, allow_nan=False) + "\n").encode())


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def serve(authority, socket_path=SOCKET_PATH):
    require_admin()
    socket_path = Path(socket_path).absolute()
    trusted_path(socket_path.parent, directory=True)
    # Never unlink an existing listener. IT must investigate and remove stale
    # sockets after confirming that no service owns them.
    with _Server(str(socket_path), _Handler) as server:
        os.chmod(socket_path, 0o666)
        inode = socket_path.lstat().st_ino
        server.authority = authority
        try:
            server.serve_forever(poll_interval=0.25)
        finally:
            if socket_path.exists() and socket_path.lstat().st_ino == inode:
                socket_path.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--state", type=Path, default=STATE_PATH)
    parser.add_argument("--socket", type=Path, default=SOCKET_PATH)
    args = parser.parse_args(argv)
    try:
        require_admin()
        installed = installation_path()
        trusted_path(installed, directory=installed.is_dir())
        if installed.is_dir():
            for resource in (*installed.glob("*.py"), installed / "SYSTEM_PROMPT.md"):
                trusted_path(resource)
        trusted_path(args.state, directory=True, private=True)
        authority = ManagedAuthority(args.policy, args.state)
        serve(authority, args.socket)
        return 0
    except (OSError, RuntimeError, ValueError):
        print("Managed service could not start. Verify the IT-owned installation, policy, state and socket directory.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
