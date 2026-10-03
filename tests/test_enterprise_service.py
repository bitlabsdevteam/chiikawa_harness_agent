"""Local management authorization, policy reloads, and kernel peer credentials."""

import copy
import os
import socket
import unittest
from unittest.mock import Mock, patch

from chiikawa.enterprise_identity import Identity
from chiikawa.enterprise_policy import EnterprisePolicy
from chiikawa.enterprise_service import ManagedAuthority, peer_uid
from test_enterprise_management import configuration, DEVICE, USER, CLIENT


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.raw = configuration()
        self.policy = EnterprisePolicy.parse(self.raw)
        mocks = [patch("chiikawa.enterprise_service.require_admin"),
                 patch("chiikawa.enterprise_service.device_fingerprint", return_value=DEVICE),
                 patch("chiikawa.enterprise_service.EnterprisePolicy.load", side_effect=lambda _: self.policy),
                 patch("chiikawa.enterprise_service.QuotaLedger")]
        for item in mocks:
            item.start()
            self.addCleanup(item.stop)
        self.now = 1000
        self.authority = ManagedAuthority("/managed/policy.json", "/managed/state", clock=lambda: self.now)
        self.authority.ledger.status.return_value = {"limit": None, "charged": 0}

    def signed_in(self):
        self.authority.identities["test-session"] = Identity(USER, 501, DEVICE, 2000)

    def request(self, operation="status", uid=501, arguments=None):
        return self.authority.dispatch(uid, {"operation": operation, "arguments": arguments or {}, "session": "test-session"})

    def test_peer_login_expiry_logout_and_forged_admin_requests(self):
        with self.assertRaises(PermissionError):
            self.request()
        self.signed_in()
        status = self.request()
        self.assertEqual((status["default_isolation"], status["network"], status["quota"]["limit"]),
                         ("jail", "offline", None))
        with self.assertRaises(PermissionError):
            self.request(uid=502)
        with self.assertRaises(ValueError):
            self.request(arguments={"uid": 501, "role": "super-admin"})
        with self.assertRaises(PermissionError):
            self.request(operation="set-limit")
        self.assertFalse(self.request(operation="logout")["authenticated"])
        with self.assertRaises(PermissionError):
            self.request()
        self.signed_in()
        self.now = 2000
        with self.assertRaises(PermissionError):
            self.request()

    def test_policy_revocation_device_change_and_limit_updates_apply_next_request(self):
        self.signed_in()
        self.raw["developers"][USER]["token_limit"] = 12
        self.policy = EnterprisePolicy.parse(self.raw)
        self.request()
        self.assertEqual(self.authority.ledger.status.call_args.args[0].token_limit, 12)
        self.raw["developers"][USER]["enabled"] = False
        self.policy = EnterprisePolicy.parse(self.raw)
        with self.assertRaises(PermissionError):
            self.request()
        self.raw["developers"][USER]["enabled"] = True
        self.policy = EnterprisePolicy.parse(self.raw)
        with patch("chiikawa.enterprise_service.device_fingerprint", return_value="a" * 64):
            with self.assertRaises(PermissionError):
                self.request()
        self.assertFalse(self.authority.identities)

    def test_tenant_change_invalidates_existing_sessions(self):
        self.signed_in()
        self.raw["tenant_id"] = CLIENT
        self.policy = EnterprisePolicy.parse(self.raw)
        with self.assertRaises(PermissionError):
            self.request()
        self.assertFalse(self.authority.identities)

    def test_revocation_while_signing_in_prevents_acceptance(self):
        def complete_login(uid, flow):
            self.raw["developers"][USER]["enabled"] = False
            self.policy = EnterprisePolicy.parse(self.raw)
            return Identity(USER, uid, DEVICE, 2000)
        self.authority.authentication.poll = complete_login
        with self.assertRaises(PermissionError):
            self.request(operation="poll", arguments={"flow": "test-flow"})
        self.assertFalse(self.authority.identities)

    def test_actual_socket_peer_uid_comes_from_kernel(self):
        first, second = socket.socketpair()
        try:
            self.assertEqual(peer_uid(first), os.getuid())
            self.assertEqual(peer_uid(second), os.getuid())
        finally:
            first.close()
            second.close()


if __name__ == "__main__":
    unittest.main()
