"""Managed-policy and quota primitives; these do not claim full CLI enforcement."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from chiikawa import enterprise_policy as policy
from chiikawa.enterprise_quota import QuotaExceeded, QuotaLedger
from chiikawa.enterprise_transport import ApprovedHTTPS


TENANT = "11111111-1111-4111-8111-111111111111"
CLIENT = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
DEVICE = "d" * 64


def configuration():
    return {"version": 1, "tenant_id": TENANT, "client_id": CLIENT,
            "developers": {USER: {"uid": 501, "device": DEVICE, "enabled": True}},
            "providers": {}, "network": {"allow": []}}


class ManagedPolicyTests(unittest.TestCase):
    def test_offline_jail_unlimited_defaults_and_immutable_policy(self):
        managed = policy.EnterprisePolicy.parse(configuration())
        developer = managed.authorize(USER, 501, DEVICE)
        self.assertIsNone(developer.token_limit)
        self.assertEqual(managed.default_isolation, "jail")
        with self.assertRaises(PermissionError):
            managed.allow_url("https://api.openai.com/v1/responses")
        with self.assertRaises(TypeError):
            managed.developers[USER] = developer
        with self.assertRaises(AttributeError):
            managed.default_isolation = "sandbox"

    def test_identity_device_and_revocation(self):
        raw = configuration()
        managed = policy.EnterprisePolicy.parse(raw)
        for identity, uid, device in ((CLIENT, 501, DEVICE), (USER, 502, DEVICE), (USER, 501, "a" * 64)):
            with self.subTest(identity=identity, uid=uid, device=device), self.assertRaises(PermissionError):
                managed.authorize(identity, uid, device)
        raw["developers"][USER]["enabled"] = False
        with self.assertRaises(PermissionError):
            policy.EnterprisePolicy.parse(raw).authorize(USER, 501, DEVICE)

    def test_reject_duplicate_assignments_unknown_fields_and_invalid_limits(self):
        for change in (
            lambda raw: raw.update(admin=True),
            lambda raw: raw["developers"][USER].update(uid=0),
            lambda raw: raw["developers"][USER].update(token_limit=True),
            lambda raw: raw["developers"][USER].update(token_limit=-1),
            lambda raw: raw["developers"][USER].update(device=[DEVICE]),
            lambda raw: raw["developers"].update({CLIENT: {"uid": 502, "device": DEVICE, "enabled": True}}),
            lambda raw: raw["developers"].update({CLIENT: {"uid": 501, "device": "a" * 64, "enabled": True}}),
        ):
            raw = configuration()
            change(raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                policy.EnterprisePolicy.parse(raw)

    def test_exact_origin_path_and_ip_rules(self):
        raw = configuration()
        raw["network"]["allow"] = ["https://api.example.test/v1/", "192.0.2.10", "https://example.test:8443/exact"]
        managed = policy.EnterprisePolicy.parse(raw)
        for url in ("https://api.example.test/v1/responses", "https://192.0.2.10/", "https://example.test:8443/exact"):
            managed.allow_url(url)
        for url in ("https://api.example.test/other", "https://sub.api.example.test/v1/",
                    "https://api.example.test.evil.test/v1/", "https://192.0.2.11/",
                    "https://example.test/exact", "https://example.test:8443/exact/child"):
            with self.subTest(url=url), self.assertRaises(PermissionError):
                managed.allow_url(url)
        for url in ("http://example.test", "https://example.test@evil.test", "https://*.example.test/",
                    "https://example.test/v1/../x", "https://example.test/%2fsecret", "https://example.test/?secret=x",
                    "https://example.test/#fragment", "https://example.test./", "https://example.test:0/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                policy.Destination.parse(url)

    def test_provider_and_network_approval_are_separate(self):
        raw = configuration()
        raw["providers"]["openai"] = {"protocol": "responses", "endpoint": "https://api.openai.com/v1/responses",
                                      "models": ["approved-model"], "credential": "openai", "max_output_tokens": 4096}
        with self.assertRaises(PermissionError):
            policy.EnterprisePolicy.parse(raw).allow_model("openai", "approved-model")
        raw["network"]["allow"] = ["https://api.openai.com/v1/responses"]
        managed = policy.EnterprisePolicy.parse(raw)
        self.assertEqual(managed.allow_model("openai", "approved-model").max_output_tokens, 4096)
        with self.assertRaises(PermissionError):
            managed.allow_model("openai", "unapproved-model")
        with self.assertRaises(PermissionError):
            managed.allow_model("google", "approved-model")

    def test_load_rejects_untrusted_file_symlink_and_duplicate_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "policy.json"
            target.write_text(json.dumps(configuration()))
            with self.assertRaises(PermissionError):
                policy.EnterprisePolicy.load(target)
            # Isolate loader checks from root-ownership checks covered above.
            with patch.object(policy, "trusted_path", side_effect=lambda p: Path(p)), \
                    patch.object(policy, "ADMIN_UID", os.getuid()):
                self.assertEqual(policy.EnterprisePolicy.load(target).tenant_id, TENANT)
                linked = Path(tmp) / "link.json"
                linked.symlink_to(target)
                with self.assertRaises(OSError):
                    policy.EnterprisePolicy.load(linked)
                target.write_text('{"version":1,"version":1}')
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    policy.EnterprisePolicy.load(target)
                target.chmod(0o666)
                with self.assertRaises(PermissionError):
                    policy.EnterprisePolicy.load(target)


class QuotaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "usage.sqlite3"
        # Tests exercise real SQLite transactions but do not impersonate an IT
        # service in production paths. Production refuses a developer-owned DB.
        for target, value in (("chiikawa.enterprise_quota.ADMIN_UID", os.geteuid()),
                              ("chiikawa.enterprise_quota.trusted_path", lambda p, **kw: Path(p))):
            stub = patch(target, value)
            stub.start()
            self.addCleanup(stub.stop)
        self.developer = policy.Developer(USER, 501, DEVICE, True, 100, "grant-1")
        self.ledger = QuotaLedger(self.path)

    def test_reserve_before_request_and_reconcile_once(self):
        reservation = self.ledger.reserve(self.developer, 50, 30)
        with self.assertRaises(QuotaExceeded):
            self.ledger.reserve(self.developer, 20, 1)
        self.ledger.settle(reservation, 30, 10)
        self.ledger.settle(reservation, 30, 10)
        with self.assertRaises(ValueError):
            self.ledger.settle(reservation, 0, 0)
        self.assertEqual(self.ledger.status(self.developer)["remaining"], 60)
        with self.assertRaises(ValueError):
            self.ledger.reserve(self.developer, True, 30)

    def test_concurrent_connections_do_not_overspend(self):
        def attempt(_):
            try:
                QuotaLedger(self.path).reserve(self.developer, 20, 30)
                return True
            except QuotaExceeded:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt, range(12))), 2)
        self.assertEqual(self.ledger.status(self.developer)["charged"], 100)

    def test_crash_unknown_usage_new_sessions_and_retries_keep_charge(self):
        reservation = self.ledger.reserve(self.developer, 50, 50)
        reloaded = QuotaLedger(self.path)
        with self.assertRaises(ValueError):
            reloaded.settle(reservation, None, None)
        with self.assertRaises(QuotaExceeded):
            reloaded.reserve(self.developer, 1, 1)
        self.assertEqual(reloaded.status(self.developer)["pending_requests"], 1)

    def test_unlimited_default_limit_changes_and_explicit_reset(self):
        unlimited = replace(self.developer, token_limit=None)
        self.ledger.reserve(unlimited, 100, 100)
        with self.assertRaises(QuotaExceeded):
            self.ledger.reserve(self.developer, 1, 1)
        reset = replace(self.developer, grant="grant-2")
        self.assertEqual(self.ledger.status(reset)["charged"], 0)
        self.ledger.reserve(reset, 50, 50)
        self.assertEqual(self.ledger.status(self.developer)["charged"], 200)

    def test_provider_overrun_is_durable_and_suspends_grant(self):
        reservation = self.ledger.reserve(self.developer, 20, 20)
        with self.assertRaises(QuotaExceeded):
            self.ledger.settle(reservation, 35, 10)
        self.assertEqual(self.ledger.status(self.developer)["charged"], 45)
        with self.assertRaises(QuotaExceeded):
            self.ledger.reserve(self.developer, 1, 1)


class TransportTests(unittest.TestCase):
    def setUp(self):
        raw = configuration()
        raw["network"]["allow"] = ["https://allowed.example.test/v1/"]
        self.transport = ApprovedHTTPS(policy.EnterprisePolicy.parse(raw))

    def test_denied_destinations_do_not_resolve_or_connect(self):
        with patch("chiikawa.enterprise_transport.socket.getaddrinfo") as dns, \
                patch("chiikawa.enterprise_transport._PinnedHTTPS") as connection:
            with self.assertRaises(PermissionError):
                self.transport.json("POST", "https://denied.example.test/", {"code": "SYNTHETIC"})
            dns.assert_not_called()
            connection.assert_not_called()

    def test_pinned_destination_redirect_denial_no_proxy_or_retry(self):
        with patch("chiikawa.enterprise_transport.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.8", 443))]), \
                patch("chiikawa.enterprise_transport._PinnedHTTPS") as connection, \
                patch.dict(os.environ, {"HTTPS_PROXY": "http://unapproved.example.test"}):
            response = connection.return_value.getresponse.return_value
            response.status = 307
            with self.assertRaises(PermissionError):
                self.transport.json("POST", "https://allowed.example.test/v1/request", {"code": "SYNTHETIC"})
            self.assertEqual(connection.call_args.args, ("allowed.example.test", 443, "192.0.2.8"))
            self.assertEqual(connection.return_value.request.call_count, 1)
            connection.return_value.close.assert_called_once()
            response.read.assert_not_called()

    def test_error_body_not_disclosed_and_response_bounded(self):
        with patch("chiikawa.enterprise_transport.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.0.2.8", 443))]), \
                patch("chiikawa.enterprise_transport._PinnedHTTPS") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 403
            response.read.return_value = b"SYNTHETIC_SECRET"
            with self.assertRaisesRegex(RuntimeError, "HTTP 403") as error:
                self.transport.json("GET", "https://allowed.example.test/v1/")
            self.assertNotIn("SYNTHETIC_SECRET", str(error.exception))
            response.read.assert_not_called()
            response.status = 200
            self.transport.max_response_bytes = 2
            with self.assertRaisesRegex(RuntimeError, "size limit"):
                self.transport.json("GET", "https://allowed.example.test/v1/")


if __name__ == "__main__":
    unittest.main()
