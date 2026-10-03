"""Immutable IT policy for the managed service; no project configuration fallback.

This module validates policy, not user authentication. A service must supply an
authenticated Entra object ID, OS peer UID, and locally measured device identity.
Never obtain those assertions from model text or an untrusted request body.
"""

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
from types import MappingProxyType
from urllib.parse import urlsplit
import uuid


POLICY_PATH = Path("/etc").resolve() / "chiikawa/enterprise.json"
MAX_POLICY_BYTES = 1_048_576
ADMIN_UID = 0


def managed_policy_present():
    # Even a broken policy link keeps a managed machine locked down. A missing
    # or invalid configuration must not silently enable the standard profile.
    return POLICY_PATH.exists() or POLICY_PATH.is_symlink()


def _fields(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError("Managed policy has missing or unknown fields.")


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Entra identifiers must be canonical UUID strings.")
    return value


def _positive(value, label, zero=False):
    if type(value) is not int or value < (0 if zero else 1):
        raise ValueError(f"{label} must be a {'nonnegative' if zero else 'positive'} integer.")
    return value


def _duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate managed policy field.")
        result[key] = value
    return result


def trusted_path(path, *, directory=False, private=False):
    """Reject developer-controlled modes, macOS ACLs, links, and nonregular files.

    Deployment paths must be canonical, e.g. /private/etc on macOS. IT should
    provision them explicitly; a developer-writable ancestor is not acceptable.
    Linux POSIX ACL write permissions are bounded by the group-class mode mask.
    macOS extended ACLs are independent; reject them rather than interpreting a
    potentially complex inherited authorization policy as safe.
    """
    path = Path(path).absolute()
    if ".." in path.parts:
        raise PermissionError("Managed paths cannot contain parent traversal.")
    for component in reversed((path, *path.parents)):
        info = component.lstat()
        if (stat.S_ISLNK(info.st_mode) or info.st_uid != ADMIN_UID
                or info.st_mode & 0o022):
            raise PermissionError("Managed files and ancestors must be IT-owned and not group/world writable.")
        if platform.system() == "Darwin":
            listing = subprocess.run(["/bin/ls", "-lde", str(component)], capture_output=True,
                                     check=True, timeout=5, env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"})
            if b"+" in listing.stdout.split(None, 1)[0]:
                raise PermissionError("Managed paths must not carry macOS extended ACLs.")
        final = component == path
        if not final or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise PermissionError("Managed ancestor is not a directory.")
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PermissionError("Managed policy must be a regular, unaliased file.")
        if final and private and info.st_mode & 0o077:
            raise PermissionError("Managed private state must be accessible only to IT.")
    return path


def _url(value):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Network destinations must be explicit HTTPS URLs.")
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or "\\" in value or "%" in parsed.netloc):
        raise ValueError("Only HTTPS destinations without credentials, query, or fragment are supported.")
    host = parsed.hostname.lower()
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        # Canonical ASCII names avoid Unicode/confusable, wildcard and trailing-dot aliases.
        if (not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
                or any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                       for label in host.split(".")) or len(host) > 253):
            raise ValueError("Use a canonical ASCII hostname or literal IP address.")
    path = parsed.path or "/"
    if "%" in path or any(part in (".", "..") for part in path.split("/")) or "//" in path:
        raise ValueError("Destination paths must be unescaped and canonical.")
    port = 443 if parsed.port is None else parsed.port
    if not 1 <= port <= 65535:
        raise ValueError("Invalid destination port.")
    return host, port, path


@dataclass(frozen=True)
class Destination:
    """An exact origin/path, a path subtree ending '/', or literal IP:443."""
    host: str
    port: int
    path: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str):
            raise ValueError("Destination must be a URL or literal IP string.")
        if "://" not in value:
            return cls(str(ipaddress.ip_address(value)), 443, "/")
        return cls(*_url(value))

    def allows(self, url):
        host, port, path = _url(url)
        return ((host, port) == (self.host, self.port)
                and (path == self.path or self.path.endswith("/") and path.startswith(self.path)))


@dataclass(frozen=True)
class Developer:
    object_id: str
    uid: int
    device: str
    enabled: bool
    token_limit: object = None
    grant: str = "initial"


@dataclass(frozen=True)
class Provider:
    name: str
    protocol: str
    endpoint: str
    models: tuple
    credential: str
    max_output_tokens: int
    count_endpoint: object = None
    auth: str = "bearer"


@dataclass(frozen=True)
class EnterprisePolicy:
    tenant_id: str
    client_id: str
    developers: object
    providers: object
    destinations: tuple
    fingerprint: str
    default_isolation: str = "jail"
    sandbox_image: object = None

    @classmethod
    def parse(cls, raw):
        """Validate all fields. Parsing alone never authenticates their source."""
        _fields(raw, ("version", "tenant_id", "client_id", "developers", "providers", "network"), ("sandbox_image",))
        sandbox_image = raw.get("sandbox_image")
        if sandbox_image is not None and (not isinstance(sandbox_image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", sandbox_image)):
            raise ValueError("IT-approved Sandbox images must be pinned local sha256 image IDs.")
        if type(raw["version"]) is not int or raw["version"] != 1:
            raise ValueError("Unsupported managed policy version.")
        tenant, client = _uuid(raw["tenant_id"]), _uuid(raw["client_id"])
        if not isinstance(raw["developers"], dict) or not isinstance(raw["providers"], dict):
            raise ValueError("Developers and providers must be objects.")
        _fields(raw["network"], ("allow",))
        if not isinstance(raw["network"]["allow"], list):
            raise ValueError("Network allowlist must be a list; an empty list means offline.")
        destinations = tuple(Destination.parse(item) for item in raw["network"]["allow"])
        developers, uids, devices = {}, set(), set()
        for oid, item in raw["developers"].items():
            _uuid(oid)
            _fields(item, ("uid", "device", "enabled"), ("token_limit", "grant"))
            uid = _positive(item["uid"], "Developer UID")
            device = item["device"]
            if not isinstance(device, str) or not re.fullmatch(r"[0-9a-f]{64}", device):
                raise ValueError("Device enrollment requires a SHA-256 fingerprint.")
            if uid in uids or device in devices:
                raise ValueError("Each developer must have one distinct OS identity and assigned machine.")
            if type(item["enabled"]) is not bool:
                raise ValueError("Developer enabled must be boolean.")
            limit = item.get("token_limit")
            if limit is not None:
                _positive(limit, "Token limit", zero=True)
            grant = item.get("grant", "initial")
            if not isinstance(grant, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", grant):
                raise ValueError("Quota grant must be an explicit stable identifier.")
            developers[oid] = Developer(oid, uid, device, item["enabled"], limit, grant)
            uids.add(uid)
            devices.add(device)
        providers = {}
        for name, item in raw["providers"].items():
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
                raise ValueError("Invalid managed provider name.")
            _fields(item, ("protocol", "endpoint", "models", "credential", "max_output_tokens"), ("count_endpoint", "auth"))
            if item["protocol"] not in {"responses", "chat-completions", "anthropic", "google"}:
                raise ValueError("Unsupported managed provider protocol.")
            _url(item["endpoint"])
            if item.get("count_endpoint") is not None:
                _url(item["count_endpoint"])
            models = item["models"]
            if (not isinstance(models, list) or not models or any(
                    not isinstance(m, str) or not m or "*" in m or any(c.isspace() for c in m) for m in models)):
                raise ValueError("Providers need an explicit nonempty model allowlist.")
            credential = item["credential"]
            if not isinstance(credential, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", credential):
                raise ValueError("Credential must name a managed secret, not a path or key value.")
            auth = item.get("auth", {"anthropic": "x-api-key", "google": "x-goog-api-key"}.get(item["protocol"], "bearer"))
            if auth not in {"bearer", "api-key", "x-api-key", "x-goog-api-key"}:
                raise ValueError("Unsupported provider authentication header.")
            providers[name] = Provider(name, item["protocol"], item["endpoint"], tuple(models), credential,
                                       _positive(item["max_output_tokens"], "Provider output limit"), item.get("count_endpoint"), auth)
        fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":"),
                                                allow_nan=False).encode()).hexdigest()
        return cls(tenant, client, MappingProxyType(developers), MappingProxyType(providers),
                   destinations, fingerprint, sandbox_image=sandbox_image)

    @classmethod
    def load(cls, path=POLICY_PATH):
        path = trusted_path(path)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if info.st_uid != ADMIN_UID or info.st_mode & 0o022 or info.st_nlink != 1:
                raise PermissionError("Managed policy changed or is not IT-owned.")
            encoded = source.read(MAX_POLICY_BYTES + 1)
        if len(encoded) > MAX_POLICY_BYTES:
            raise ValueError("Managed policy exceeds size limit.")
        return cls.parse(json.loads(encoded, object_pairs_hook=_duplicates))

    def authorize(self, object_id, uid, device):
        developer = self.developers.get(object_id)
        if (developer is None or not developer.enabled or developer.uid != uid
                or developer.device != device):
            raise PermissionError("Developer is unassigned, disabled, or using an unapproved identity/device.")
        return developer

    def allow_url(self, url):
        if not any(rule.allows(url) for rule in self.destinations):
            raise PermissionError("Destination is not approved by company IT; offline is the default.")

    def allow_model(self, provider, model):
        selected = self.providers.get(provider)
        if selected is None or model not in selected.models:
            raise PermissionError("Provider/model is not approved by company IT.")
        url = selected.endpoint
        if "{model}" in url:
            if not re.fullmatch(r"[A-Za-z0-9._-]+", model):
                raise ValueError("URL model placeholders require a simple model identifier.")
            url = url.replace("{model}", model)
        self.allow_url(url)
        return selected
