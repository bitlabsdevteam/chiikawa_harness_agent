"""IT-only policy administration: python -m chiikawa.enterprise_admin --help.

Run from an IT-managed installation, never via sudo on developer-owned source.
No model/tool route invokes these commands. Root is the local OS trust anchor.
"""

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import uuid

from .enterprise_device import device_fingerprint
from .enterprise_policy import ADMIN_UID, POLICY_PATH, EnterprisePolicy, trusted_path, _duplicates


def require_admin():
    if os.geteuid() != ADMIN_UID:
        raise PermissionError("Only company IT administrators may modify managed enterprise policy.")


def write_policy(path, raw):
    """Validate completely, then atomically replace a file in an IT-owned directory."""
    require_admin()
    EnterprisePolicy.parse(raw)
    path = Path(path).absolute()
    trusted_path(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        trusted_path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".policy-", delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o644)
            output.write((json.dumps(raw, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=POLICY_PATH, help="IT-owned canonical policy path")
    parser.add_argument("--state", type=Path, default=Path("/var/lib").resolve() / "chiikawa", help="Private IT-managed state directory")
    commands = parser.add_subparsers(dest="command", required=True)
    initialize = commands.add_parser("initialize", help="Create offline policy with no enrolled developers/providers")
    initialize.add_argument("--tenant-id", required=True)
    initialize.add_argument("--client-id", required=True)
    commands.add_parser("device", help="Show the local enrollment fingerprint, not raw hardware identifiers")
    commands.add_parser("validate", help="Validate ownership, schema, and assignments")
    commands.add_parser("provision-state", help="Create the private local management state directory")
    secret = commands.add_parser("set-secret", help="Store a provider credential privately; never pass keys as arguments")
    secret.add_argument("--name", required=True)
    secret.add_argument("--stdin", action="store_true", help="Read a credential from a trusted pipe instead of prompting")
    install = commands.add_parser("install-policy", help="Validate and install IT-reviewed provider/network policy")
    install.add_argument("source", type=Path)
    assign = commands.add_parser("assign", help="Assign an Entra object ID and OS UID to this machine")
    assign.add_argument("--object-id", required=True)
    assign.add_argument("--uid", type=int, required=True)
    disable = commands.add_parser("disable", help="Revoke a developer's local execution")
    disable.add_argument("--object-id", required=True)
    limit = commands.add_parser("set-limit", help="Set a finite token grant or unlimited")
    limit.add_argument("--object-id", required=True)
    limit.add_argument("--tokens", required=True, help="Nonnegative integer or unlimited")
    limit.add_argument("--reset-grant", action="store_true", help="Issue a new grant instead of preserving prior consumption")
    args = parser.parse_args(argv)
    try:
        require_admin()  # Before reading source/configuration or measuring hardware.
        if args.command == "provision-state":
            trusted_path(args.state.parent, directory=True)
            args.state.mkdir(mode=0o700, exist_ok=True)
            trusted_path(args.state, directory=True, private=True)
            print("Private management state directory is ready.")
            return 0
        if args.command == "set-secret":
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", args.name):
                raise ValueError("Invalid managed credential name.")
            trusted_path(args.state, directory=True, private=True)
            directory = args.state / "secrets"
            directory.mkdir(mode=0o700, exist_ok=True)
            trusted_path(directory, directory=True, private=True)
            path = directory / args.name
            if path.exists() or path.is_symlink():
                trusted_path(path, private=True)
            if not args.stdin and not sys.stdin.isatty():
                raise ValueError("Use --stdin explicitly for a trusted noninteractive credential pipe.")
            value = sys.stdin.read(16385).strip() if args.stdin else getpass.getpass("Provider credential: ")
            if not value or len(value) > 16384 or any(c.isspace() for c in value):
                raise ValueError("Invalid provider credential.")
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".secret-", delete=False) as output:
                    temporary = Path(output.name)
                    os.fchmod(output.fileno(), 0o600)
                    output.write(value.encode())
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                temporary = None
                descriptor = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            print("Managed provider credential stored.")
            return 0
        if args.command == "device":
            print(device_fingerprint())
            return 0
        if args.command == "initialize":
            if args.policy.exists() or args.policy.is_symlink():
                raise FileExistsError("Managed policy already exists; use install-policy for a reviewed replacement.")
            trusted_path(args.policy.parent.parent, directory=True)
            args.policy.parent.mkdir(mode=0o755, exist_ok=True)
            raw = {"version": 1, "tenant_id": args.tenant_id, "client_id": args.client_id,
                   "developers": {}, "providers": {}, "network": {"allow": []}}
        elif args.command == "install-policy":
            raw = json.loads(args.source.read_bytes(), object_pairs_hook=_duplicates)
        else:
            managed = EnterprisePolicy.load(args.policy)
            if args.command == "validate":
                print(f"Valid managed policy: {managed.fingerprint}")
                return 0
            raw = json.loads(args.policy.read_bytes(), object_pairs_hook=_duplicates)
            if args.command == "assign":
                previous = raw["developers"].get(args.object_id, {})
                raw["developers"][args.object_id] = {
                    "uid": args.uid, "device": device_fingerprint(), "enabled": True,
                    "token_limit": previous.get("token_limit"), "grant": previous.get("grant", "initial")}
            else:
                entry = raw["developers"].get(args.object_id)
                if entry is None:
                    raise ValueError("Developer is not enrolled.")
                if args.command == "disable":
                    entry["enabled"] = False
                elif args.command == "set-limit":
                    entry["token_limit"] = None if args.tokens == "unlimited" else int(args.tokens)
                    if args.reset_grant:
                        entry["grant"] = uuid.uuid4().hex
        write_policy(args.policy, raw)
        print("Managed enterprise policy updated. No provider or internet access is implicitly granted.")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Enterprise administration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
