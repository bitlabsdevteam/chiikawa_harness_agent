"""Entra signature validation and device-flow ownership with synthetic identities."""

import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

from chiikawa import enterprise_admin as admin, enterprise_device as device
from chiikawa.enterprise_identity import EntraAuthentication, Identity, validate_id_token
from chiikawa.enterprise_policy import EnterprisePolicy
from test_enterprise_management import CLIENT, DEVICE, TENANT, USER, configuration

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa
except ImportError:
    rsa = None


def encoded(value):
    if isinstance(value, dict):
        value = json.dumps(value).encode()
    if isinstance(value, int):
        value = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@unittest.skipIf(rsa is None, "Install the optional enterprise dependency for Entra signature tests")
class EntraTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = cls.key.public_key().public_numbers()
        cls.keys = {"keys": [{"kid": "test-key", "kty": "RSA", "use": "sig",
                              "n": encoded(public.n), "e": encoded(public.e)}]}

    def setUp(self):
        self.claims = {"oid": USER, "tid": TENANT, "aud": CLIENT,
                       "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                       "iat": 1000, "nbf": 1000, "exp": 4600}
        self.now = 1000
        self.transport = Mock()
        self.transport.request.return_value = json.dumps({
            "device_code": "PRIVATE_CODE", "user_code": "VISIBLE-CODE", "interval": 5, "expires_in": 900,
            "verification_uri": "https://microsoft.com/devicelogin"}).encode()
        self.transport.json.return_value = self.keys
        self.auth = EntraAuthentication(EnterprisePolicy.parse(configuration()), DEVICE,
                                        clock=lambda: self.now, transport=self.transport)

    def token(self, claims=None, header=None):
        message = encoded(header or {"alg": "RS256", "kid": "test-key"}) + "." + encoded(claims or self.claims)
        signature = self.key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return message + "." + encoded(signature)

    def test_real_signature_and_all_identity_boundaries(self):
        self.assertEqual(validate_id_token(self.token(), self.keys, TENANT, CLIENT, self.now)["oid"], USER)
        for updates in ({"aud": USER}, {"tid": USER}, {"iss": "https://attacker.test"},
                        {"exp": 1000}, {"nbf": 2000}, {"iat": True}, {"exp": "4600"}):
            claims = {**self.claims, **updates}
            with self.subTest(updates=updates), self.assertRaises(PermissionError):
                validate_id_token(self.token(claims), self.keys, TENANT, CLIENT, self.now)
        for header in ({"alg": "none", "kid": "test-key"}, {"alg": "HS256", "kid": "test-key"},
                       {"alg": "RS256", "kid": "test-key", "jku": "https://attacker.test"}):
            with self.subTest(header=header), self.assertRaises(PermissionError):
                validate_id_token(self.token(header=header), self.keys, TENANT, CLIENT, self.now)
        token = self.token().split(".")
        token[1] = encoded({**self.claims, "oid": CLIENT})
        with self.assertRaises(PermissionError):
            validate_id_token(".".join(token), self.keys, TENANT, CLIENT, self.now)

    def test_flow_bound_to_enrolled_os_peer_and_device(self):
        with self.assertRaises(PermissionError):
            self.auth.begin(502)
        self.transport.request.assert_not_called()
        self.auth.device = "b" * 64
        with self.assertRaises(PermissionError):
            self.auth.begin(501)
        self.transport.request.assert_not_called()
        self.auth.device = DEVICE
        flow = self.auth.begin(501)
        self.assertNotIn("PRIVATE_CODE", str(flow))
        with self.assertRaises(PermissionError):
            self.auth.poll(502, flow["flow"])
        self.assertTrue(self.auth.poll(501, flow["flow"])["pending"])
        self.assertEqual(self.transport.request.call_count, 1)
        self.now += 5
        self.transport.request.return_value = json.dumps({"id_token": self.token()}).encode()
        identity = self.auth.poll(501, flow["flow"])
        self.assertEqual(identity, Identity(USER, 501, DEVICE, 4600))
        with self.assertRaises(PermissionError):
            self.auth.poll(501, flow["flow"])

    def test_pending_wrong_account_revocation_and_expiration(self):
        flow = self.auth.begin(501)["flow"]
        self.now += 5
        self.transport.request.return_value = b'{"error":"slow_down"}'
        self.assertEqual(self.auth.poll(501, flow)["interval"], 10)
        self.now += 10
        self.transport.request.return_value = json.dumps({"id_token": self.token({**self.claims, "oid": CLIENT})}).encode()
        with self.assertRaises(PermissionError):
            self.auth.poll(501, flow)
        self.assertNotIn(flow, self.auth.flows)
        self.setUp()
        flow = self.auth.begin(501)["flow"]
        raw = configuration()
        raw["developers"][USER]["enabled"] = False
        self.auth.policy = EnterprisePolicy.parse(raw)
        with self.assertRaises(PermissionError):
            self.auth.poll(501, flow)
        self.setUp()
        flow = self.auth.begin(501)["flow"]
        self.now += 901
        with self.assertRaisesRegex(PermissionError, "expired"):
            self.auth.poll(501, flow)


class AdminTests(unittest.TestCase):
    def test_developer_cannot_administer_or_measure_privileged_device(self):
        for command in (["device"], ["initialize", "--tenant-id", TENANT, "--client-id", CLIENT],
                        ["install-policy", "/arbitrary/source"], ["set-limit", "--object-id", USER, "--tokens", "unlimited"]):
            with self.subTest(command=command), patch.object(admin.os, "geteuid", return_value=501), \
                    patch.object(admin, "device_fingerprint") as measured, \
                    patch.object(admin, "write_policy") as write, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(admin.main(command), 1)
                measured.assert_not_called()
                write.assert_not_called()

    def test_invalid_policy_does_not_replace_existing_and_updates_are_atomic(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(admin, "require_admin"), \
                patch.object(admin, "trusted_path", side_effect=lambda p, **kw: Path(p)):
            target = Path(tmp) / "enterprise.json"
            original = configuration()
            admin.write_policy(target, original)
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                admin.write_policy(target, {**original, "unexpected": True})
            self.assertEqual(target.read_bytes(), before)
            changed = copy.deepcopy(original)
            changed["developers"][USER]["token_limit"] = 1000
            admin.write_policy(target, changed)
            self.assertEqual(json.loads(target.read_bytes())["developers"][USER]["token_limit"], 1000)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse(list(Path(tmp).glob(".policy-*")))

    def test_device_measurement_has_no_environment_or_hostname_fallback(self):
        with patch.object(device.platform, "system", return_value="Darwin"), \
                patch.object(device.subprocess, "run") as command:
            command.return_value.stdout = plistlib.dumps([{"IOPlatformUUID": CLIENT}])
            fingerprint = device.device_fingerprint()
            self.assertEqual(len(fingerprint), 64)
            self.assertNotIn(CLIENT, fingerprint)
            command.return_value.stdout = plistlib.dumps([{"IOPlatformUUID": "00000000-0000-0000-0000-000000000000"}])
            with self.assertRaises(RuntimeError):
                device.device_fingerprint()
        with patch.object(device.platform, "system", return_value="Unsupported"):
            with self.assertRaises(RuntimeError):
                device.device_fingerprint()


if __name__ == "__main__":
    unittest.main()
