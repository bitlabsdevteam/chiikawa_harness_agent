"""Microsoft Entra device-code authentication owned by the local IT service.

The browser sign-in authenticates an enrolled OS peer. No caller-supplied JWT,
email address, or role claim is accepted as a local login. Tokens are received
directly from the tenant's approved HTTPS endpoint and validated cryptographically.
Cryptography is an optional enterprise dependency; standard usage never imports it.
"""

import base64
from dataclasses import dataclass
import json
import secrets
import time
from urllib.parse import urlencode

from .enterprise_policy import _duplicates
from .enterprise_transport import ApprovedHTTPS


def _decode(value):
    if not isinstance(value, str) or len(value) > 131072:
        raise ValueError("Invalid Entra token encoding.")
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def validate_id_token(token, keys, tenant, client, now):
    """Validate signature, issuer, audience, tenant, object ID, and time claims."""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
    except ImportError as exc:
        raise RuntimeError("IT must install chiikawa-harness[enterprise] for Entra authentication.") from exc
    try:
        if not isinstance(token, str) or len(token) > 131072:
            raise ValueError("Invalid token.")
        header_text, payload_text, signature_text = token.split(".")
        header = json.loads(_decode(header_text), object_pairs_hook=_duplicates)
        if (not isinstance(header, dict) or header.get("alg") != "RS256" or not header.get("kid")
                or any(name in header for name in ("jku", "jwk", "x5u", "crit"))):
            raise ValueError("Unsupported token header.")
        matching = [key for key in keys["keys"] if key.get("kid") == header["kid"]
                    and key.get("kty") == "RSA" and key.get("use", "sig") == "sig"
                    and key.get("alg", "RS256") == "RS256"]
        if len(matching) != 1:
            raise ValueError("Missing or ambiguous signing key.")
        key = matching[0]
        if key.get("issuer", f"https://login.microsoftonline.com/{tenant}/v2.0").replace("{tenantid}", tenant) != \
                f"https://login.microsoftonline.com/{tenant}/v2.0":
            raise ValueError("Wrong signing-key issuer.")
        modulus = int.from_bytes(_decode(key["n"]), "big")
        exponent = int.from_bytes(_decode(key["e"]), "big")
        if modulus.bit_length() < 2048 or exponent != 65537:
            raise ValueError("Unsupported signing key.")
        public_key = rsa.RSAPublicNumbers(exponent, modulus).public_key()
        public_key.verify(_decode(signature_text), f"{header_text}.{payload_text}".encode("ascii"),
                          padding.PKCS1v15(), hashes.SHA256())
        claims = json.loads(_decode(payload_text), object_pairs_hook=_duplicates)
        if (not isinstance(claims, dict) or claims.get("aud") != client or claims.get("tid") != tenant
                or claims.get("iss") != f"https://login.microsoftonline.com/{tenant}/v2.0"
                or not isinstance(claims.get("oid"), str)):
            raise ValueError("Wrong token identity, tenant, issuer, or audience.")
        for field in ("exp", "iat", "nbf"):
            if type(claims.get(field)) is not int:
                raise ValueError("Invalid token time.")
        if claims["exp"] <= now or claims["iat"] > now + 60 or claims["nbf"] > now + 60:
            raise ValueError("Expired or not-yet-valid token.")
        return claims
    except Exception as exc:
        # Tokens, authorization codes, and claim payloads never enter diagnostics.
        raise PermissionError("Entra ID token validation failed.") from exc


@dataclass
class _Flow:
    uid: int
    object_id: str
    device_code: str
    expires: float
    interval: int
    next_poll: float


@dataclass(frozen=True)
class Identity:
    object_id: str
    uid: int
    device: str
    expires: float


class EntraAuthentication:
    """Service-thread-owned flow state. Callers supply OS-authenticated peer UIDs."""
    def __init__(self, policy, device, *, clock=time.time, transport=None):
        self.policy, self.device, self.clock = policy, device, clock
        self.transport = transport or ApprovedHTTPS(policy)
        self.flows = {}
        self.authority = f"https://login.microsoftonline.com/{policy.tenant_id}"

    def _post(self, suffix, payload):
        raw = self.transport.request("POST", self.authority + suffix, urlencode(payload).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                                     accepted_status=(200, 400))
        response = json.loads(raw)
        if not isinstance(response, dict):
            raise RuntimeError("Invalid Entra response.")
        return response

    def begin(self, uid):
        candidates = [d for d in self.policy.developers.values() if d.uid == uid]
        if len(candidates) != 1:
            raise PermissionError("OS account has not been enrolled by IT.")
        developer = self.policy.authorize(candidates[0].object_id, uid, self.device)
        now = self.clock()
        self.flows = {key: value for key, value in self.flows.items() if value.expires > now and value.uid != uid}
        response = self._post("/oauth2/v2.0/devicecode", {"client_id": self.policy.client_id, "scope": "openid profile"})
        if response.get("error"):
            raise PermissionError("Entra device sign-in could not be started; contact company IT.")
        code, display_code = response.get("device_code"), response.get("user_code")
        interval, expires = response.get("interval", 5), response.get("expires_in")
        # Display only Microsoft's documented device-code destination. The service
        # never opens a provider-supplied arbitrary browser URL or prints its message.
        verification = response.get("verification_uri")
        if (not isinstance(code, str) or not code or not isinstance(display_code, str) or not display_code
                or len(display_code) > 32 or not all(c.isalnum() or c == "-" for c in display_code)
                or type(interval) is not int or not 1 <= interval <= 60
                or type(expires) is not int or not 1 <= expires <= 1800
                or verification not in {"https://microsoft.com/devicelogin", "https://www.microsoft.com/devicelogin"}):
            raise RuntimeError("Invalid Entra device sign-in response.")
        handle = secrets.token_urlsafe(32)
        self.flows[handle] = _Flow(uid, developer.object_id, code, now + expires, interval, now + interval)
        return {"flow": handle, "user_code": display_code, "verification_uri": verification,
                "interval": interval, "expires_in": expires}

    def poll(self, uid, handle):
        flow = self.flows.get(handle)
        now = self.clock()
        if flow is None or flow.uid != uid:
            raise PermissionError("Unknown sign-in flow for this OS account.")
        self.policy.authorize(flow.object_id, uid, self.device)
        if flow.expires <= now:
            del self.flows[handle]
            raise PermissionError("Entra sign-in flow expired.")
        if now < flow.next_poll:
            return {"pending": True, "interval": max(1, int(flow.next_poll - now))}
        flow.next_poll = now + flow.interval
        response = self._post("/oauth2/v2.0/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.policy.client_id, "device_code": flow.device_code})
        error = response.get("error")
        if error in {"authorization_pending", "slow_down"}:
            if error == "slow_down":
                flow.interval = min(60, flow.interval + 5)
                flow.next_poll = now + flow.interval
            return {"pending": True, "interval": flow.interval}
        del self.flows[handle]  # Consume success/failure once; never replay an ID token.
        if error:
            raise PermissionError("Entra sign-in was denied or expired.")
        keys = self.transport.json("GET", self.authority + "/discovery/v2.0/keys")
        now = self.clock()
        if flow.expires <= now:
            raise PermissionError("Entra sign-in flow expired during verification.")
        claims = validate_id_token(response.get("id_token"), keys, self.policy.tenant_id, self.policy.client_id, now)
        if claims["oid"] != flow.object_id:
            raise PermissionError("Sign-in identity is not assigned to this OS account.")
        self.policy.authorize(claims["oid"], uid, self.device)
        return Identity(claims["oid"], uid, self.device, min(claims["exp"], now + 3600))
