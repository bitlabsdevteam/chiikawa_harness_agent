"""Local hardware enrollment evidence, measured by the trusted host service."""

import hashlib
from pathlib import Path
import platform
import plistlib
import subprocess
import uuid


def device_fingerprint():
    """Hash the platform UUID without persisting or printing raw serial numbers.

    This identifier supports assignment, not remote attestation. The IT-owned
    service and managed OS prevent developers from substituting the measurement.
    Unsupported/missing identities fail closed rather than falling back to an
    environment variable or hostname that a developer could easily change.
    """
    system = platform.system()
    if system == "Darwin":
        result = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice", "-a"],
                                capture_output=True, check=True, timeout=10)
        value = plistlib.loads(result.stdout)[0].get("IOPlatformUUID")
    elif system == "Linux":
        value = Path("/sys/class/dmi/id/product_uuid").read_text().strip()
    else:
        raise RuntimeError("Managed device enrollment supports macOS and Linux with a platform UUID.")
    try:
        identifier = uuid.UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("No valid hardware UUID; IT device enrollment is required.") from exc
    if identifier.int in (0, (1 << 128) - 1):
        raise RuntimeError("Unusable hardware UUID; IT device enrollment is required.")
    return hashlib.sha256(f"chiikawa-device-v1:{system}:{identifier}".encode()).hexdigest()
