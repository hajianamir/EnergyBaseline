"""Probe actual OS protection across fresh processes; never substitute a fake keyring.

No passwords, keys, protected tokens or plaintext probes are emitted in evidence.
The default reports unavailable secure storage as a failed gate, not a successful
fallback. Use an isolated CI OS account/keyring: Linux may create the application's
master key in the real Secret Service. This proves application-process restart,
not machine reboot, locked desktop recovery or physical hardware certification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def worker(operation: str) -> int:
    from energybaseline.core.secrets import encrypt_text, decrypt_text, backend_status

    data = json.load(sys.stdin)
    try:
        if operation == "encrypt":
            result = {"ok": True, "token": encrypt_text(data["plain"]), "backend": backend_status()}
        else:
            decoded = decrypt_text(data["token"])
            result = {"ok": True, "digest": hashlib.sha256(decoded.encode()).hexdigest()}
    except Exception:
        result = {"ok": False}
    print(json.dumps(result))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=("encrypt", "decrypt"), help=argparse.SUPPRESS)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    if args.out is None:
        parser.error("--out is required")
    checks = {}
    with tempfile.TemporaryDirectory(prefix="eb_native_credentials_") as home:
        env = {**os.environ, "EB_HOME": home}
        env.pop("EB_ALLOW_INSECURE_KEYFILE", None)

        def child(operation, data, child_env=env):
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker", operation],
                input=json.dumps(data),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env,
                cwd=ROOT,
                timeout=30,
                check=True,
            )
            return json.loads(result.stdout)

        # A random probe is not a real password. Nothing containing it or its
        # encrypted token is retained in the output artifact or command line.
        plain = "native-credential-probe-" + os.urandom(32).hex()
        digest = hashlib.sha256(plain.encode()).hexdigest()
        encrypted = child("encrypt", {"plain": plain})
        backend = encrypted.get("backend", {})
        checks["actual_secure_os_backend"] = bool(backend.get("secure_store") and backend.get("persistent"))
        if encrypted.get("ok"):
            token = encrypted["token"]
            decoded = child("decrypt", {"token": token})
            checks["fresh_process_recovers_same_plaintext"] = decoded.get("digest") == digest
            position = token.index(":") + 1 + (len(token) - token.index(":") - 1) // 2
            tampered = token[:position] + ("A" if token[position] != "A" else "B") + token[position + 1 :]
            checks["tampered_ciphertext_is_rejected"] = not child("decrypt", {"token": tampered}).get("ok")
        else:
            checks["fresh_process_recovers_same_plaintext"] = False
            checks["tampered_ciphertext_is_rejected"] = False
        checks["no_plaintext_keyfile_created"] = not any(Path(home).rglob(".eb_master.key"))
        failure = {
            "executed": False,
            "reason": "Native DPAPI tamper rejection is exercised above; service outage is Linux-only.",
        }
        if sys.platform.startswith("linux"):
            disconnected = {
                **env,
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent-eb-test-only-bus",
                "PYTHON_KEYRING_BACKEND": "keyring.backends.SecretService.Keyring",
            }
            fallback = child("encrypt", {"plain": plain}, disconnected)
            fallback_backend = fallback.get("backend", {})
            expired = child("decrypt", {"token": fallback.get("token", "")}, disconnected)
            failure = {
                "executed": True,
                "disconnected_os_service_does_not_claim_persistence": not fallback_backend.get("persistent", True),
                "disconnected_os_service_uses_session_only": fallback.get("token", "").startswith("session-fernet:"),
                "fallback_expires_across_process_restart": not expired.get("ok", True),
            }
            checks["actual_service_outage_fails_closed"] = all(v for k, v in failure.items() if k != "executed")
    payload = {
        "evidence_schema_version": 1,
        "platform": sys.platform,
        "native": True,
        "mocked": False,
        "backend": backend,
        "checks": checks,
        "service_failure": failure,
        "passed": all(checks.values()),
        "scope": "Actual OS backend, process restart and ciphertext tamper; not reboot or physical desktop evidence.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
